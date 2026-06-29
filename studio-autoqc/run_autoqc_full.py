#!/usr/bin/env python3
"""Full semantic-QC run: fire the REVIEWER AutoQC module over the needs-review set.

Reviewer (agentic opus judge) is the actionable module (12% FAIL yield on the 250
sample). Adversary is excluded — it NEUTRAL-flags ~91% of tasks (noise; runtime is
its real precision gate), so labeling off it is not useful.

Design for a long background run over ~9,438 tasks:
  - TRIGGER phase: POST /qc-audits/ per task, fire-and-forget. The audit persists
    server-side in RLS, so we don't hold a 27-min in-memory poll. Resumable via
    triggered.txt (skip already-fired).
  - COLLECT phase (--collect): GET /qc-audits/ per task, append verdict to
    results.jsonl. Resumable via the ids already in results.jsonl. Re-run until
    complete.
  - REPORT phase (--report): summarize FAIL rate + dims from results.jsonl.

Usage:
  python run_autoqc_full.py --ids-file _local/behavioral_all/autoqc_target_ids.txt   # trigger
  python run_autoqc_full.py --collect                                                # poll/collect
  python run_autoqc_full.py --report                                                 # summarize
"""
import argparse
import collections
import json
import os
import sys
import time
import requests

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
API = "https://api.studio.mercor.com"
REVIEWER = "qcspec_7bddfd703a12994dbc31fd1b"
OUT = f"{ROOT}/_local/autoqc_full"


def key():
    for line in open(f"{ROOT}/.env"):
        if line.startswith("RLS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("no RLS_KEY")


H = {"Authorization": f"Bearer {key()}", "X-Campaign-Id": "camp_4e196b1414a1499db54b43233104b0a7",
     "X-Company-Id": "comp_2fa4115109d741cd94a3c409ed89e61f",
     "X-Account-Id": "acct_85b680d4c5ba49a29f19c173672aebea",
     "User-Agent": "curl/8.7.1", "Content-Type": "application/json"}


def verdict(outcome):
    gp, dims = None, []
    if isinstance(outcome, dict):
        gp = outcome.get("global_pass")
        for sec in (outcome.get("sections") or []):
            for d in (sec.get("dimensions") or []):
                dims.append(((d.get("dimension") or d.get("name") or d.get("key") or ""),
                             str(d.get("status", "")).lower()))
    return gp, dims


def trigger(ids):
    os.makedirs(OUT, exist_ok=True)
    log = f"{OUT}/triggered.txt"
    done = set(l.strip() for l in open(log)) if os.path.exists(log) else set()
    todo = [t for t in ids if t not in done]
    print(f"trigger: {len(done)} already fired, {len(todo)} to go", flush=True)
    with open(log, "a") as f:
        for i, tid in enumerate(todo):
            try:
                requests.post(f"{API}/qc-audits/", headers=H, timeout=60,
                              data=json.dumps({"qc_spec_id": REVIEWER, "subject_kind": "task",
                                               "subject_id": tid, "source": "automatic",
                                               "subject_params": None}))
                f.write(tid + "\n"); f.flush()
            except Exception as e:
                print(f"  trigger fail {tid}: {e}", flush=True)
                continue
            if (i + 1) % 200 == 0:
                print(f"  triggered {i+1}/{len(todo)}", flush=True)
            time.sleep(0.12)
    print(f"trigger done: {len(done)+len(todo)} total fired", flush=True)


def collect(ids):
    os.makedirs(OUT, exist_ok=True)
    res = f"{OUT}/results.jsonl"
    have = set()
    if os.path.exists(res):
        for l in open(res):
            try:
                have.add(json.loads(l)["task_id"])
            except Exception:
                pass
    todo = [t for t in ids if t not in have]
    print(f"collect: {len(have)} collected, {len(todo)} pending", flush=True)
    got = 0
    with open(res, "a") as f:
        for i, tid in enumerate(todo):
            try:
                d = requests.get(f"{API}/qc-audits/", headers=H, timeout=45,
                                 params={"subject_kind": "task", "subject_id": tid,
                                         "qc_spec_id": REVIEWER}).json()
            except Exception:
                continue
            rows = d.get("audits", d if isinstance(d, list) else [])
            row = rows[0] if rows else None
            st = (row or {}).get("status")
            if st and st not in ("pending", "queued", "running", "in_progress"):
                gp, dims = verdict(row.get("outcome") or row.get("result") or {})
                f.write(json.dumps({"task_id": tid, "status": st, "global_pass": gp, "dims": dims}) + "\n")
                f.flush(); got += 1
            if (i + 1) % 200 == 0:
                print(f"  scanned {i+1}/{len(todo)}, new {got}", flush=True)
            time.sleep(0.05)
    print(f"collect pass done: +{got} (total {len(have)+got}); re-run --collect for stragglers", flush=True)


def report():
    res = f"{OUT}/results.jsonl"
    rows = [json.loads(l) for l in open(res)] if os.path.exists(res) else []
    fail = collections.Counter()
    fail_tasks = 0
    for r in rows:
        fails = [dn for dn, s in r["dims"] if s == "fail"]
        if fails:
            fail_tasks += 1
            fail.update(fails)
    n = len(rows)
    print(f"REVIEWER complete: {n} tasks")
    print(f"  FAIL: {fail_tasks}/{n} ({100*fail_tasks/max(1,n):.1f}%)")
    print(f"  top fail dims: {json.dumps(dict(fail.most_common(10)), indent=1)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids-file", default=f"{ROOT}/_local/behavioral_all/autoqc_target_ids.txt")
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    ids = [l.strip() for l in open(args.ids_file) if l.strip()]
    if args.report:
        report()
    elif args.collect:
        collect(ids)
    else:
        trigger(ids)


if __name__ == "__main__":
    main()
