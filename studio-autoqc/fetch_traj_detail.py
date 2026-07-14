#!/usr/bin/env python3
"""Fetch per-trajectory detail (diff + per-test statuses) for a task list.

Reads local summary attempts (attempts.jsonl), keeps only trajectories whose
task_id is in the deduped CSV, and fetches GET /trajectories/{id} detail for
each (threaded). Writes detail.jsonl in the shape triage/diff_signals/scan want.
"""
import argparse, csv, json, os, sys, threading
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "skills", "trajectory-audit", "scripts"),
                os.path.join(HERE, "..", "skills", "static-semantic-qc", "scripts")]
import studio_pull
from pull_batch import add_detail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--attempts", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--scores", default="", help="comma list to keep e.g. '0.0' or '1.0' (default all)")
    args = ap.parse_args()

    keep = {r["task_id"] for r in csv.DictReader(open(args.csv))}
    want_scores = {s for s in args.scores.split(",") if s}
    rows = []
    seen = set()
    for fn in args.attempts:
        for line in open(fn):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("task_id") in keep and d.get("trajectory_id") not in seen:
                if want_scores and str(d.get("score")) not in want_scores:
                    continue
                seen.add(d["trajectory_id"]); rows.append(d)
    print(f"{len(rows)} trajectories to fetch", flush=True)

    key = studio_pull.load_key()
    lock = threading.Lock(); prog = {"n": 0, "err": 0}
    out = open(args.out, "w")

    def handle(r):
        try:
            add_detail(key, r)
        except Exception as e:
            r["detail_error"] = str(e)
            with lock: prog["err"] += 1
        with lock:
            out.write(json.dumps(r) + "\n"); out.flush()
            prog["n"] += 1
            if prog["n"] % 100 == 0:
                print(f"  {prog['n']}/{len(rows)} (err={prog['err']})", flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for _ in as_completed([ex.submit(handle, r) for r in rows]):
            pass
    out.close()
    print(f"DONE {prog['n']} fetched, {prog['err']} errors -> {args.out}")


if __name__ == "__main__":
    main()
