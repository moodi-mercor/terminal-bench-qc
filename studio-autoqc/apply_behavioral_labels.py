#!/usr/bin/env python3
"""Apply behavioral validate_patch verdicts back onto RL Studio task custom_fields.

Reads the puller's results.csv (task_id, golden/empty scores, verdict) and PATCHes
each task's custom_fields (merge-safe):
  - all graded tasks: qc_oracle = pass|fail, qc_run_behavioral = <run tag>
  - broken-oracle (golden<1): qc_status=defective-hard, qc_remediation=broken-oracle,
    qc_confidence=confirmed
  - oracle-healthy: qc_confidence stays as-is (the leak is not behaviorally confirmed
    by oracle/no-op alone — needs the cheat batch), but qc_oracle=pass is recorded.

Usage: python apply_behavioral_labels.py <results.csv> [--run-tag ...] [--apply]
"""
import argparse
import csv
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

API = "https://api.studio.mercor.com"


def key():
    for line in open(os.path.join(os.path.dirname(__file__), "..", ".env")):
        if line.startswith("RLS_WRITE_KEY="):
            return line.split("=", 1)[1].strip()
    sys.exit("RLS_WRITE_KEY missing")


H = {"Authorization": f"Bearer {key()}", "X-Campaign-Id": "camp_4e196b1414a1499db54b43233104b0a7",
     "X-Company-Id": "comp_2fa4115109d741cd94a3c409ed89e61f", "Content-Type": "application/json"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("--run-tag", default="2026-06-27-behavioral")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    rows = [r for r in csv.DictReader(open(args.results)) if r.get("task_id")]
    print(f"{len(rows)} tasks | mode {'APPLY' if args.apply else 'DRY-RUN'}")

    def want(r):
        d = {"qc_oracle": "fail" if r["verdict"] in ("broken-oracle", "noop-passes") else "pass",
             "qc_run_behavioral": args.run_tag}
        if r["verdict"] == "broken-oracle":
            d.update({"qc_status": "defective-hard", "qc_remediation": "broken-oracle",
                      "qc_confidence": "confirmed"})
        elif r["verdict"] == "noop-passes":
            d.update({"qc_status": "defective-hard", "qc_remediation": "gameable",
                      "qc_confidence": "confirmed"})
        return d

    cnt = {"patched": 0, "skipped": 0, "fail": 0}

    def handle(r):
        if r["verdict"] == "unknown":
            cnt["skipped"] += 1
            return
        tid = r["task_id"]; w = want(r)
        try:
            cf = requests.get(f"{API}/tasks/{tid}", headers=H, timeout=60).json().get("custom_fields") or {}
            if all(cf.get(k) == v for k, v in w.items()):
                cnt["skipped"] += 1
                return
            if not args.apply:
                cnt["patched"] += 1
                return
            rr = requests.patch(f"{API}/tasks/{tid}", headers=H,
                                data=json.dumps({"custom_fields": {**cf, **w}}), timeout=60)
            cnt["patched" if rr.status_code == 200 else "fail"] += 1
        except Exception as e:
            cnt["fail"] += 1
            print("  fail", r.get("task_name"), str(e)[:60])

    with ThreadPoolExecutor(max_workers=16) as ex:
        for _ in as_completed([ex.submit(handle, r) for r in rows]):
            pass
    print("done:", cnt)


if __name__ == "__main__":
    main()
