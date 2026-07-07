#!/usr/bin/env python3
"""Concurrent variant of bulk_patch_conftest.py.

Same fix (insert --noconftest into the pytest grader line, upload a new snapshot),
same safety wrapper (backup + skip-guarded + verify + resumable state.jsonl), but
fires the /snapshots/task/{id}/update writes through a thread pool instead of the
5/min self-throttle. Empirically the endpoint tolerates ~15-wide concurrency at
~120 writes/min with no 429s; upload_testsh() still backs off on any 429 seen.

Shares the SAME out dir / state.jsonl / backup dir as the sequential tool, so the
two are interchangeable and resume off each other.

Usage:
  python bulk_patch_conftest_fast.py --dry-run
  python bulk_patch_conftest_fast.py --apply --workers 12
"""
import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "skills", "static-semantic-qc", "scripts")]
import studio_pull as sp  # noqa: E402
import bulk_patch_conftest as bp  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--out", default=bp.OUT)
    a = ap.parse_args()
    apply = a.apply and not a.dry_run

    os.makedirs(a.out, exist_ok=True)
    backup_dir = os.path.join(a.out, "backup")
    state_path = os.path.join(a.out, "state.jsonl")
    rkey = bp.env("RLS_KEY")
    wkey = bp.env("RLS_WRITE_KEY")

    tasks = sp.list_tasks(rkey, sp.WORLD)
    rows = sorted(
        ({"task_name": t.get("task_name"), "task_id": t["task_id"]}
         for t in tasks
         if (t.get("custom_fields") or {}).get("qc_conftest_vulnerable") == "true"
         and t.get("archived_at") is None),
        key=lambda x: x["task_name"] or "",
    )
    done = bp.load_done(state_path)
    todo = [r for r in rows
            if not (r["task_name"] in done
                    and done[r["task_name"]] in ("patched", "skipped-guarded", "skipped-notest"))]
    if a.limit:
        todo = todo[:a.limit]
    print(f"CQV vulnerable: {len(rows)} | done: {len(done)} | TODO: {len(todo)} | "
          f"workers: {a.workers} | mode: {'APPLY' if apply else 'DRY-RUN'}", flush=True)

    lock = threading.Lock()
    sf = open(state_path, "a")
    counters = {"patched": 0, "skipped-guarded": 0, "skipped-notest": 0,
                "verify-failed": 0, "error": 0}

    def record(name, tid, status, err=None):
        rec = {"name": name, "id": tid, "status": status}
        if err:
            rec["err"] = err[:200]
        with lock:
            sf.write(json.dumps(rec) + "\n")
            sf.flush()
            counters[status] = counters.get(status, 0) + 1

    def work(row):
        name, tid = row["task_name"], row["task_id"]
        try:
            fs, txt = bp.fetch_testsh(rkey, tid)
            if txt is None:
                record(name, tid, "skipped-notest")
                return
            new, changed = bp.patch(txt)
            if not changed:
                record(name, tid, "skipped-guarded")
                return
            bpath = os.path.join(backup_dir, name, "test.sh")
            with lock:
                os.makedirs(os.path.dirname(bpath), exist_ok=True)
            open(bpath, "w").write(txt)
            if not apply:
                record(name, tid, "patched")  # dry-run: count as would-patch
                return
            bp.upload_testsh(wkey, tid, new)
            _, check = bp.fetch_testsh(rkey, tid)
            ok = check is not None and "--noconftest" in check
            record(name, tid, "patched" if ok else "verify-failed")
        except Exception as e:
            record(name, tid, "error", str(e))

    t0 = time.time()
    n = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(work, r) for r in todo]
        for _ in as_completed(futs):
            n += 1
            if n % 100 == 0:
                rate = n / max(1e-9, time.time() - t0) * 60
                print(f"  {n}/{len(todo)} | "
                      + " ".join(f"{k}={v}" for k, v in counters.items() if v)
                      + f" | {rate:.0f}/min", flush=True)
    sf.close()
    wall = time.time() - t0
    print(f"\nDONE ({'APPLY' if apply else 'DRY-RUN'}) in {wall/60:.1f} min. "
          + " ".join(f"{k}={v}" for k, v in counters.items()), flush=True)
    print(f"backups: {backup_dir}\nstate: {state_path}", flush=True)


if __name__ == "__main__":
    main()
