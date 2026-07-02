#!/usr/bin/env python3
"""Fetch + patch every conftest-vulnerable task's tests/test.sh LOCALLY (no Studio writes).

Studio writes are capped at 5/min (~31h for 9,011 tasks); reads are not, so this
fetches all vulnerable tasks' tests/test.sh in parallel, inserts `--noconftest` into
the pytest grader line, and writes the patched files into a local tree that can be
zipped or committed to GitHub.

Output tree (OUT):
  <task_name>/tests/test.sh        patched grader (ready to upload/apply)
  manifest.csv                     task_name,task_id,status,before,after
  fixed_task_names.txt             one patched task_name per line

status: patched | guarded (already has --noconftest) | notest | error

Resumable: skips tasks whose patched test.sh already exists.

Usage:
  python export_conftest_fix.py                 # all 9,011, default OUT, 24 workers
  python export_conftest_fix.py --limit 20      # quick sample
  python export_conftest_fix.py --workers 32 --out ../_local/conftest_fix_all
"""
import argparse
import csv
import os
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "skills", "static-semantic-qc", "scripts")]
import studio_pull as sp  # noqa: E402

API, WORLD = sp.API, sp.WORLD
OUT = os.path.normpath(os.path.join(HERE, "..", "_local", "conftest_fix_all"))
INVOKE = re.compile(r'(\s*)(python3?\s+-m\s+pytest|pytest)(\s)', re.M)


def is_grader(line):
    s = line.strip()
    if s.startswith("#") or "import pytest" in s or "command -v" in s:
        return False
    if re.search(r'\b(pip3?|uv)\b.*\binstall\b', s):
        return False
    return bool(re.match(r'(python3?\s+-m\s+pytest|pytest)\s', s))


def patch(src):
    if "--noconftest" in src or "--confcutdir" in src:
        return src, False
    out, changed = [], False
    for line in src.splitlines(keepends=True):
        if not changed and is_grader(line):
            line = INVOKE.sub(r'\1\2 --noconftest\3', line, count=1)
            changed = True
        out.append(line)
    return "".join(out), changed


def get_json_retry(url, key, retries=4, **kw):
    for i in range(retries):
        try:
            r = requests.get(url, headers=sp.headers(key), timeout=60, **kw)
            if r.status_code == 429:
                time.sleep(2 * (i + 1)); continue
            r.raise_for_status()
            return r.json()
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(1.5 * (i + 1))


def fetch_testsh(key, tid):
    files = sp.snapshot_files(key, tid)
    cand = [f for f in files if f["key"].rstrip("/").endswith("filesystem/tests/test.sh")]
    if not cand:
        cand = [f for f in files if f["key"].rstrip("/").endswith("tests/test.sh")]
    if not cand:
        return None
    raw = cand[0]["key"]
    fs = raw[raw.find("filesystem/"):] if "filesystem/" in raw else raw
    for i in range(4):
        j = get_json_retry(f"{API}/snapshots/task/{tid}/file-url", key, params={"file_path": fs})
        try:
            r = requests.get(j["url"], timeout=120)
            r.raise_for_status()
            return r.text
        except Exception:
            if i == 3:
                raise
            time.sleep(1.5 * (i + 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    key = sp.load_key()
    tasks = sp.list_tasks(key, WORLD)
    vuln = sorted(
        [t for t in tasks
         if (t.get("custom_fields") or {}).get("qc_conftest_vulnerable") == "true"
         and t.get("archived_at") is None],
        key=lambda t: t.get("task_name") or "",
    )
    if a.limit:
        vuln = vuln[:a.limit]
    print(f"{len(vuln)} vulnerable tasks | {a.workers} workers | out={a.out}", flush=True)

    ct = Counter()
    rows = []
    lock = threading.Lock()
    prog = {"n": 0}
    t0 = time.time()

    def handle(t):
        name, tid = t.get("task_name"), t["task_id"]
        dest = os.path.join(a.out, name, "tests", "test.sh")
        try:
            if os.path.exists(dest):
                with lock:
                    ct["cached"] += 1
                return
            txt = fetch_testsh(key, tid)
            if txt is None:
                with lock:
                    ct["notest"] += 1
                    rows.append((name, tid, "notest", "", ""))
                return
            new, changed = patch(txt)
            status = "patched" if changed else "guarded"
            before = next((l.strip() for l in txt.splitlines() if is_grader(l)), "")
            after = next((l.strip() for l in new.splitlines() if "--noconftest" in l), before)
            if changed:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                open(dest, "w").write(new)
            with lock:
                ct[status] += 1
                rows.append((name, tid, status, before, after))
        except Exception as e:
            with lock:
                ct["error"] += 1
                rows.append((name, tid, "error", str(e)[:150], ""))
        finally:
            with lock:
                prog["n"] += 1
                if prog["n"] % 250 == 0:
                    rate = prog["n"] / max(1e-9, time.time() - t0)
                    print(f"  {prog['n']}/{len(vuln)} | {dict(ct)} | {rate:.1f}/s", flush=True)

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for _ in as_completed([ex.submit(handle, t) for t in vuln]):
            pass

    rows.sort(key=lambda r: r[0])
    with open(os.path.join(a.out, "manifest.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["task_name", "task_id", "status", "before", "after"])
        w.writerows(rows)
    with open(os.path.join(a.out, "fixed_task_names.txt"), "w") as f:
        f.write("\n".join(r[0] for r in rows if r[2] == "patched") + "\n")
    print(f"\nDONE. {dict(ct)}\nout: {a.out}\nmanifest: {a.out}/manifest.csv", flush=True)


if __name__ == "__main__":
    main()
