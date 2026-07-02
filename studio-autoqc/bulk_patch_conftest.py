#!/usr/bin/env python3
"""Bulk-patch the conftest-plant vulnerability on every qc_conftest_vulnerable task.

Source of truth = the "QC ▸ Conftest-plant vulnerable (cross-cutting)" CQV:
  SELECT ... FROM tasks WHERE ... custom_fields->>'qc_conftest_vulnerable'='true'

For each task it fetches tests/test.sh, inserts `--noconftest` into the pytest
grader line, and uploads a NEW immutable task snapshot via
POST /snapshots/task/{id}/update (old snapshots are preserved -> reversible).

Safety wrapper (per sign-off 2026-07-01):
  * Backs up every original tests/test.sh to <out>/backup/<task>/test.sh before writing.
  * Skips tasks already guarded (--noconftest / --confcutdir present) -> no-op.
  * Idempotent + resumable: appends per-task result to <out>/state.jsonl and skips
    tasks already recorded done/skipped on restart.
  * Verifies after upload that --noconftest is present in the new snapshot.
  * Rate-limited to the server's 5/min cap on the update endpoint (12.5s/write).

Usage:
  python bulk_patch_conftest.py --dry-run            # fetch+patch+backup, NO writes
  python bulk_patch_conftest.py --limit 25 --apply   # patch first 25 (validation batch)
  python bulk_patch_conftest.py --apply              # full rollout (~30h at 5/min)
"""
import argparse
import json
import os
import re
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "skills", "static-semantic-qc", "scripts")]
import studio_pull as sp  # noqa: E402

API = sp.API
WORLD = sp.WORLD
OUT = os.path.normpath(os.path.join(HERE, "..", "_local", "conftest_bulk"))
CQV_SQL = (
    "SELECT task_name, task_id FROM tasks "
    f"WHERE world_id='{WORLD}' AND archived_at IS NULL AND is_latest=TRUE "
    "AND custom_fields->>'qc_conftest_vulnerable'='true' ORDER BY task_name"
)
INVOKE = re.compile(r'(\s*)(python3?\s+-m\s+pytest|pytest)(\s)', re.M)
WRITE_INTERVAL = 12.5  # 5/min server cap -> stay just under


def env(name):
    v = os.environ.get(name)
    if v:
        return v
    for line in open(os.path.join(HERE, "..", ".env")):
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit(f"no {name} in env/.env")


def hdr(key):
    return {"Authorization": f"Bearer {key}",
            "X-Campaign-Id": sp.CAMPAIGN, "X-Company-Id": sp.COMPANY}


def is_grader(line):
    s = line.strip()
    if s.startswith("#") or "import pytest" in s or "command -v" in s:
        return False
    if re.search(r'\b(pip3?|uv)\b.*\binstall\b', s):
        return False
    return bool(re.match(r'(python3?\s+-m\s+pytest|pytest)\s', s))


def patch(src):
    """Insert --noconftest into the first real pytest grader line. Returns (new, changed)."""
    if "--noconftest" in src or "--confcutdir" in src:
        return src, False
    out, changed = [], False
    for line in src.splitlines(keepends=True):
        if not changed and is_grader(line):
            line = INVOKE.sub(r'\1\2 --noconftest\3', line, count=1)
            changed = True
        out.append(line)
    return "".join(out), changed


def fetch_testsh(rkey, tid):
    """Return (fs_path, text) for the graded filesystem/tests/test.sh, or (None, None).

    Snapshot keys are `tasks/<snap>/filesystem/tests/test.sh`; the file-url endpoint
    wants the path rooted at `filesystem/`. Target that exact file (ignore any stray
    top-level `tests/test.sh`).
    """
    files = sp.snapshot_files(rkey, tid)
    cand = [f for f in files if f["key"].rstrip("/").endswith("filesystem/tests/test.sh")]
    if not cand:
        return None, None
    raw = cand[0]["key"]
    idx = raw.find("filesystem/")
    fs = raw[idx:] if idx >= 0 else raw
    j = sp.get_json(f"{API}/snapshots/task/{tid}/file-url", rkey, params={"file_path": fs})
    txt = requests.get(j["url"], timeout=120).text
    return fs, txt


