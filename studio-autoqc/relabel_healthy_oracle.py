#!/usr/bin/env python3
"""Relabel the 1,555 mislabeled "broken-oracle" TB tasks that are actually healthy.

Background
----------
World `world_2c7cdb23737845ad83a9acfa1aa8c25b` has 2,509 tasks bucketed
`custom_fields.qc_final_bucket == 'broken-oracle'`. We re-ran the AUTHORITATIVE
Modal oracle gate (native amd64) over 1,555 of them and they PASS cleanly
(oracle=1, no-op fails) with zero edits -- they were never actually broken.
Those 1,555 task_names live in `_local/broken_oracle_export/healthy_ok.txt`;
their difficulty is in `_local/broken_oracle_export/manifest.csv`.

This tool moves each of the 1,555 from `broken-oracle` into the existing healthy
taxonomy and records the re-gate provenance, preserving every other custom_field.

Label decision (difficulty -> qc_final_bucket)
----------------------------------------------
The three healthy buckets that exist in production are:
    healthy-easy, healthy-hard, healthy-unknown-difficulty
(there is NO healthy-medium bucket). We map from the manifest `difficulty`:
    easy    -> healthy-easy
    medium  -> healthy-hard   (no medium bucket; "not-easy" -> the hard bucket)
    hard    -> healthy-hard
    <blank> -> healthy-unknown-difficulty
Additionally set on every relabeled task:
    qc_oracle  = 'pass'
    qc_regate  = 'modal-ok-2026-07-07'   (provenance)

Write semantics
---------------
READ-MODIFY-WRITE, merge-safe: we GET the task, confirm it is still
`broken-oracle`, then PATCH `custom_fields` as {**existing, **changes} so no
other field is dropped. Mirrors apply_behavioral_labels.py's PATCH mechanism and
bulk_patch_conftest.py's resumable / rate-limited / verify-after-write safety.

Safety
------
  * --dry-run (DEFAULT): fetches + confirms + computes labels, writes NOTHING.
  * --pilot N: apply to the first N tasks only (validation batch), then stop.
  * --apply: full rollout over all 1,555.
  * Idempotent + resumable: per-task result appended to <out>/state.jsonl;
    tasks already recorded done/skipped are skipped on restart.
  * Rate-limited to the server cap (default 5/min => 12.5s/write).
  * Verify-after-write: re-GET the task and confirm the new bucket landed.
  * Aborts a write if the task is no longer 'broken-oracle' (someone else moved
    it) -- recorded as skipped-not-broken, never clobbered.

Usage
-----
  python relabel_healthy_oracle.py --dry-run          # NO writes (default)
  python relabel_healthy_oracle.py --pilot 3          # write 3 then stop
  python relabel_healthy_oracle.py --apply            # full 1,555 rollout
"""
import argparse
import csv
import json
import os
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "skills", "static-semantic-qc", "scripts")]
import studio_pull as sp  # noqa: E402

API = sp.API
WORLD = sp.WORLD
EXPORT = os.path.normpath(os.path.join(HERE, "..", "_local", "broken_oracle_export"))
HEALTHY_LIST = os.path.join(EXPORT, "healthy_ok.txt")
MANIFEST = os.path.join(EXPORT, "manifest.csv")
OUT = os.path.normpath(os.path.join(HERE, "..", "_local", "relabel_healthy_oracle"))

REGATE_TAG = "modal-ok-2026-07-07"
EXPECT_FROM = "broken-oracle"            # the only bucket we will move away from
WRITE_INTERVAL = 12.5                    # 5/min server cap -> stay just under

DIFF_TO_BUCKET = {
    "easy": "healthy-easy",
    "medium": "healthy-hard",            # no healthy-medium bucket exists
    "hard": "healthy-hard",
}
UNKNOWN_BUCKET = "healthy-unknown-difficulty"


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
            "X-Campaign-Id": sp.CAMPAIGN, "X-Company-Id": sp.COMPANY,
            "Content-Type": "application/json"}


def target_bucket(difficulty):
    d = (difficulty or "").strip().lower()
    if not d:
        return UNKNOWN_BUCKET
    return DIFF_TO_BUCKET.get(d, UNKNOWN_BUCKET)


