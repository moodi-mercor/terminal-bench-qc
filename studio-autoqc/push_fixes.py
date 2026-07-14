#!/usr/bin/env python3
"""Write the QC fixes back to Studio as new immutable snapshots.

For each fixed task: pull its CURRENT snapshot files, diff against the locally-fixed
tree, and POST the changed + new files (EXCLUDING solution/solve.sh and .pull_done)
via POST /snapshots/task/{id}/update. Old snapshots are preserved -> reversible.

Dry-run by default (shows the per-task upload set). Pass --apply to POST.
Concurrent workers with backoff for the server's update rate cap.
"""
import json, os, sys, time, difflib
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

API = "https://api.studio.mercor.com"
KEY = "rls-sk-iuA_SCYWiui2xXr_HD7Rab0BfF5VPmDNlK6eAyLzcYw"
CAMP = "camp_0c1f9a9809604271a534edd77c3cbec1"
COMP = "comp_2fa4115109d741cd94a3c409ed89e61f"
ACCT = "acct_85b680d4c5ba49a29f19c173672aebea"
ROOT = "/Users/mahmoodmapara/Desktop/terminal-bench-qc"
TASKS = f"{ROOT}/_local/gemini_flash_qc/tasks"
IDS = f"{ROOT}/_local/gemini_flash_qc/fixed_task_ids.json"
STATE = f"{ROOT}/_local/gemini_flash_qc/push_state.jsonl"
EXCLUDE = {"solution/solve.sh"}          # don't push oracle un-neutralization
SKIP_SUFFIX = (".pull_done", ".realbak", ".orig")
H = {"Authorization": f"Bearer {KEY}", "X-Campaign-Id": CAMP, "X-Company-Id": COMP,
     "X-Account-Id": ACCT, "User-Agent": "curl/8.7.1"}
WORKERS = 4


def gj(url, **kw):
    for i in range(5):
        try:
            r = requests.get(url, headers=H, timeout=90, **kw)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(1.5 * (i + 1))
    return None


def current_files(tid):
    """Map rel-path -> current snapshot bytes (rel is rooted below filesystem/)."""
    data = gj(f"{API}/snapshots/task/{tid}/input-files")
    out = {}
    for f in (data or {}).get("files", []):
        raw = f["key"]
        idx = raw.find("filesystem/")
        fs = raw[idx:] if idx >= 0 else raw
        rel = fs[len("filesystem/"):] if fs.startswith("filesystem/") else fs
        if not rel or rel.endswith("/"):
            continue
        j = gj(f"{API}/snapshots/task/{tid}/file-url", params={"file_path": fs})
        if not j or "url" not in j:
            continue
        try:
            r = requests.get(j["url"], timeout=120); r.raise_for_status()
            out[rel] = r.content
        except Exception:
            pass
    return out


def local_files(name):
    base = os.path.join(TASKS, name)
    out = {}
    for dp, _, fns in os.walk(base):
        for fn in fns:
            p = os.path.join(dp, fn)
            rel = os.path.relpath(p, base)
            if rel in EXCLUDE or rel.endswith(SKIP_SUFFIX):
                continue
            out[rel] = open(p, "rb").read()
    return out


def diff_set(name, tid):
    cur = current_files(tid)
    loc = local_files(name)
    changed = {rel: b for rel, b in loc.items()
               if rel not in cur or cur[rel] != b}
    return changed, cur


def upload(tid, changed):
    files = [("files", (f"filesystem/{rel}", b, "application/octet-stream"))
             for rel, b in changed.items()]
    for i in range(6):
        r = requests.post(f"{API}/snapshots/task/{tid}/update", headers=H,
                          files=files, timeout=180)
        if r.status_code in (200, 201):
            return True, r.status_code
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(13 * (i + 1)); continue
        return False, f"{r.status_code}:{r.text[:120]}"
    return False, "retries-exhausted"


def main():
    apply = "--apply" in sys.argv
    ids = json.load(open(IDS))
    done = set()
    if os.path.exists(STATE):
        for l in open(STATE):
            d = json.loads(l)
            if d.get("ok"):
                done.add(d["task"])
    todo = {t: i for t, i in ids.items() if t not in done}
    print(f"{'APPLY' if apply else 'DRY-RUN'} | {len(todo)} tasks to push ({len(done)} done)\n")

    def work(name, tid):
        changed, cur = diff_set(name, tid)
        return name, tid, changed

    results = {}
    with ThreadPoolExecutor(WORKERS) as ex:
        futs = {ex.submit(work, t, i): t for t, i in todo.items()}
        for fut in as_completed(futs):
            name, tid, changed = fut.result()
            results[name] = (tid, changed)
            print(f"  {name}: {len(changed)} file(s) -> {sorted(changed)}")

    if not apply:
        print("\nDRY-RUN complete. Re-run with --apply to upload.")
        return

    print("\nUploading...")
    sf = open(STATE, "a")
    with ThreadPoolExecutor(WORKERS) as ex:
        futs = {ex.submit(upload, tid, changed): name
                for name, (tid, changed) in results.items() if changed}
        for fut in as_completed(futs):
            name = futs[fut]
            ok, info = fut.result()
            sf.write(json.dumps({"task": name, "ok": ok, "info": info}) + "\n"); sf.flush()
            print(f"  [{'OK' if ok else 'FAIL'}] {name}: {info}")
    print("done.")


if __name__ == "__main__":
    main()
