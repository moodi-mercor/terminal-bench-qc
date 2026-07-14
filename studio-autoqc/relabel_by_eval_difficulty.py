#!/usr/bin/env python3
"""Relabel the (now-healthy) broken-oracle bucket into healthy-* buckets, with
difficulty grounded in the Opus-4.8 avg@8 eval (batch_9ba18ba4...), not the stale
static difficulty field.

Scope: every task in the export manifest EXCEPT the deleted culls (bo_cull.txt).
That's the ~2,507 healthy + 16-fixed tasks, all still bucketed 'broken-oracle'.

Difficulty (repo convention: hard == low solve rate):
    avg@8 <= 0.5  -> healthy-hard
    avg@8 >  0.5  -> healthy-easy
    no eval runs  -> healthy-unknown-difficulty
Per task also sets: qc_oracle='pass', qc_regate='modal-ok-2026-07-07',
qc_avg_at_8=<rounded avg>, qc_difficulty_source='opus48-avg8'. READ-MODIFY-WRITE
merge-safe (only moves tasks still 'broken-oracle'); concurrent; resumable; verify.

Usage:
  python relabel_by_eval_difficulty.py --dry-run
  python relabel_by_eval_difficulty.py --apply --workers 12
"""
import argparse
import csv
import json
import os
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "skills", "static-semantic-qc", "scripts")]
import studio_pull as sp                    # noqa: E402
import relabel_healthy_oracle as rl         # noqa: E402  (env/hdr/get_task/patch_custom_fields)

API = sp.API
WORLD = sp.WORLD
EXPORT = os.path.normpath(os.path.join(HERE, "..", "_local", "broken_oracle_export"))
MANIFEST = os.path.join(EXPORT, "manifest.csv")
CULL_F = os.path.normpath(os.path.join(HERE, "..", "_local", "bo_cull.txt"))
OUT = os.path.normpath(os.path.join(HERE, "..", "_local", "relabel_eval"))
SCORES_F = os.path.join(OUT, "avg8_scores.json")
BATCH = "batch_9ba18ba4ec6e4acaa4ea7fccdfa4c0e5"
REGATE_TAG = "modal-ok-2026-07-07"
EXPECT_FROM = "broken-oracle"
HARD_THRESHOLD = 0.5
CHUNK = 8000


def pull_scores(rkey):
    """Aggregate avg score per task_id over the batch. Cached to SCORES_F."""
    if os.path.isfile(SCORES_F):
        return json.load(open(SCORES_F))
    agg = defaultdict(lambda: {"runs": 0, "sum": 0.0})
    off = 0
    while True:
        sql = ("SELECT task_id AS tid, trajectory_output->>'score' AS score "
               f"FROM trajectories WHERE trajectory_batch_id='{BATCH}' "
               f"ORDER BY trajectory_id LIMIT {CHUNK} OFFSET {off}")
        r = requests.post(f"{API}/querier/unstructured", headers=rl.hdr(rkey),
                          json={"query": sql}, timeout=300)
        r.raise_for_status()
        rows = r.json().get("rows", [])
        if not rows:
            break
        for row in rows:
            sc = row.get("score")
            if sc is None:
                continue
            try:
                v = float(sc)
            except (TypeError, ValueError):
                continue
            a = agg[row["tid"]]
            a["runs"] += 1
            a["sum"] += v
        if len(rows) < CHUNK:
            break
        off += CHUNK
        print(f"  scanned {off}...", flush=True)
    out = {tid: {"runs": a["runs"], "avg": (a["sum"] / a["runs"] if a["runs"] else None)}
           for tid, a in agg.items()}
    os.makedirs(OUT, exist_ok=True)
    json.dump(out, open(SCORES_F, "w"))
    return out


STATIC_MAP = {"easy": "healthy-easy", "medium": "healthy-hard", "hard": "healthy-hard"}


