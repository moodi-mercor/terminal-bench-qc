#!/usr/bin/env python3
"""Pull task files (Studio snapshots) for the delivered Gemini-3.5-Flash-hard tasks
(the 0/8 + 1-2/8 set from batch_7c4f522a) so the static-semantic-QC gates can run on
them locally.

These tasks live in world cognition-v2-clean-internet-eval under the GDM-10k campaign
(NOT the OTS Terminal Bench world), so we override campaign/company/account + key.

Two phases for throughput (each file needs a file-url mint + an S3 GET, so a flat
work queue at high concurrency beats per-task serial):
  1. list input-files for every task (parallel)
  2. download every (task, file) presigned URL (parallel)

Output: _local/gemini_flash_qc/tasks/<task_name>/<tb-layout files>
Resumable: a task dir with .pull_done is skipped in phase 1.
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://api.studio.mercor.com"
KEY = "rls-sk-iuA_SCYWiui2xXr_HD7Rab0BfF5VPmDNlK6eAyLzcYw"
CAMP = "camp_0c1f9a9809604271a534edd77c3cbec1"   # GDM-10k
COMP = "comp_2fa4115109d741cd94a3c409ed89e61f"
ACCT = "acct_85b680d4c5ba49a29f19c173672aebea"
ROOT = os.path.normpath(os.path.join(HERE, ".."))
PERTASK = f"{ROOT}/_local/batch_gemini_flash/per_task.json"
OUT = f"{ROOT}/_local/gemini_flash_qc/tasks"
LIST_WORKERS = 24
DL_WORKERS = 48

H = {"Authorization": f"Bearer {KEY}", "X-Campaign-Id": CAMP, "X-Company-Id": COMP,
     "X-Account-Id": ACCT, "User-Agent": "curl/8.7.1"}


def get_json(url, **kw):
    for attempt in range(4):
        try:
            r = requests.get(url, headers=H, timeout=60, **kw)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(1.2 * (attempt + 1))
    return None


def list_files(task):
    tid, tname = task["task_id"], task["task_name"]
    if os.path.exists(os.path.join(OUT, tname, ".pull_done")):
        return tname, tid, None  # cached
    data = get_json(f"{API}/snapshots/task/{tid}/input-files")
    if not data:
        return tname, tid, []
    out = []
    for f in data.get("files", []):
        raw = f["key"]
        idx = raw.find("filesystem/")
        fs_path = raw[idx:] if idx >= 0 else raw
        rel = fs_path[len("filesystem/"):] if fs_path.startswith("filesystem/") else fs_path
        if rel and not rel.endswith("/"):
            out.append((fs_path, rel))
    return tname, tid, out


def dl(job):
    tname, tid, fs_path, rel = job
    dest = os.path.join(OUT, tname, rel)
    for attempt in range(3):
        j = get_json(f"{API}/snapshots/task/{tid}/file-url", params={"file_path": fs_path})
        if not j or "url" not in j:
            return False
        try:
            r = requests.get(j["url"], timeout=120)
            r.raise_for_status()
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as fh:
                fh.write(r.content)
            return True
        except Exception:
            time.sleep(1.2 * (attempt + 1))
    return False


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    per = json.load(open(PERTASK))
    delivered = [{"task_name": t, "task_id": e["task_id"]}
                 for t, e in sorted(per.items()) if e["passes"] <= 2 and e["task_id"]]
    if limit:
        delivered = delivered[:limit]
    os.makedirs(OUT, exist_ok=True)
    print(f"delivered tasks: {len(delivered)}", flush=True)

    # phase 1: gather file lists
    jobs = []
    counts = {}          # tname -> (tid, nfiles)
    cached = 0
    with ThreadPoolExecutor(max_workers=LIST_WORKERS) as ex:
        futs = [ex.submit(list_files, t) for t in delivered]
        for i, fut in enumerate(as_completed(futs), 1):
            tname, tid, files = fut.result()
            if files is None:
                cached += 1
                continue
            counts[tname] = (tid, len(files))
            for fs_path, rel in files:
                jobs.append((tname, tid, fs_path, rel))
            if i % 300 == 0:
                print(f"  listed {i}/{len(delivered)} | files queued {len(jobs)}", flush=True)
    print(f"phase1 done: {cached} cached tasks, {len(counts)} to pull, {len(jobs)} files", flush=True)

    # phase 2: download
    ok = 0
    got = {}
    with ThreadPoolExecutor(max_workers=DL_WORKERS) as ex:
        futs = {ex.submit(dl, j): j for j in jobs}
        for n, fut in enumerate(as_completed(futs), 1):
            j = futs[fut]
            if fut.result():
                ok += 1
                got[j[0]] = got.get(j[0], 0) + 1
            if n % 2000 == 0:
                print(f"  downloaded {n}/{len(jobs)} ({ok} ok)", flush=True)

    # write .pull_done markers
    for tname, (tid, nfiles) in counts.items():
        g = got.get(tname, 0)
        if g:
            open(os.path.join(OUT, tname, ".pull_done"), "w").write(f"{g}/{nfiles}\n")
    zero = [t for t in counts if got.get(t, 0) == 0]
    print(f"done. files ok {ok}/{len(jobs)} | tasks with 0 files: {len(zero)}", flush=True)
    if zero[:10]:
        print("zero-file sample:", zero[:10])


if __name__ == "__main__":
    main()
