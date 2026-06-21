#!/usr/bin/env python3
"""Helper — fetch a task's instruction + verifier source from Studio snapshots.

The judge (Stage 3) needs to read the verifier to decide whether a failing check
is brittle or fair. Diffs come from pull_batch.py --with-tests; the verifier and
instruction come from here, via the same snapshot endpoints studio_pull.py uses.

Fetches instruction.md + tests/test_outputs.py (+ tests/test.sh) for each task id
into <out>/<task_name>/...  (best-effort; skips files a task doesn't have).

Usage:
  python fetch_task_files.py --ids '{"task-name":"task_id",...}' --out audit_out/src
  python fetch_task_files.py --ids @audit_out/single_check_ids.json --out audit_out/src
"""
import argparse
import json
import os

import requests

from common import API, get_json, load_key

def _wanted(rel):
    """instruction.md + every verifier-side script (tests/*.py, tests/*.sh) —
    including helper graders like verify.py the test delegates to."""
    if rel == "instruction.md":
        return True
    return rel.startswith("tests/") and rel.endswith((".py", ".sh"))


def snapshot_files(key, task_id):
    data = get_json(f"/snapshots/task/{task_id}/input-files", key)
    return data.get("files", [])


def fetch_file(key, task_id, fs_path, dest):
    j = get_json(f"/snapshots/task/{task_id}/file-url", key, params={"file_path": fs_path})
    r = requests.get(j["url"], timeout=120)   # presigned S3, no auth
    r.raise_for_status()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(r.content)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True, help='JSON {name:task_id} or @file')
    ap.add_argument("--out", default="audit_out/src")
    args = ap.parse_args()
    key = load_key()

    raw = args.ids
    if raw.startswith("@"):
        raw = open(raw[1:]).read()
    ids = json.loads(raw)

    for name, tid in ids.items():
        files = snapshot_files(key, tid)
        got = 0
        for f in files:
            raw_key = f["key"]
            idx = raw_key.find("filesystem/")
            fs_path = raw_key[idx:] if idx >= 0 else raw_key
            rel = fs_path[len("filesystem/"):] if fs_path.startswith("filesystem/") else fs_path
            if not _wanted(rel):
                continue
            try:
                fetch_file(key, tid, fs_path, os.path.join(args.out, name, rel))
                got += 1
            except Exception as e:
                print(f"  ! {name}/{rel}: {e}")
        print(f"  {name}: {got} file(s)")


if __name__ == "__main__":
    main()
