#!/usr/bin/env python3
"""Re-audit a few representative trajectories with the retuned (v2) Verifier Audit module."""
import json
import sys
import time
import requests

API = "https://api.studio.mercor.com"
CAMP = "camp_4e196b1414a1499db54b43233104b0a7"
COMP = "comp_2fa4115109d741cd94a3c409ed89e61f"
ACCT = "acct_85b680d4c5ba49a29f19c173672aebea"
ROOT = "/Users/mahmoodmapara/Desktop/terminal-bench-qc"
SID = "qcspec_ece2ca798fd2580188abd82c"

# (task, why) — pick one score-0 trajectory each from the eval map
CASES = [
    ("berth-event-reconciliation", "objective consistency -> should STAY fail"),
    ("telemetry-command-bus", "lone-model FN -> expect NEUTRAL now"),
    ("slo-budget-packet-reconciler", "DEFECT FN -> NEUTRAL unless brittle readable"),
    ("rail-yard-envelope-pipeline", "cross-model FN -> per-traj NEUTRAL now"),
]


def key():
    for line in open(f"{ROOT}/.env"):
        if line.startswith("RLS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")


H = {"Authorization": f"Bearer {key()}", "X-Campaign-Id": CAMP, "X-Company-Id": COMP,
     "X-Account-Id": ACCT, "User-Agent": "curl/8.7.1", "Content-Type": "application/json"}


def get(p, **kw):
    r = requests.get(f"{API}{p}", headers=H, params=kw or None, timeout=120)
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)


def post(p, b):
    r = requests.post(f"{API}{p}", headers=H, data=json.dumps(b), timeout=120)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text


def main():
    tmap = json.load(open(f"{ROOT}/_local/tb_modules/_eval_traj_map.json"))
    picks = []
    for task, why in CASES:
        rows = tmap.get(task, [])
        z = next((r for r in rows if (r[1] or 0) == 0), rows[0] if rows else None)
        if z:
            picks.append((task, why, z[0], z[1]))
    trig = {}
    for task, why, tid, score in picks:
        st, resp = post("/qc-audits/", {"qc_spec_id": SID, "subject_kind": "trajectory",
                                        "subject_id": tid, "source": "automatic",
                                        "function": None, "dimensions_filter": None, "subject_params": None})
        trig[tid] = (task, why, score)
        print(f"  trigger {task:34s} -> {st}")
        time.sleep(0.2)

    done = {}
    for _ in range(48):
        for tid in trig:
            if tid in done:
                continue
            st, data = get("/qc-audits/", subject_kind="trajectory", subject_id=tid, qc_spec_id=SID)
            rows = data.get("audits", data if isinstance(data, list) else []) if isinstance(data, (dict, list)) else []
            row = rows[0] if rows else None
            stt = (row or {}).get("status")
            if stt and stt not in ("pending", "queued", "running", "in_progress"):
                done[tid] = row
        print(f"  {len(done)}/{len(trig)} done...", flush=True)
        if len(done) == len(trig):
            break
        time.sleep(12)

    print("\n== retuned (v2) verdicts ==")
    for tid, row in done.items():
        task, why, score = trig[tid]
        o = row.get("outcome") or {}
        print(f"\n### {task} (score={score}) — {why}")
        print(f"   counts={o.get('status_counts')}")
        for sec in (o.get("sections") or []):
            for dd in (sec.get("dimensions") or []):
                print(f"   {str(dd.get('status','?')).upper():7s} {dd.get('dimension') or dd.get('name')}")


if __name__ == "__main__":
    main()
