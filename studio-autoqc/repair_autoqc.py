#!/usr/bin/env python3
"""Backfill rate-limit-dropped AutoQC audits — ONE process, governed under the cap.

LESSON LEARNED: the API enforces ~10,000 requests/HOUR counting ALL verbs (GET+POST),
rolling. Running a refire loop AND a collect loop concurrently with no global governor
did thousands of GETs/hr and kept the quota permanently maxed (every POST -> 429).

This is a SINGLE-THREADED, request-governed rewrite. Do NOT run a second copy.

Per uncollected id, one economical pass does:
  GET the audit (1 req) -> completed? save result.  NO-ROW? POST it (1 req).  pending? leave.
Loop passes until everything is collected. Every request goes through a sliding-window
governor capped at MAXREQ/hour so we never re-trip the limiter. A stray 429 -> back off.

Run via night_run.sh (caffeinate + restart-on-crash). Resumable: results.jsonl is truth.
"""
import json
import os
import sys
import time
import collections
import requests

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
API = "https://api.studio.mercor.com"
MAXREQ = 9000          # requests/hour ceiling (margin under the 10k cap)
WINDOW = 3600.0
PASS_SLEEP = 45        # between full passes
BACKOFF = 600          # on an unexpected 429

RUNS = [
    {"name": "fn", "spec": "qcspec_ece2ca798fd2580188abd82c", "kind": "trajectory",
     "ids": f"{ROOT}/_local/traj_audit/fn_all.txt", "out": f"{ROOT}/_local/traj_audit/fn_all_run", "idkey": "trajectory_id", "target": 4897},
    {"name": "reviewer", "spec": "qcspec_7bddfd703a12994dbc31fd1b", "kind": "task",
     "ids": f"{ROOT}/_local/behavioral_all/autoqc_target_ids.txt", "out": f"{ROOT}/_local/autoqc_full", "idkey": "task_id", "target": 9438},
    {"name": "fp", "spec": "qcspec_ece2ca798fd2580188abd82c", "kind": "trajectory",
     "ids": f"{ROOT}/_local/traj_audit/fp_all.txt", "out": f"{ROOT}/_local/traj_audit/fp_all_run", "idkey": "trajectory_id", "target": 1834},
]


def key():
    for line in open(f"{ROOT}/.env"):
        if line.startswith("RLS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("no RLS_KEY")


H = {"Authorization": f"Bearer {key()}", "X-Campaign-Id": "camp_4e196b1414a1499db54b43233104b0a7",
     "X-Company-Id": "comp_2fa4115109d741cd94a3c409ed89e61f",
     "X-Account-Id": "acct_85b680d4c5ba49a29f19c173672aebea",
     "User-Agent": "curl/8.7.1", "Content-Type": "application/json"}

_req_times = collections.deque()


def govern():
    """Block until making one more request keeps us under MAXREQ in the trailing hour."""
    now = time.time()
    while _req_times and now - _req_times[0] > WINDOW:
        _req_times.popleft()
    if len(_req_times) >= MAXREQ:
        sleep_for = WINDOW - (now - _req_times[0]) + 1
        print(f"  [governor] hourly budget full ({len(_req_times)}), sleeping {int(sleep_for)}s", flush=True)
        time.sleep(max(1, sleep_for))
    _req_times.append(time.time())


def get_audit(run, sid):
    govern()
    try:
        r = requests.get(f"{API}/qc-audits/", headers=H, timeout=40,
                         params={"subject_kind": run["kind"], "subject_id": sid, "qc_spec_id": run["spec"]})
    except Exception:
        return "ERR", None
    if r.status_code == 429:
        return "RATE", None          # window maxed — must NOT be read as NO-ROW
    if r.status_code >= 300:
        return "ERR", None
    d = r.json()
    rows = d.get("audits", d if isinstance(d, list) else [])
    row = rows[0] if rows else None
    return ((row.get("status") if row else None), row)


def post_audit(run, sid):
    govern()
    r = requests.post(f"{API}/qc-audits/", headers=H, timeout=60,
                      data=json.dumps({"qc_spec_id": run["spec"], "subject_kind": run["kind"],
                                       "subject_id": sid, "source": "automatic", "subject_params": None}))
    return r.status_code


def verdict(row):
    o = (row or {}).get("outcome") or (row or {}).get("result") or {}
    gp = o.get("global_pass") if isinstance(o, dict) else None
    dims = []
    if isinstance(o, dict):
        for sec in (o.get("sections") or []):
            for d in (sec.get("dimensions") or []):
                dims.append(((d.get("dimension") or d.get("name") or ""), str(d.get("status", "")).lower()))
    return gp, dims


def collected(run):
    s = set()
    p = f"{run['out']}/results.jsonl"
    if os.path.exists(p):
        for l in open(p):
            try:
                s.add(json.loads(l)[run["idkey"]])
            except Exception:
                pass
    return s


def main():
    ids = {r["name"]: [l.strip() for l in open(r["ids"]) if l.strip()] for r in RUNS}
    while True:
        all_done = True
        for run in RUNS:
            have = collected(run)
            todo = [t for t in ids[run["name"]] if t not in have]
            if not todo:
                continue
            all_done = False
            done = posted = 0
            with open(f"{run['out']}/results.jsonl", "a") as f:
                for sid in todo:
                    st, row = get_audit(run, sid)
                    if st == "RATE":                      # window maxed — wait for it to drain
                        print(f"  [{run['name']}] GET 429 -> backoff {BACKOFF}s", flush=True)
                        time.sleep(BACKOFF)
                    elif st and st not in ("pending", "queued", "running", "in_progress", "ERR"):
                        gp, dims = verdict(row)
                        f.write(json.dumps({run["idkey"]: sid, "status": st, "global_pass": gp, "dims": dims}) + "\n")
                        f.flush(); done += 1
                    elif st is None:                      # NO-ROW: dropped -> refire
                        code = post_audit(run, sid)
                        if code == 429:
                            print(f"  [{run['name']}] POST 429 -> backoff {BACKOFF}s", flush=True)
                            time.sleep(BACKOFF)
                        else:
                            posted += 1
            print(f"[{run['name']}] pass: collected+{done} posted+{posted} (total {len(have)+done}/{run['target']})", flush=True)
        if all_done:
            print("ALL COMPLETE", flush=True)
            return
        time.sleep(PASS_SLEEP)


if __name__ == "__main__":
    main()
