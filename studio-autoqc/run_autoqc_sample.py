#!/usr/bin/env python3
"""Run the task-subject AutoQC modules (reviewer + adversary) on a sample of task_ids.

Triggers POST /qc-audits/ per (task x module), polls GET /qc-audits/ until each
completes, and reports: reviewer FAIL rate + which dims, adversary NEUTRAL (cheat-
vector candidate) rate + which cv. Use to validate the modules at scale + measure
yield/cost before committing to the full needs-review set.

Usage:
    python run_autoqc_sample.py --ids-file _local/behavioral_all/autoqc_target_ids.txt --n 250
    python run_autoqc_sample.py ... --modules reviewer,adversary
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
MODS = {"reviewer": "qcspec_7bddfd703a12994dbc31fd1b",
        "adversary": "qcspec_e5cb0f9be6123abea7d720c4",
        "static": "qcspec_7e5dbd46cf6de18e0a08d2a6"}


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids-file", required=True)
    ap.add_argument("--n", type=int, default=250)
    ap.add_argument("--modules", default="reviewer,adversary")
    ap.add_argument("--out", default="_local/autoqc_sample")
    ap.add_argument("--collect-only", action="store_true", help="skip triggering; just poll/collect")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    ids = [l.strip() for l in open(args.ids_file) if l.strip()][:args.n]
    mods = [m.strip() for m in args.modules.split(",")]
    print(f"{len(ids)} tasks x {len(mods)} modules ({mods}) = {len(ids)*len(mods)} audits")

    triggered = {(tid, m): True for tid in ids for m in mods}
    if not args.collect_only:
        for tid in ids:
            for m in mods:
                try:
                    requests.post(f"{API}/qc-audits/", headers=H, timeout=60,
                                  data=json.dumps({"qc_spec_id": MODS[m], "subject_kind": "task",
                                                   "subject_id": tid, "source": "automatic",
                                                   "subject_params": None}))
                except Exception:
                    pass
                time.sleep(0.12)
        print(f"triggered {len(triggered)}; polling ...", flush=True)
    else:
        print("collect-only: polling already-triggered audits ...", flush=True)

    results = {}
    for rnd in range(80):
        todo = [k for k in triggered if k not in results]
        if not todo:
            break
        for (tid, m) in todo:
            try:
                d = requests.get(f"{API}/qc-audits/", headers=H, timeout=45,
                                 params={"subject_kind": "task", "subject_id": tid, "qc_spec_id": MODS[m]}).json()
            except Exception:
                continue  # transient timeout — retry next round
            rows = d.get("audits", d if isinstance(d, list) else [])
            row = rows[0] if rows else None
            st = (row or {}).get("status")
            if st and st not in ("pending", "queued", "running", "in_progress"):
                gp, dims = verdict(row.get("outcome") or row.get("result") or {})
                results[(tid, m)] = {"status": st, "global_pass": gp, "dims": dims}
        json.dump({f"{k[0]}|{k[1]}": v for k, v in results.items()}, open(f"{args.out}/results.json", "w"))
        if rnd % 4 == 0:
            print(f"  round {rnd+1}: {len(results)}/{len(triggered)} complete", flush=True)
        if len(results) >= len(triggered):
            break
        time.sleep(20)

    # report
    rev_fail = collections.Counter()
    rev_fail_tasks = 0
    adv_neutral = collections.Counter()
    adv_neutral_tasks = 0
    for (tid, m), r in results.items():
        fails = [dn for dn, stt in r["dims"] if stt == "fail"]
        neuts = [dn for dn, stt in r["dims"] if stt == "neutral"]
        if m == "reviewer" and fails:
            rev_fail_tasks += 1
            rev_fail.update(fails)
        if m == "adversary" and neuts:
            adv_neutral_tasks += 1
            adv_neutral.update(neuts)
    json.dump({str(k): v for k, v in results.items()}, open(f"{args.out}/results.json", "w"), indent=1)
    n_rev = sum(1 for k in results if k[1] == "reviewer")
    n_adv = sum(1 for k in results if k[1] == "adversary")
    print(f"\ncomplete {len(results)}/{len(triggered)}")
    print(f"REVIEWER: {rev_fail_tasks}/{n_rev} tasks with >=1 FAIL dim. top fail dims: {dict(rev_fail.most_common(8))}")
    print(f"ADVERSARY: {adv_neutral_tasks}/{n_adv} tasks with >=1 NEUTRAL cheat-vector. top: {dict(adv_neutral.most_common(8))}")


if __name__ == "__main__":
    main()
