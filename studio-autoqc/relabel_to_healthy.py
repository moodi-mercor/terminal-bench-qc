#!/usr/bin/env python3
"""Relabel verified-good QC tasks -> healthy-unknown-difficulty in RL Studio.

Reads a {task_name: task_id} json (default _local/relabel_ids.json) of tasks we've
fixed-and-verified or confirmed-sound, and PATCHes their custom_fields to the healthy
bucket. Read-modify-write: GET the task, merge {**existing, **changes}, PATCH — never
drops other fields. Resumable (state.jsonl), concurrent, verify-after-write.

Sets: qc_final_bucket=healthy-unknown-difficulty, qc_oracle=pass, qc_remediation='',
qc_status=healthy, qc_relabel=qc-resolved-2026-07-08. Everything else preserved.

Usage:
  python relabel_to_healthy.py --dry-run
  python relabel_to_healthy.py --apply --workers 12
"""
import argparse, json, os, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "skills", "static-semantic-qc", "scripts")]
import studio_pull as sp          # noqa: E402
import bulk_patch_conftest as bp  # noqa: E402  (env/hdr/load_done)

API = sp.API
IDS = os.path.normpath(os.path.join(HERE, "..", "_local", "relabel_ids.json"))
OUT = os.path.normpath(os.path.join(HERE, "..", "_local", "relabel_out"))
CHANGES = {"qc_final_bucket": "healthy-unknown-difficulty", "qc_oracle": "pass",
           "qc_remediation": "", "qc_status": "healthy",
           "qc_relabel": "qc-resolved-2026-07-08"}
lock = threading.Lock()


def get_cf(rkey, tid):
    r = requests.get(f"{API}/tasks/{tid}", headers=bp.hdr(rkey), timeout=60)
    r.raise_for_status()
    return r.json().get("custom_fields") or {}


def patch_cf(wkey, tid, cf):
    for a in range(6):
        h = {**bp.hdr(wkey), "Content-Type": "application/json"}
        r = requests.patch(f"{API}/tasks/{tid}", headers=h,
                           data=json.dumps({"custom_fields": cf}), timeout=120)
        if r.status_code in (200, 201):
            return
        if r.status_code == 429:
            time.sleep(15 * (a + 1)); continue
        raise RuntimeError(f"{r.status_code}:{r.text[:150]}")
    raise RuntimeError("patch failed after retries")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default=IDS)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    apply = a.apply and not a.dry_run
    os.makedirs(a.out, exist_ok=True)
    state_path = os.path.join(a.out, "state.jsonl")
    rkey = bp.env("RLS_KEY"); wkey = bp.env("RLS_WRITE_KEY") if apply else None
    ids = json.load(open(a.ids))
    done = bp.load_done(state_path)
    todo = [(n, i) for n, i in ids.items() if done.get(n) not in ("relabeled", "already")]
    print(f"relabel set: {len(ids)} | done: {len(done)} | TODO: {len(todo)} | "
          f"mode: {'APPLY' if apply else 'DRY-RUN'}", flush=True)
    sf = open(state_path, "a"); counts = {}
    def rec(n, st):
        with lock:
            sf.write(json.dumps({"name": n, "status": st}) + "\n"); sf.flush()
            counts[st] = counts.get(st, 0) + 1
    sample = []
    def work(item):
        n, tid = item
        try:
            cf = get_cf(rkey, tid)
            if cf.get("qc_final_bucket") == CHANGES["qc_final_bucket"]:
                rec(n, "already"); return
            merged = {**cf, **CHANGES}
            if not apply:
                with lock:
                    if len(sample) < 8:
                        sample.append(f"  {n}: {cf.get('qc_final_bucket')} -> {merged['qc_final_bucket']} "
                                      f"(preserve {len(cf)} fields)")
                rec(n, "would-relabel"); return
            patch_cf(wkey, tid, merged)
            chk = get_cf(rkey, tid)
            rec(n, "relabeled" if chk.get("qc_final_bucket") == CHANGES["qc_final_bucket"] else "verify-failed")
        except Exception as e:
            rec(n, "error")
            with lock: print(f"  ! {n}: {e}", flush=True)
    t0 = time.time(); k = 0
    with ThreadPoolExecutor(a.workers) as ex:
        futs = [ex.submit(work, it) for it in todo]
        for _ in as_completed(futs):
            k += 1
            if k % 100 == 0 or k == len(todo):
                print(f"  {k}/{len(todo)} {counts}", flush=True)
    sf.close()
    for s in sample: print(s)
    print(f"DONE ({'APPLY' if apply else 'DRY-RUN'}) in {(time.time()-t0)/60:.1f}m {counts}", flush=True)


if __name__ == "__main__":
    main()