def desired_changes(difficulty):
    """The custom_fields keys this tool sets. Everything else is preserved."""
    return {
        "qc_final_bucket": target_bucket(difficulty),
        "qc_oracle": "pass",
        "qc_regate": REGATE_TAG,
    }


def load_manifest():
    """task_name -> difficulty (lowercased)."""
    diffs = {}
    with open(MANIFEST) as f:
        for r in csv.DictReader(f):
            diffs[r["task_name"]] = (r.get("difficulty") or "").strip().lower()
    return diffs


def load_healthy_names():
    return [l.strip() for l in open(HEALTHY_LIST) if l.strip() and not l.startswith("#")]


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


def get_task(key, tid):
    r = requests.get(f"{API}/tasks/{tid}", headers=hdr(key), timeout=60)
    r.raise_for_status()
    return r.json()


def patch_custom_fields(wkey, tid, new_cf):
    """PATCH /tasks/{id} with the FULL merged custom_fields dict. Retries 429."""
    for attempt in range(6):
        r = requests.patch(f"{API}/tasks/{tid}", headers=hdr(wkey),
                           data=json.dumps({"custom_fields": new_cf}), timeout=120)
        if r.status_code in (200, 201):
            return r.json()
        if r.status_code == 429:
            wait = 20 * (attempt + 1)
            print(f"    429 rate-limited; backing off {wait}s", flush=True)
            time.sleep(wait)
            continue
        raise RuntimeError(f"patch {r.status_code}: {r.text[:200]}")
    raise RuntimeError("patch failed after retries (429)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="fetch+compute, NO writes (default)")
    ap.add_argument("--pilot", type=int, default=0, help="APPLY to only the first N tasks, then stop")
    ap.add_argument("--apply", action="store_true", help="APPLY to all 1,555 tasks")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--interval", type=float, default=WRITE_INTERVAL, help="seconds between writes")
    a = ap.parse_args()

    # Mode resolution: writes happen only when --apply or --pilot>0 is set AND
    # --dry-run is not. --dry-run always wins (safest).
    do_write = (a.apply or a.pilot > 0) and not a.dry_run
    limit = a.pilot if (a.pilot > 0 and not a.dry_run) else 0
    mode = "DRY-RUN" if not do_write else (f"PILOT({a.pilot})" if a.pilot else "APPLY")

    os.makedirs(a.out, exist_ok=True)
    state_path = os.path.join(a.out, "state.jsonl")

    rkey = env("RLS_KEY")
    wkey = env("RLS_WRITE_KEY") if do_write else None

    names = load_healthy_names()
    diffs = load_manifest()
    tasks_by_name = {t.get("task_name"): t for t in sp.list_tasks(rkey, WORLD)}
    done = load_done(state_path)

    if limit:
        names = names[:limit]

    print(f"healthy set: {len(load_healthy_names())} | processing: {len(names)} | "
          f"already recorded: {len(done)} | mode: {mode}", flush=True)

    # -------- difficulty -> bucket mapping table + counts --------
    from collections import Counter
    diff_counts = Counter()
    bucket_counts = Counter()
    for name in load_healthy_names():
        d = diffs.get(name, "")
        diff_counts[d or "<blank>"] += 1
        bucket_counts[target_bucket(d)] += 1
    print("\n=== difficulty -> qc_final_bucket mapping (full 1,555) ===")
    print(f"  {'difficulty':<12} {'-> bucket':<28} count")
    order = ["easy", "medium", "hard", "<blank>"]
    for d in order + [k for k in diff_counts if k not in order]:
        if d not in diff_counts:
            continue
        b = UNKNOWN_BUCKET if d == "<blank>" else target_bucket(d)
        print(f"  {d:<12} -> {b:<25} {diff_counts[d]}")
    print("  " + "-" * 44)
    print("  resulting bucket totals:")
    for b, c in bucket_counts.most_common():
        print(f"    {b:<28} {c}")
    print(f"    {'TOTAL':<28} {sum(bucket_counts.values())}")

    sf = open(state_path, "a")
    n_done = n_write = n_skip_done = n_skip_notbroken = n_missing = n_fail = 0
    n_dry_ok = 0
    samples_shown = 0
    all_broken = True
    t0 = time.time()

    for i, name in enumerate(names, 1):
        if name in done and done[name] in ("relabeled", "skipped-not-broken", "missing"):
            n_skip_done += 1
            continue
        t = tasks_by_name.get(name)
        if not t:
            n_missing += 1
            all_broken = False
            if do_write:
                sf.write(json.dumps({"name": name, "status": "missing"}) + "\n"); sf.flush()
            continue
        tid = t["task_id"]
        diff = diffs.get(name, "")
        changes = desired_changes(diff)
        try:
            # READ current state (live)
            cur = get_task(rkey, tid)
            cf = cur.get("custom_fields") or {}
            cur_bucket = cf.get("qc_final_bucket")

            if cur_bucket != EXPECT_FROM:
                # Never clobber: only move tasks still bucketed broken-oracle.
                n_skip_notbroken += 1
                all_broken = False
                print(f"  [skip] {name}: bucket is {cur_bucket!r}, not {EXPECT_FROM!r}", flush=True)
                if do_write:
                    sf.write(json.dumps({"name": name, "id": tid,
                                         "status": "skipped-not-broken",
                                         "found_bucket": cur_bucket}) + "\n"); sf.flush()
                continue

            merged = {**cf, **changes}   # MODIFY: merge-safe, preserves all other fields

            if not do_write:
                # DRY-RUN: print a before->after diff for ~10 sample tasks.
                n_dry_ok += 1
                if samples_shown < 10:
                    samples_shown += 1
                    before = {k: cf.get(k) for k in ("qc_final_bucket", "qc_oracle", "qc_regate")}
                    after = {k: merged.get(k) for k in ("qc_final_bucket", "qc_oracle", "qc_regate")}
                    print(f"\n  [dry {samples_shown:>2}] {name}  (difficulty={diff or '<blank>'})")
                    print(f"        before: {before}")
                    print(f"        after : {after}")
                    print(f"        (preserving {len(cf)} existing custom_fields)")
                continue

            # WRITE (pilot/apply only)
            patch_custom_fields(wkey, tid, merged)
            # VERIFY: re-read and confirm the new bucket landed
            check = get_task(rkey, tid).get("custom_fields") or {}
            ok = (check.get("qc_final_bucket") == changes["qc_final_bucket"]
                  and check.get("qc_oracle") == "pass"
                  and check.get("qc_regate") == REGATE_TAG)
            status = "relabeled" if ok else "verify-failed"
            if ok:
                n_write += 1
            else:
                n_fail += 1
            sf.write(json.dumps({"name": name, "id": tid, "status": status,
                                 "to_bucket": changes["qc_final_bucket"]}) + "\n"); sf.flush()
            time.sleep(a.interval)
        except Exception as e:
            n_fail += 1
            all_broken = False
            if do_write:
                sf.write(json.dumps({"name": name, "id": tid, "status": "error",
                                     "err": str(e)[:200]}) + "\n"); sf.flush()
            print(f"  [fail] {name}: {e}", flush=True)
        if do_write and i % 25 == 0:
            rate = i / max(1e-9, (time.time() - t0)) * 60
            print(f"  {i}/{len(names)} | relabeled {n_write} skip-not-broken "
                  f"{n_skip_notbroken} fail {n_fail} | {rate:.1f}/min", flush=True)
    sf.close()

    print(f"\nDONE ({mode}).")
    if not do_write:
        print(f"  confirmed still-broken-oracle: {n_dry_ok}/{len(names)} | "
              f"already-recorded {n_skip_done} | missing {n_missing} | "
              f"not-broken {n_skip_notbroken}")
        print(f"  all processed tasks are '{EXPECT_FROM}': "
              f"{all_broken and n_missing == 0 and n_skip_notbroken == 0}")
        print("  NO WRITES PERFORMED.")
    else:
        print(f"  relabeled={n_write} skipped-done={n_skip_done} "
              f"skipped-not-broken={n_skip_notbroken} missing={n_missing} failed={n_fail}")
    print(f"  state: {state_path}")


if __name__ == "__main__":
    main()
