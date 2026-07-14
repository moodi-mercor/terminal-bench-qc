#!/usr/bin/env python3
"""Pull task snapshots for the tb2400 Airtable-base tasks that aren't already local.

Tasks live in world_07deccb138c3471585223bc682e0d2a0 (GDM-10k campaign). Folder name
is the task_id (Airtable rows have no slug/name). Two-phase parallel pull (list files,
then download presigned URLs). Resumable via .pull_done markers.

Output: _local/tb2400/tasks/<task_id>/<tb-layout files>
Usage: python pull_tb2400.py [limit]
"""
import json, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://api.studio.mercor.com"
KEY = "rls-sk-iuA_SCYWiui2xXr_HD7Rab0BfF5VPmDNlK6eAyLzcYw"
CAMP = "camp_0c1f9a9809604271a534edd77c3cbec1"
COMP = "comp_2fa4115109d741cd94a3c409ed89e61f"
ACCT = "acct_85b680d4c5ba49a29f19c173672aebea"
ROOT = os.path.normpath(os.path.join(HERE, ".."))
IDS = os.environ.get("PULL_IDS", f"{ROOT}/_local/tb2400/need_pull.txt")
OUT = os.environ.get("PULL_OUT", f"{ROOT}/_local/tb2400/tasks")
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


def list_files(tid):
    if os.path.exists(os.path.join(OUT, tid, ".pull_done")):
        return tid, None
    data = get_json(f"{API}/snapshots/task/{tid}/input-files")
    if not data:
        return tid, []
    out = []
    for f in data.get("files", []):
        raw = f["key"]
        idx = raw.find("filesystem/")
        fs_path = raw[idx:] if idx >= 0 else raw
        rel = fs_path[len("filesystem/"):] if fs_path.startswith("filesystem/") else fs_path
        if rel and not rel.endswith("/"):
            out.append((fs_path, rel))
    return tid, out


def dl(job):
    tid, fs_path, rel = job
    dest = os.path.join(OUT, tid, rel)
    for attempt in range(3):
        j = get_json(f"{API}/snapshots/task/{tid}/file-url", params={"file_path": fs_path})
        if not j or "url" not in j:
            return False
        try:
            r = requests.get(j["url"], timeout=120); r.raise_for_status()
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            open(dest, "wb").write(r.content)
            return True
        except Exception:
            time.sleep(1.2 * (attempt + 1))
    return False


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    ids = [t for t in open(IDS).read().split() if t]
    if limit:
        ids = ids[:limit]
    os.makedirs(OUT, exist_ok=True)
    print(f"tasks to consider: {len(ids)}", flush=True)

    jobs = []; counts = {}; cached = 0
    with ThreadPoolExecutor(LIST_WORKERS) as ex:
        futs = [ex.submit(list_files, t) for t in ids]
        for i, fut in enumerate(as_completed(futs), 1):
            tid, files = fut.result()
            if files is None:
                cached += 1; continue
            counts[tid] = len(files)
            for fs_path, rel in files:
                jobs.append((tid, fs_path, rel))
            if i % 300 == 0:
                print(f"  listed {i}/{len(ids)} | files queued {len(jobs)}", flush=True)
    print(f"phase1: {cached} cached, {len(counts)} to pull, {len(jobs)} files", flush=True)

    ok = 0; got = {}
    with ThreadPoolExecutor(DL_WORKERS) as ex:
        futs = {ex.submit(dl, j): j for j in jobs}
        for n, fut in enumerate(as_completed(futs), 1):
            j = futs[fut]
            if fut.result():
                ok += 1; got[j[0]] = got.get(j[0], 0) + 1
            if n % 2000 == 0:
                print(f"  downloaded {n}/{len(jobs)} ({ok} ok)", flush=True)

    for tid, nfiles in counts.items():
        g = got.get(tid, 0)
        if g:
            open(os.path.join(OUT, tid, ".pull_done"), "w").write(f"{g}/{nfiles}\n")
    zero = [t for t in counts if got.get(t, 0) == 0]
    print(f"done. files ok {ok}/{len(jobs)} | tasks with 0 files: {len(zero)}", flush=True)
    if zero[:10]:
        print("zero-file sample:", zero[:10])


if __name__ == "__main__":
    main()
