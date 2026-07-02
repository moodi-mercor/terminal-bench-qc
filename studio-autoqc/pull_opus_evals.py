#!/usr/bin/env python3
"""Pull historical eval results and classify GOOD (QC-passing) tasks by Opus solvability.

Produces, for the healthy QC buckets (healthy-easy / healthy-hard /
healthy-unknown-difficulty), two lists:
  (a) opus_pass  — Opus solved the task at least once (score >= 1.0 in any run)
  (b) opus_fail  — Opus was run >=1 time and NEVER solved it (all runs score < 1.0)
  (also: opus_none — good task with zero Opus runs; reported separately)

Data model (RL Studio querier):
  trajectories.trajectory_output = JSON {model, score, ...}; Opus = model ILIKE %opus%.
  The querier caps unstructured rows at 100 unless the query has an explicit LIMIT,
  and JSON in WHERE/GROUP-BY over the 318k-row table times out — so we page the whole
  trajectories table with a plain SELECT (JSON only in the SELECT list) and aggregate
  locally.

Usage:
  python pull_opus_evals.py                 # full run -> _local/opus_qc/
"""
import json
import os
import sys
import time
from collections import defaultdict

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "skills", "static-semantic-qc", "scripts")]
import studio_pull as sp  # noqa: E402

API, WORLD = sp.API, sp.WORLD
OUT = os.path.normpath(os.path.join(HERE, "..", "_local", "opus_qc"))
GOOD_BUCKETS = {"healthy-easy", "healthy-hard", "healthy-unknown-difficulty"}
PASS_THRESHOLD = 1.0
CHUNK = 8000


def q(key, sql):
    for i in range(5):
        r = requests.post(f"{API}/querier/unstructured", headers=sp.headers(key),
                          json={"query": sql}, timeout=300)
        if r.status_code == 200:
            return r.json()["rows"]
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(3 * (i + 1)); continue
        raise RuntimeError(f"{r.status_code}: {r.text[:200]}")
    raise RuntimeError("query failed after retries")


def main():
    os.makedirs(OUT, exist_ok=True)
    key = sp.load_key()

    # 1. GOOD tasks (healthy QC buckets) from the full world list (has custom_fields).
    tasks = sp.list_tasks(key, WORLD)
    good = {}  # task_id -> (task_name, bucket)
    for t in tasks:
        if t.get("archived_at") is not None:
            continue
        b = (t.get("custom_fields") or {}).get("qc_final_bucket")
        if b in GOOD_BUCKETS:
            good[t["task_id"]] = (t.get("task_name"), b)
    print(f"good (QC-passing) tasks: {len(good)}", flush=True)

    # 2. Scan trajectories per batch (indexed filter avoids the full-table sort
    #    timeout), keep only Opus runs (filter on the real model field, since some
    #    batches are mixed-model), aggregate per task.
    batches = [r["trajectory_batch_id"] for r in
               q(key, f"SELECT trajectory_batch_id, COUNT(*) AS n FROM trajectories "
                       f"WHERE world_id='{WORLD}' GROUP BY 1 ORDER BY n DESC LIMIT 200")
               if r["trajectory_batch_id"]]
    print(f"batches to scan: {len(batches)}", flush=True)

    agg = defaultdict(lambda: {"runs": 0, "pass": 0, "max": None})
    total, opus_total = 0, 0
    for bi, bid in enumerate(batches, 1):
        offset = 0
        while True:
            sql = (
                "SELECT task_id AS tid, trajectory_output->>'model' AS model, "
                "trajectory_output->>'score' AS score FROM trajectories "
                f"WHERE world_id='{WORLD}' AND trajectory_batch_id='{bid}' "
                f"ORDER BY trajectory_id LIMIT {CHUNK} OFFSET {offset}"
            )
            rows = q(key, sql)
            if not rows:
                break
            total += len(rows)
            for r in rows:
                if "opus" not in (r.get("model") or "").lower():
                    continue
                opus_total += 1
                a = agg[r["tid"]]
                sc = r.get("score")
                try:
                    scf = float(sc) if sc is not None else None
                except (TypeError, ValueError):
                    scf = None
                a["runs"] += 1
                if scf is not None and scf >= PASS_THRESHOLD:
                    a["pass"] += 1
                if scf is not None and (a["max"] is None or scf > a["max"]):
                    a["max"] = scf
            if len(rows) < CHUNK:
                break
            offset += CHUNK
        print(f"  [{bi}/{len(batches)}] {bid[:16]} | scanned {total} | opus {opus_total} | opus-tasks {len(agg)}", flush=True)

    # 3. classify GOOD tasks
    rows_out = []
    counts = defaultdict(int)
    for tid, (name, bucket) in good.items():
        a = agg.get(tid)
        if not a or a["runs"] == 0:
            cls = "opus_none"
        elif a["pass"] >= 1:
            cls = "opus_pass"
        else:
            cls = "opus_fail"
        counts[cls] += 1
        counts[f"{bucket}|{cls}"] += 1
        rows_out.append({
            "task_name": name, "task_id": tid, "qc_bucket": bucket,
            "classification": cls,
            "opus_runs": a["runs"] if a else 0,
            "opus_passes": a["pass"] if a else 0,
            "opus_max_score": a["max"] if a else None,
        })

    rows_out.sort(key=lambda r: (r["classification"], r["qc_bucket"], r["task_name"] or ""))
    import csv
    with open(os.path.join(OUT, "good_tasks_opus.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
        w.writeheader(); w.writerows(rows_out)
    for cls in ("opus_pass", "opus_fail", "opus_none"):
        names = sorted(r["task_name"] for r in rows_out if r["classification"] == cls)
        open(os.path.join(OUT, f"{cls}.txt"), "w").write("\n".join(names) + "\n")
    json.dump(dict(counts), open(os.path.join(OUT, "summary.json"), "w"), indent=2)

    print(f"\nscanned {total} trajectories, {opus_total} opus runs")
    print("GOOD-task Opus classification:")
    for cls in ("opus_pass", "opus_fail", "opus_none"):
        print(f"  {cls}: {counts[cls]}")
    print("by bucket x class:")
    for b in sorted(GOOD_BUCKETS):
        for cls in ("opus_pass", "opus_fail", "opus_none"):
            print(f"  {b} | {cls}: {counts.get(f'{b}|{cls}', 0)}")
    print(f"out: {OUT}/  (good_tasks_opus.csv, opus_pass.txt, opus_fail.txt, opus_none.txt)")


if __name__ == "__main__":
    main()
