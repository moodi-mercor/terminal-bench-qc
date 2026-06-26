#!/usr/bin/env python3
"""Write QC triage buckets onto RL Studio tasks via custom_fields (merge-safe).

The QC bucketizer (shared/aggregate.py -> tasks_buckets.csv) decides ship/fixable/
review/total + remediation + priority per task NAME. This maps each name to its
Studio task_id and PATCHes the task's `custom_fields` with qc_* keys, MERGING into
the existing custom_fields (which on OTS tasks already hold difficulty/category/
gh_files/... — a blind set would wipe them). Idempotent: skips a task whose qc_*
already match. Defaults to DRY-RUN; pass --apply to write.

Endpoints (per the Studio API): GET /tasks/{id}  +  PATCH /tasks/{id} {custom_fields}.
Auth: Bearer RLS_WRITE_KEY (write tier) + X-Campaign-Id / X-Company-Id headers.

Usage:
    # build name->id map once (25MB world list):
    #   GET /tasks/world/{WORLD}  -> {task_name: task_id}
    python studio_label.py --buckets buckets.csv --map name2id.json            # dry-run
    python studio_label.py --buckets buckets.csv --map name2id.json --apply    # write
"""
import argparse
import csv
import glob
import json
import os
import sys
import time

import requests

API = "https://api.studio.mercor.com"
WORLD = "world_2c7cdb23737845ad83a9acfa1aa8c25b"
QC_KEYS = ("qc_bucket", "qc_status", "qc_remediation", "qc_priority", "qc_confidence", "qc_run")


def _env(name):
    if os.environ.get(name):
        return os.environ[name]
    env = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.isfile(env):
        for line in open(env):
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip()
    sys.exit(f"{name} not set (need a WRITE-tier Studio key in .env)")


def headers():
    return {"Authorization": f"Bearer {_env('RLS_WRITE_KEY')}",
            "X-Campaign-Id": "camp_4e196b1414a1499db54b43233104b0a7",
            "X-Company-Id": "comp_2fa4115109d741cd94a3c409ed89e61f",
            "Content-Type": "application/json"}


def load_map(path):
    if path and os.path.isfile(path):
        return json.load(open(path))
    print("fetching world task list (one ~25MB GET) ...")
    r = requests.get(f"{API}/tasks/world/{WORLD}", headers=headers(), timeout=180)
    r.raise_for_status()
    m = {t["task_name"]: t["task_id"] for t in r.json()["tasks"]}
    if path:
        json.dump(m, open(path, "w"))
    return m


def load_buckets(spec):
    rows = {}
    for f in glob.glob(spec):
        for r in csv.DictReader(open(f)):
            rows[r["task"]] = r  # last write wins (sticky handled upstream)
    return rows


def desired(row, run_tag):
    # qc_status may be absent in older CSVs — derive from bucket as a fallback.
    rollup = {"ship": "passing", "fixable": "needs-fixing",
              "total": "defective-hard", "review": "needs-review"}
    status = row.get("status") or rollup.get(row.get("bucket", ""), "")
    return {"qc_bucket": row["bucket"], "qc_status": status,
            "qc_remediation": row.get("remediation", ""),
            "qc_priority": row.get("priority", ""), "qc_confidence": row.get("confidence", ""),
            "qc_run": run_tag}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--buckets", required=True, help="glob of tasks_buckets.csv file(s)")
    ap.add_argument("--map", default="name2id.json", help="name->task_id cache (built if absent)")
    ap.add_argument("--run-tag", default="static")
    ap.add_argument("--apply", action="store_true", help="actually PATCH (default: dry-run)")
    ap.add_argument("--limit", type=int, default=0, help="cap number of tasks (0 = all)")
    ap.add_argument("--delay", type=float, default=0.15, help="seconds between writes")
    args = ap.parse_args()

    name2id = load_map(args.map)
    buckets = load_buckets(args.buckets)
    targets = [(n, r, name2id[n]) for n, r in buckets.items() if n in name2id]
    missing = [n for n in buckets if n not in name2id]
    if args.limit:
        targets = targets[:args.limit]
    print(f"{len(buckets)} bucketed; {len(targets)} matched to Studio task_id; "
          f"{len(missing)} not in world (e.g. public-TB controls)")
    print(f"mode: {'APPLY' if args.apply else 'DRY-RUN'}")

    h = headers()
    applied = skipped = failed = 0
    for i, (name, row, tid) in enumerate(targets, 1):
        want = desired(row, args.run_tag)
        try:
            cur = requests.get(f"{API}/tasks/{tid}", headers=h, timeout=60).json()
            cf = cur.get("custom_fields") or {}
        except Exception as e:
            failed += 1; print(f"  [GET fail] {name}: {e}"); continue
        if all(cf.get(k) == v for k, v in want.items()):
            skipped += 1; continue
        if not args.apply:
            applied += 1
            if applied <= 5:
                print(f"  would PATCH {name} ({row['bucket']}/{row.get('remediation','')}) -> {tid}")
            continue
        merged = {**cf, **want}
        try:
            r = requests.patch(f"{API}/tasks/{tid}", headers=h,
                               data=json.dumps({"custom_fields": merged}), timeout=60)
            if r.status_code == 200:
                applied += 1
            else:
                failed += 1; print(f"  [PATCH {r.status_code}] {name}: {r.text[:120]}")
        except Exception as e:
            failed += 1; print(f"  [PATCH fail] {name}: {e}")
        time.sleep(args.delay)
        if i % 50 == 0:
            print(f"  ... {i}/{len(targets)} (applied {applied}, skipped {skipped}, failed {failed})")
    verb = "patched" if args.apply else "would patch"
    print(f"done: {verb} {applied}, skipped(unchanged) {skipped}, failed {failed}")


if __name__ == "__main__":
    main()
