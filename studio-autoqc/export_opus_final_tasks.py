#!/usr/bin/env python3
"""Export the FULL task trees for the opus_final deliverable (a/b lists), with the
conftest fix applied.

For each task in _local/opus_final/good_tasks_final.csv:
  - pull the complete filesystem snapshot from Studio (all files, not just test.sh)
    into _local/opus_final_tasks/{a_pass|b_fail}/<task_name>/
  - overlay the patched tests/test.sh from _local/conftest_fix_all/<name>/ when present
    (adds --noconftest; tasks not in that tree were already guarded or non-pytest)
  - drop a .done marker per task -> resumable

Usage:
  python export_opus_final_tasks.py [--workers 24] [--limit N]
Then zip:
  cd _local && zip -qr opus_final_tasks.zip opus_final_tasks opus_final
"""
import argparse
import csv
import os
import shutil
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path[:0] = [os.path.join(ROOT, "skills", "static-semantic-qc", "scripts")]
import studio_pull as sp  # noqa: E402

OUT = f"{ROOT}/_local/opus_final_tasks"
CSV = f"{ROOT}/_local/opus_final/good_tasks_final.csv"
FIX = f"{ROOT}/_local/conftest_fix_all"
SUB = {"a_pass_at_least_once": "a_pass", "b_fail_all_opus_runs": "b_fail"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    key = sp.load_key()
    allt = {t["task_id"]: t for t in sp.list_tasks(key, sp.WORLD)}
    rows = list(csv.DictReader(open(CSV)))
    if a.limit:
        rows = rows[:a.limit]
    print(f"{len(rows)} tasks to export | workers {a.workers}", flush=True)

    ct = Counter()
    lock = threading.Lock()
    prog = {"n": 0}
    t0 = time.time()

    def handle(r):
        name = r["task_name"]
        sub = SUB[r["final_bucket"]]
        droot = os.path.join(OUT, sub)
        tdir = os.path.join(droot, name)
        marker = os.path.join(tdir, ".done")
        try:
            if os.path.exists(marker):
                with lock:
                    ct["cached"] += 1
                return
            t = allt.get(r["task_id"])
            if not t:
                with lock:
                    ct["unresolved"] += 1
                return
            n_ok = sp.pull_task(key, t, droot)
            if n_ok == 0:
                with lock:
                    ct["empty"] += 1
                return
            # overlay conftest-fixed test.sh
            fixed = os.path.join(FIX, name, "tests", "test.sh")
            if os.path.isfile(fixed):
                dst = os.path.join(tdir, "tests", "test.sh")
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(fixed, dst)
                with lock:
                    ct["conftest_fixed"] += 1
            open(marker, "w").write("ok\n")
            with lock:
                ct["pulled"] += 1
        except Exception as e:
            with lock:
                ct["error"] += 1
            print(f"  [fail] {name}: {e}", flush=True)
        finally:
            with lock:
                prog["n"] += 1
                if prog["n"] % 200 == 0:
                    rate = prog["n"] / max(1e-9, time.time() - t0)
                    eta = (len(rows) - prog["n"]) / max(rate, 1e-9) / 60
                    print(f"  {prog['n']}/{len(rows)} | {dict(ct)} | {rate:.1f} tasks/s | ETA {eta:.0f}m", flush=True)

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for _ in as_completed([ex.submit(handle, r) for r in rows]):
            pass
    print(f"\nDONE. {dict(ct)}\nout: {OUT}", flush=True)


if __name__ == "__main__":
    main()
