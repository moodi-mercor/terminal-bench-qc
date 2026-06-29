#!/usr/bin/env python3
"""Stage 1 — pull a Studio trajectory batch into a local JSONL of attempts.

A trajectory batch (the thing behind a studio.mercor.com/admin/batch/<id> URL)
is a completed eval run: every task tried N times by one or more models, each
attempt already carrying the score the verifier gave it. We do NOT run anything
here — we read what already exists.

Two passes, cheapest first:
  - SUMMARY (default): page through /trajectories/batch/<id>. One row per
    attempt with task_name, model, and final_score. Enough for pass-rate,
    split-score, and all-fail triage. ~cheap (one call per page).
  - DETAIL (--with-tests): for a NARROWED set of tasks, fetch each trajectory's
    per-test pass/fail map (test_statuses) and code diff from
    /trajectories/<id>. Targeted, so it stays cheap — run it on the candidate
    tasks triage surfaces, never the whole batch.

Output: <out> JSONL, one attempt per line:
  {"trajectory_id","task_id","task_name","model","status","score",
   "test_statuses"?: {...}, "diff"?: "..."}

Usage:
  python pull_batch.py batch_c5e617... --out attempts.jsonl
  python pull_batch.py batch_c5e617... --out attempts.jsonl --max 2000
  # add per-test detail for specific tasks (the triage candidates):
  python pull_batch.py batch_c5e617... --out detail.jsonl \
      --with-tests --tasks abandoned-cart-releaser,aborted-request-cascade
"""
import argparse
import json
import os

from common import get_json, load_key


def pull_summary(key, batch_id, page_size, max_rows):
    """Yield summary rows for every attempt in the batch."""
    page = 1
    seen = 0
    while True:
        data = get_json(f"/trajectories/batch/{batch_id}", key,
                        params={"limit": str(page_size), "offset": (page - 1) * page_size})
        rows = data.get("trajectories", [])
        if not rows:
            break
        for t in rows:
            yield {
                "trajectory_id": t.get("trajectory_id"),
                "task_id": t.get("task_id"),
                "task_name": t.get("task_name"),
                "model": t.get("orchestrator_llm_model"),
                "status": t.get("trajectory_status"),
                "score": t.get("final_score"),
                # free trajectory-shape signals (no detail call needed)
                "tool_calls": t.get("trajectory_statistics_tool_calls"),
                "total_tokens": t.get("trajectory_statistics_total_tokens"),
                "time_elapsed": t.get("trajectory_time_elapsed"),
            }
            seen += 1
            if max_rows and seen >= max_rows:
                return
        pg = data.get("pagination") or {}
        if page >= (pg.get("total_pages") or page):
            break
        page += 1


def add_detail(key, row):
    """Fetch per-test statuses + diff for one trajectory (the expensive call)."""
    t = get_json(f"/trajectories/{row['trajectory_id']}", key)
    out = t.get("trajectory_output") or {}
    if isinstance(out, dict):
        row["test_statuses"] = out.get("test_statuses") or {}
        row["diff"] = out.get("solution") or ""
        row["tests_passed"] = out.get("tests_passed")
        row["tests_failed"] = out.get("tests_failed")
        # runtime context — distinguishes a runtime/setup bug from a bad solution,
        # and gives the judge the failure reason without the full transcript.
        row["exit_code"] = out.get("exit_code")
        row["eval_status"] = out.get("eval_status")
        row["error_message"] = out.get("error_message")
        row["duration_seconds"] = out.get("duration_seconds")
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("batch_id")
    ap.add_argument("--out", default="attempts.jsonl")
    ap.add_argument("--page-size", type=int, default=100)
    ap.add_argument("--max", type=int, default=0, help="cap rows (0 = all)")
    ap.add_argument("--with-tests", action="store_true",
                    help="also fetch per-test statuses + diff (per-trajectory call)")
    ap.add_argument("--tasks", default="",
                    help="comma-separated task_name filter (use with --with-tests)")
    args = ap.parse_args()
    key = load_key()

    want = {s for s in args.tasks.split(",") if s}
    n = 0
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        for row in pull_summary(key, args.batch_id, args.page_size, args.max):
            if want and row["task_name"] not in want:
                continue
            if args.with_tests and row.get("status") == "completed":
                try:
                    add_detail(key, row)
                except Exception as e:
                    row["detail_error"] = str(e)
            f.write(json.dumps(row) + "\n")
            n += 1
            if n % 200 == 0:
                print(f"  ...{n} rows", flush=True)
    print(f"Wrote {n} attempt(s) -> {args.out}")


if __name__ == "__main__":
    main()
