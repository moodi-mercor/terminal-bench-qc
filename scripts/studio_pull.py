#!/usr/bin/env python3
"""Pull Terminal-Bench OTS tasks from RL Studio into a local tree for QC.

Reads RLS_KEY from the repo `.env`. Endpoints/identifiers are documented in
references/studio-data-access.md. Downloads each task's `filesystem/` snapshot
into <out>/<task_name>/... (the `filesystem/` prefix is stripped so the result
is a standard TB2 task tree the detectors understand).

Usage:
    python studio_pull.py --n 50 --out ../../tasks_cache
    python studio_pull.py --task-id task_xxx --out ../../tasks_cache
    python studio_pull.py --list            # just print available tasks

Requires: requests (already available in this env).
"""
import argparse
import json
import os
import sys
import time

import requests

API = "https://api.studio.mercor.com"
CAMPAIGN = "camp_4e196b1414a1499db54b43233104b0a7"   # [OTS] Terminal Bench
COMPANY = "comp_2fa4115109d741cd94a3c409ed89e61f"
WORLD = "world_2c7cdb23737845ad83a9acfa1aa8c25b"


def find_env():
    here = os.path.abspath(os.path.dirname(__file__))
    for _ in range(6):
        cand = os.path.join(here, ".env")
        if os.path.isfile(cand):
            return cand
        here = os.path.dirname(here)
    return None


def load_key():
    if os.environ.get("RLS_KEY"):
        return os.environ["RLS_KEY"]
    env = find_env()
    if not env:
        sys.exit("RLS_KEY not set and no .env found.")
    for line in open(env):
        line = line.strip()
        if line.startswith("RLS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("RLS_KEY not found in .env")


def headers(key):
    return {"Authorization": f"Bearer {key}",
            "X-Campaign-Id": CAMPAIGN, "X-Company-Id": COMPANY}


def get_json(url, key, **kw):
    r = requests.get(url, headers=headers(key), timeout=60, **kw)
    r.raise_for_status()
    return r.json()


def list_tasks(key, world, refresh=False):
    # the /full endpoint returns all ~13k tasks and takes ~60s; cache it.
    import tempfile
    cache = os.path.join(tempfile.gettempdir(), f"studio_tasks_{world}.json")
    if not refresh and os.path.isfile(cache):
        try:
            return json.load(open(cache))
        except Exception:
            pass
    data = get_json(f"{API}/tasks/world/{world}/full", key)
    tasks = data.get("tasks", data if isinstance(data, list) else [])
    try:
        json.dump(tasks, open(cache, "w"))
    except Exception:
        pass
    return tasks


def snapshot_files(key, task_id):
    data = get_json(f"{API}/snapshots/task/{task_id}/input-files", key)
    return data.get("files", [])


def download_file(key, task_id, file_path, dest, retries=3):
    for attempt in range(retries):
        try:
            j = get_json(f"{API}/snapshots/task/{task_id}/file-url", key,
                         params={"file_path": file_path})
            url = j["url"]
            r = requests.get(url, timeout=120)   # presigned S3, no auth
            r.raise_for_status()
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(r.content)
            return True
        except Exception as e:
            if attempt == retries - 1:
                print(f"    ! failed {file_path}: {e}")
                return False
            time.sleep(1.5 * (attempt + 1))   # re-mint presigned url


def pull_task(key, task, out_root):
    tid = task["task_id"]
    tname = task.get("task_name") or tid
    files = snapshot_files(key, tid)
    n_ok = 0
    for f in files:
        # snapshot keys look like `tasks/snap_<id>/filesystem/<tb-path>`.
        # The file-url endpoint wants the path rooted at `filesystem/`.
        raw = f["key"]
        idx = raw.find("filesystem/")
        fs_path = raw[idx:] if idx >= 0 else raw
        rel = fs_path[len("filesystem/"):] if fs_path.startswith("filesystem/") else fs_path
        dest = os.path.join(out_root, tname, rel)
        if download_file(key, tid, fs_path, dest):
            n_ok += 1
    print(f"  {tname}: {n_ok}/{len(files)} files", flush=True)
    return n_ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10, help="number of tasks to pull")
    ap.add_argument("--out", default="tasks_cache")
    ap.add_argument("--world", default=WORLD)
    ap.add_argument("--task-id", default=None, help="pull a single task by id")
    ap.add_argument("--names", default=None,
                    help="comma-separated task names, or @file with one name per line")
    ap.add_argument("--list", action="store_true", help="list tasks and exit")
    args = ap.parse_args()
    key = load_key()

    tasks = list_tasks(key, args.world)
    print(f"World {args.world}: {len(tasks)} tasks available")
    if args.list:
        for t in tasks[:args.n]:
            print(f"  {t.get('task_name')}  ({t['task_id']})  "
                  f"status={t.get('task_status_defn')}")
        return

    if args.task_id:
        tasks = [t for t in tasks if t["task_id"] == args.task_id]
        if not tasks:
            sys.exit(f"task-id {args.task_id} not found in world")
    elif args.names:
        if args.names.startswith("@"):
            wanted = [ln.strip() for ln in open(args.names[1:]) if ln.strip()
                      and not ln.startswith("#")]
        else:
            wanted = [n.strip() for n in args.names.split(",") if n.strip()]
        by_name = {t.get("task_name"): t for t in tasks}
        tasks = [by_name[n] for n in wanted if n in by_name]
        missing = [n for n in wanted if n not in by_name]
        if missing:
            print(f"  ! {len(missing)} name(s) not found: {missing}")
    else:
        tasks = tasks[:args.n]

    os.makedirs(args.out, exist_ok=True)
    print(f"Pulling {len(tasks)} task(s) into {args.out}/")
    for t in tasks:
        pull_task(key, t, args.out)
    print("Done.")


if __name__ == "__main__":
    main()