def upload_testsh(wkey, tid, content):
    """Upload the patched grader to filesystem/tests/test.sh as a new snapshot.

    Uploaded filenames are snapshot-relative, so the path must include the
    `filesystem/` prefix to overwrite the real graded file. Also strips any stray
    top-level `tests/test.sh` (no-op when absent). Retries 429 with backoff.
    """
    for attempt in range(6):
        r = requests.post(
            f"{API}/snapshots/task/{tid}/update",
            headers=hdr(wkey),
            files=[("files", ("filesystem/tests/test.sh", content.encode(), "text/x-sh"))],
            data={"remove_files": "tests/test.sh"},
            timeout=180,
        )
        if r.status_code in (200, 201):
            return r.json()
        if r.status_code == 429:
            wait = 20 * (attempt + 1)
            print(f"    429 rate-limited; backing off {wait}s", flush=True)
            time.sleep(wait)
            continue
        raise RuntimeError(f"update {r.status_code}: {r.text[:200]}")
    raise RuntimeError("update failed after retries (429)")


def load_done(state_path):
    done = {}
    if os.path.isfile(state_path):
        for line in open(state_path):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                done[rec["name"]] = rec["status"]
            except Exception:
                pass
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually upload (default dry-run)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    apply = a.apply and not a.dry_run

    os.makedirs(a.out, exist_ok=True)
    backup_dir = os.path.join(a.out, "backup")
    state_path = os.path.join(a.out, "state.jsonl")
    rkey = env("RLS_KEY")
    wkey = env("RLS_WRITE_KEY")

    # 1. resolve the vulnerable set. The /querier endpoint silently caps at 100
    #    rows, so pull the full world task list and filter locally on the exact
    #    flag the CQV uses (qc_conftest_vulnerable == "true").
    tasks = sp.list_tasks(rkey, WORLD)
    rows = sorted(
        ({"task_name": t.get("task_name"), "task_id": t["task_id"]}
         for t in tasks
         if (t.get("custom_fields") or {}).get("qc_conftest_vulnerable") == "true"
         and t.get("archived_at") is None),
        key=lambda x: x["task_name"] or "",
    )
    if a.limit:
        rows = rows[:a.limit]
    done = load_done(state_path)
    print(f"CQV vulnerable set: {len(rows)} | already recorded: {len(done)} | "
          f"mode: {'APPLY' if apply else 'DRY-RUN'}", flush=True)

    sf = open(state_path, "a")
    n_patched = n_skip_guard = n_skip_done = n_notest = n_fail = 0
    t0 = time.time()
    for i, row in enumerate(rows, 1):
        name, tid = row["task_name"], row["task_id"]
        if name in done and done[name] in ("patched", "skipped-guarded", "skipped-notest"):
            n_skip_done += 1
            continue
        try:
            fs, txt = fetch_testsh(rkey, tid)
            if txt is None:
                n_notest += 1
                sf.write(json.dumps({"name": name, "id": tid, "status": "skipped-notest"}) + "\n"); sf.flush()
                continue
            new, changed = patch(txt)
            if not changed:
                n_skip_guard += 1
                sf.write(json.dumps({"name": name, "id": tid, "status": "skipped-guarded"}) + "\n"); sf.flush()
                continue
            # backup original before any write
            bpath = os.path.join(backup_dir, name, "test.sh")
            os.makedirs(os.path.dirname(bpath), exist_ok=True)
            open(bpath, "w").write(txt)
            if not apply:
                n_patched += 1
                if n_patched <= 2:
                    ob = next(l for l in txt.splitlines() if is_grader(l)).strip()
                    nb = next(l for l in new.splitlines() if "--noconftest" in l).strip()
                    print(f"  [dry] {name}\n    - {ob}\n    + {nb}", flush=True)
                continue
            # write new snapshot
            upload_testsh(wkey, tid, new)
            # verify
            _, check = fetch_testsh(rkey, tid)
            ok = check is not None and "--noconftest" in check
            status = "patched" if ok else "verify-failed"
            if ok:
                n_patched += 1
            else:
                n_fail += 1
            sf.write(json.dumps({"name": name, "id": tid, "status": status}) + "\n"); sf.flush()
            time.sleep(WRITE_INTERVAL)
        except Exception as e:
            n_fail += 1
            sf.write(json.dumps({"name": name, "id": tid, "status": "error", "err": str(e)[:200]}) + "\n"); sf.flush()
            print(f"  [fail] {name}: {e}", flush=True)
        if i % 50 == 0:
            rate = i / max(1e-9, (time.time() - t0)) * 60
            print(f"  {i}/{len(rows)} | patched {n_patched} guarded {n_skip_guard} "
                  f"done-skip {n_skip_done} notest {n_notest} fail {n_fail} | {rate:.1f}/min", flush=True)
    sf.close()
    print(f"\nDONE ({'APPLY' if apply else 'DRY-RUN'}). patched={n_patched} "
          f"skipped-guarded={n_skip_guard} skipped-done={n_skip_done} "
          f"no-test.sh={n_notest} failed={n_fail}", flush=True)
    print(f"backups: {backup_dir}\nstate: {state_path}", flush=True)


if __name__ == "__main__":
    main()