def bucket_for(avg, static_diff=""):
    """Eval-grounded when we have runs; else fall back to the static difficulty."""
    if avg is not None:
        return "healthy-hard" if avg <= HARD_THRESHOLD else "healthy-easy"
    d = (static_diff or "").strip().lower()
    return STATIC_MAP.get(d, "healthy-unknown-difficulty")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--scored-only", action="store_true",
                    help="only relabel tasks that HAVE an eval avg@8 score (leave the rest)")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    do_write = a.apply and not a.dry_run
    mode = "APPLY" if do_write else "DRY-RUN"

    os.makedirs(a.out, exist_ok=True)
    state_path = os.path.join(a.out, "state.jsonl")
    rkey = rl.env("RLS_KEY")
    wkey = rl.env("RLS_WRITE_KEY") if do_write else None

    cull = {l.strip() for l in open(CULL_F)} if os.path.isfile(CULL_F) else set()
    rows = [r for r in csv.DictReader(open(MANIFEST))
            if r["task_name"] not in cull and r.get("task_id")]
    scores = pull_scores(rkey)
    if a.scored_only:
        rows = [r for r in rows if (scores.get(r["task_id"]) or {}).get("avg") is not None]
        print(f"--scored-only: restricted to {len(rows)} tasks with eval scores", flush=True)
    print(f"scored task_ids: {len(scores)} | tasks to relabel: {len(rows)} | mode: {mode}", flush=True)

    # preview bucket distribution
    bc = Counter(); src = Counter()
    for r in rows:
        s = scores.get(r["task_id"])
        avg = s["avg"] if s else None
        bc[bucket_for(avg, r.get("difficulty"))] += 1
        src["eval" if avg is not None else "static"] += 1
    print(f"difficulty source: eval={src['eval']} static-fallback={src['static']}")
    print("=== bucket distribution (eval where available, static fallback) ===")
    for b, c in bc.most_common():
        print(f"  {b:<28} {c}")
    print(f"  {'TOTAL':<28} {sum(bc.values())}")

    done = rl.load_done(state_path)
    todo = [r for r in rows if done.get(r["task_name"]) not in
            ("relabeled", "skipped-not-broken", "missing")]
    print(f"already done: {len(done)} | TODO: {len(todo)}", flush=True)
    if not do_write:
        # show 8 samples
        for r in rows[:8]:
            s = scores.get(r["task_id"]); avg = s["avg"] if s else None
            print(f"  [dry] {r['task_name']}: avg@8={avg if avg is None else round(avg,3)} "
                  f"runs={s['runs'] if s else 0} static={r.get('difficulty')} -> {bucket_for(avg, r.get('difficulty'))}")
        print("NO WRITES PERFORMED.")
        return

    lock = threading.Lock()
    sf = open(state_path, "a")
    cnt = Counter()

    def rec(name, tid, status, extra=None):
        d = {"name": name, "id": tid, "status": status}
        if extra:
            d.update(extra)
        with lock:
            sf.write(json.dumps(d) + "\n"); sf.flush(); cnt[status] += 1

    def work(r):
        name, tid = r["task_name"], r["task_id"]
        s = scores.get(tid); avg = s["avg"] if s else None
        b = bucket_for(avg, r.get("difficulty"))
        try:
            cur = rl.get_task(rkey, tid)
            cf = cur.get("custom_fields") or {}
            if cf.get("qc_final_bucket") != EXPECT_FROM:
                rec(name, tid, "skipped-not-broken", {"found": cf.get("qc_final_bucket")})
                return
            changes = {"qc_final_bucket": b, "qc_oracle": "pass", "qc_regate": REGATE_TAG,
                       "qc_avg_at_8": (None if avg is None else round(avg, 3)),
                       "qc_difficulty_source": ("opus48-avg8" if avg is not None else "static")}
            rl.patch_custom_fields(wkey, tid, {**cf, **changes})
            chk = rl.get_task(rkey, tid).get("custom_fields") or {}
            ok = chk.get("qc_final_bucket") == b and chk.get("qc_regate") == REGATE_TAG
            rec(name, tid, "relabeled" if ok else "verify-failed", {"to": b})
        except Exception as e:
            rec(name, tid, "error", {"err": str(e)[:200]})

    t0 = time.time(); n = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(work, r) for r in todo]
        for _ in as_completed(futs):
            n += 1
            if n % 100 == 0 or n == len(todo):
                rate = n / max(1e-9, time.time() - t0) * 60
                print(f"  {n}/{len(todo)} | "
                      + " ".join(f"{k}={v}" for k, v in cnt.items()) + f" | {rate:.0f}/min", flush=True)
    sf.close()
    print(f"\nDONE (APPLY) in {(time.time()-t0)/60:.1f} min. "
          + " ".join(f"{k}={v}" for k, v in cnt.items()), flush=True)
    print(f"state: {state_path}")


if __name__ == "__main__":
    main()
