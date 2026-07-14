#!/usr/bin/env python3
"""Concurrent, resumable pull of the reward-hack-leak bucket into a local tree,
so the Modal reward-hack probe can build each image. Reuses export_broken_oracle's
pull_one (per-task filesystem snapshot download) — the concurrent path that pulled
all 2,509 broken oracles cleanly (studio_pull --names dies ~23 in on big lists).

Out: _local/rh_all/tasks/<name>/ + manifest.csv
Usage: python pull_reward_hack.py [--workers 12]
"""
import argparse
import csv
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "skills", "static-semantic-qc", "scripts")]
import studio_pull as sp  # noqa: E402
import export_broken_oracle as ebo  # noqa: E402  (reuse pull_one/already_pulled)

OUT = os.path.normpath(os.path.join(HERE, "..", "_local", "rh_all"))
BUCKET = "reward-hack-leak"
# both overridable via --bucket/--out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--bucket", default=BUCKET)
    a = ap.parse_args()
    key = sp.load_key()
    tasks = sp.list_tasks(key, sp.WORLD)
    def cf(t, k): return (t.get("custom_fields") or {}).get(k)
    rh = sorted((t for t in tasks
                 if t.get("archived_at") is None and cf(t, "qc_final_bucket") == a.bucket),
                key=lambda t: t.get("task_name") or "")
    print(f"{a.bucket}: {len(rh)} tasks", flush=True)

    tasks_root = os.path.join(a.out, "tasks")
    os.makedirs(tasks_root, exist_ok=True)
    with open(os.path.join(a.out, "manifest.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["task_name", "task_id", "qc_remediation", "qc_priority", "difficulty"])
        for t in rh:
            w.writerow([t.get("task_name"), t["task_id"], cf(t, "qc_remediation"),
                        cf(t, "qc_priority"), cf(t, "difficulty")])

    done = ok = skip = err = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(ebo.pull_one, key, t, tasks_root): t for t in rh}
        for i, fut in enumerate(as_completed(futs), 1):
            name, status, n = fut.result()
            if status == "ok": ok += 1
            elif status == "skip": skip += 1
            else: err += 1; print(f"  ! {name}: {status}", flush=True)
            if i % 50 == 0 or i == len(rh):
                print(f"  [{i}/{len(rh)}] ok={ok} skip={skip} err={err}", flush=True)
    print(f"\nDone. downloaded={ok} present={skip} errors={err} -> {tasks_root}/", flush=True)


if __name__ == "__main__":
    main()
