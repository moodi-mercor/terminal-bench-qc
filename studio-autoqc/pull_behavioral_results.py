#!/usr/bin/env python3
"""Pull validate_patch results for a behavioral batch and classify each task.

For each trajectory in the batch: read trajectory_output.{empty_score, golden_score,
validation_passed}. Classify:
  golden_score < 1            -> broken-oracle   (defective-hard, confirmed)
  empty_score  > 0            -> noop-passes     (defective-hard / gameable, confirmed)
  empty=0 and golden=1        -> oracle-healthy  (no-op fails, oracle works)

Writes results.csv + behavioral_signals.json {task_name: {oracle, noop}} for the
bucketizer, and prints the histogram.

Usage: python pull_behavioral_results.py <batch_id> [--out _local/behavioral_p0]
"""
import argparse
import json
import os
import sys
import collections
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

API = "https://api.studio.mercor.com"
H = None


def key():
    for line in open(os.path.join(os.path.dirname(__file__), "..", ".env")):
        if line.startswith("RLS_WRITE_KEY="):
            return line.split("=", 1)[1].strip()
    sys.exit("RLS_WRITE_KEY not in .env")


def main():
    global H
    ap = argparse.ArgumentParser()
    ap.add_argument("batch_id", nargs="+")
    ap.add_argument("--out", default="_local/behavioral_p0")
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    H = {"Authorization": f"Bearer {key()}",
         "X-Campaign-Id": "camp_4e196b1414a1499db54b43233104b0a7",
         "X-Company-Id": "comp_2fa4115109d741cd94a3c409ed89e61f",
         "Content-Type": "application/json"}
    trajs = []
    for bid in args.batch_id:
        trajs += requests.get(f"{API}/trajectories/batch/{bid}", headers=H, timeout=120).json()["trajectories"]
    print(f"{len(args.batch_id)} batch(es), {len(trajs)} trajectories; fetching outputs ...")

    def fetch(tr):
        tid = tr["trajectory_id"]
        try:
            o = requests.get(f"{API}/trajectories/{tid}", headers=H, timeout=60).json().get("trajectory_output") or {}
        except Exception as e:
            return {"task_name": tr.get("task_name"), "task_id": tr["task_id"], "error": str(e)[:60]}
        return {"task_name": tr.get("task_name"), "task_id": tr["task_id"],
                "empty_score": o.get("empty_score"), "golden_score": o.get("golden_score"),
                "validation_passed": o.get("validation_passed"),
                "tests_passed": o.get("tests_passed"), "tests_total": o.get("tests_total")}

    rows = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for f in as_completed([ex.submit(fetch, t) for t in trajs]):
            rows.append(f.result())

    def classify(r):
        if r.get("error") or r.get("golden_score") is None:
            return "unknown"
        if r["golden_score"] < 1:
            return "broken-oracle"
        if (r.get("empty_score") or 0) > 0:
            return "noop-passes"
        return "oracle-healthy"

    hist = collections.Counter()
    signals = {}
    import csv
    with open(os.path.join(args.out, "results.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["task_name", "task_id", "empty_score", "golden_score", "validation_passed", "verdict"])
        for r in rows:
            v = classify(r)
            hist[v] += 1
            w.writerow([r.get("task_name"), r.get("task_id"), r.get("empty_score"),
                        r.get("golden_score"), r.get("validation_passed"), v])
            if r.get("task_name") and v != "unknown":
                signals[r["task_name"]] = {"oracle": 1 if r["golden_score"] >= 1 else 0,
                                           "noop": 1 if (r.get("empty_score") or 0) > 0 else 0}
    json.dump(signals, open(os.path.join(args.out, "behavioral_signals.json"), "w"), indent=1)
    print("verdicts:", dict(hist))
    bad = [r for r in rows if classify(r) in ("broken-oracle", "noop-passes")]
    print(f"CONFIRMED defective: {len(bad)}")
    for r in bad[:25]:
        print(f"  {classify(r):14} {r.get('task_name')}  empty={r.get('empty_score')} golden={r.get('golden_score')}")


if __name__ == "__main__":
    main()
