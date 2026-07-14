#!/usr/bin/env python3
"""Push locally-fixed broken-oracle task files back into RL Studio, in place.

Follows the bulk-fix guide: POST /snapshots/task/{id}/update creates a NEW
immutable snapshot (keeps task_id + trajectories + eval history; fully
reversible). No delete/re-upload. Concurrent, backup + verify + resumable.

Unlike bulk_patch_conftest (one transform over one file), this uploads the
already-edited local files for a specific set of fixed tasks. For each task it
diffs candidate files against the current Studio snapshot and uploads only those
that differ (idempotent — re-run is a no-op once done).

Candidate files per task = union of:
  - fix_report.json "files_changed"
  - the standard editable set (solve.sh, test_outputs.py, test.sh, Dockerfile)
Each differing file is backed up (Studio's current bytes) before the write.

Usage:
  python upload_fixed_tasks.py --list _local/bo_upload.txt --dry-run
  python upload_fixed_tasks.py --list _local/bo_upload.txt --apply --workers 12
"""
import argparse
import json
import mimetypes
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "skills", "static-semantic-qc", "scripts")]
import studio_pull as sp        # noqa: E402
import bulk_patch_conftest as bp  # noqa: E402  (reuse env/hdr/load_done)

API = sp.API
TASKS_DIR = os.path.normpath(os.path.join(HERE, "..", "_local", "broken_oracle_export", "tasks"))
OUT = os.path.normpath(os.path.join(HERE, "..", "_local", "upload_fixed"))
STANDARD = ["solution/solve.sh", "tests/test_outputs.py", "tests/test.sh",
            "environment/Dockerfile"]


def candidate_files(task):
    """Union of fix_report files_changed + standard editable set that exist locally."""
    tdir = os.path.join(TASKS_DIR, task)
    rels = list(STANDARD)
    for rep in ("fix_report.json", "leak_fix_report.json"):
        fr = os.path.join(tdir, rep)
        if os.path.isfile(fr):
            try:
                rels += (json.load(open(fr)).get("files_changed") or [])
            except Exception:
                pass
    seen, out = set(), []
    for r in rels:
        r = r.strip().lstrip("/")
        if r and r not in seen and os.path.isfile(os.path.join(tdir, r)):
            seen.add(r)
            out.append(r)
    return out


def studio_bytes(rkey, tid, rel):
    """Current bytes of filesystem/<rel> in Studio, or None if absent."""
    fs = "filesystem/" + rel
    try:
        j = sp.get_json(f"{API}/snapshots/task/{tid}/file-url", rkey, params={"file_path": fs})
        r = requests.get(j["url"], timeout=120)
        r.raise_for_status()
        return r.content
    except Exception:
        return None


def upload(wkey, tid, rel_to_bytes):
    files = []
    for rel, data in rel_to_bytes.items():
        mime = mimetypes.guess_type(rel)[0] or "application/octet-stream"
        files.append(("files", ("filesystem/" + rel, data, mime)))
    for attempt in range(6):
        r = requests.post(f"{API}/snapshots/task/{tid}/update",
                          headers=bp.hdr(wkey), files=files, timeout=180)
        if r.status_code in (200, 201):
            return
        if r.status_code == 429:
            time.sleep(20 * (attempt + 1)); continue
        raise RuntimeError(f"update {r.status_code}: {r.text[:200]}")
    raise RuntimeError("update failed after retries (429)")


def main():
    global TASKS_DIR
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", required=True, help="file of task names, one per line")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--tasks-root", default=TASKS_DIR)
    a = ap.parse_args()
    apply = a.apply and not a.dry_run
    TASKS_DIR = a.tasks_root

    os.makedirs(a.out, exist_ok=True)
    backup_dir = os.path.join(a.out, "backup")
    state_path = os.path.join(a.out, "state.jsonl")
    rkey = bp.env("RLS_KEY")
    wkey = bp.env("RLS_WRITE_KEY")

    want = [ln.strip() for ln in open(a.list) if ln.strip() and not ln.startswith("#")]
    tasks = sp.list_tasks(rkey, sp.WORLD)
    id_of = {t.get("task_name"): t["task_id"] for t in tasks}
    missing = [n for n in want if n not in id_of]
    if missing:
        print(f"! {len(missing)} name(s) not in world: {missing}", flush=True)
    rows = [(n, id_of[n]) for n in want if n in id_of]
    done = bp.load_done(state_path)
    todo = [(n, tid) for (n, tid) in rows if done.get(n) not in ("uploaded", "nochange")]
    print(f"tasks: {len(rows)} | done: {len(done)} | TODO: {len(todo)} | "
          f"workers: {a.workers} | mode: {'APPLY' if apply else 'DRY-RUN'}", flush=True)

    lock = threading.Lock()
    sf = open(state_path, "a")
    counters = {"uploaded": 0, "nochange": 0, "verify-failed": 0, "error": 0}

    def record(name, tid, status, detail=None):
        rec = {"name": name, "id": tid, "status": status}
        if detail:
            rec["detail"] = detail if isinstance(detail, list) else str(detail)[:300]
        with lock:
            sf.write(json.dumps(rec) + "\n"); sf.flush()
            counters[status] = counters.get(status, 0) + 1

    def work(row):
        name, tid = row
        try:
            tdir = os.path.join(TASKS_DIR, name)
            changed = {}   # rel -> local bytes
            for rel in candidate_files(name):
                local = open(os.path.join(tdir, rel), "rb").read()
                cur = studio_bytes(rkey, tid, rel)
                if cur is None or cur != local:
                    changed[rel] = local
                    bpath = os.path.join(backup_dir, name, rel)
                    with lock:
                        os.makedirs(os.path.dirname(bpath), exist_ok=True)
                    open(bpath, "wb").write(cur if cur is not None else b"<absent-in-studio>\n")
            if not changed:
                record(name, tid, "nochange")
                return
            if not apply:
                record(name, tid, "uploaded", sorted(changed))  # dry-run: would-upload
                return
            upload(wkey, tid, changed)
            # verify: re-fetch each and compare
            bad = [rel for rel, data in changed.items() if studio_bytes(rkey, tid, rel) != data]
            record(name, tid, "verify-failed" if bad else "uploaded", bad or sorted(changed))
        except Exception as e:
            record(name, tid, "error", str(e))

    t0 = time.time(); n = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(work, r) for r in todo]
        for _ in as_completed(futs):
            n += 1
            if n % 5 == 0 or n == len(todo):
                print(f"  {n}/{len(todo)} | "
                      + " ".join(f"{k}={v}" for k, v in counters.items() if v), flush=True)
    sf.close()
    print(f"\nDONE ({'APPLY' if apply else 'DRY-RUN'}) in {(time.time()-t0)/60:.1f} min. "
          + " ".join(f"{k}={v}" for k, v in counters.items()), flush=True)
    print(f"backups: {backup_dir}\nstate: {state_path}", flush=True)


if __name__ == "__main__":
    main()
