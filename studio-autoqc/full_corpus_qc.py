#!/usr/bin/env python3
"""Full-corpus static QC + bucket labeling over an entire RL Studio world.

For every task in the world: pull its filesystem snapshot -> run all Layer-1 static
gates + reconcile + bucketize -> PATCH the task's custom_fields with qc_* (merge-safe)
-> delete the local tree (disk-safe). Resumable via a done-log; throttled; per-task
errors are logged and skipped (never aborts the run). Read uses RLS_KEY; writes use
RLS_WRITE_KEY.

Usage:
    python full_corpus_qc.py --out _local/full_qc --run-tag 2026-06-25-static          # dry-run
    python full_corpus_qc.py --out _local/full_qc --run-tag 2026-06-25-static --apply   # label
    python full_corpus_qc.py ... --limit 20    # small test batch
"""
import argparse
import json
import os
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
L1 = os.path.normpath(os.path.join(HERE, "..", "skills", "static-semantic-qc", "scripts"))
SH = os.path.normpath(os.path.join(HERE, "..", "shared"))
sys.path[:0] = [L1, SH]

import requests
import studio_pull
import aggregate
import check_structure, check_metadata, check_leakage, check_reward_hack
import check_env_fairness, check_portability, check_dockerfile, check_instructions
import check_verifier_defenses, check_security

GATES = [check_structure, check_metadata, check_leakage, check_reward_hack,
         check_env_fairness, check_portability, check_dockerfile, check_instructions,
         check_verifier_defenses, check_security]
API = studio_pull.API
ROLLUP = {"ship": "passing", "fixable": "needs-fixing",
          "total": "defective-hard", "review": "needs-review"}


def write_key():
    if os.environ.get("RLS_WRITE_KEY"):
        return os.environ["RLS_WRITE_KEY"]
    for line in open(os.path.join(HERE, "..", ".env")):
        if line.startswith("RLS_WRITE_KEY="):
            return line.split("=", 1)[1].strip()
    sys.exit("RLS_WRITE_KEY not set")


def wheaders(k):
    return {"Authorization": f"Bearer {k}",
            "X-Campaign-Id": "camp_4e196b1414a1499db54b43233104b0a7",
            "X-Company-Id": "comp_2fa4115109d741cd94a3c409ed89e61f",
            "Content-Type": "application/json"}


def qc_one(name, root):
    """Run all static gates + reconcile + bucketize for one task dir."""
    findings = []
    for mod in GATES:
        try:
            findings += mod.check_task(name, root)
        except Exception as e:
            findings.append({"task": name, "area": "structure", "severity": "WARN",
                             "title": "qc-gate-error", "detail": f"{mod.__name__}: {e}"})
    findings, _ = aggregate.reconcile(findings)
    return aggregate.bucketize(findings)


def label(tid, bk, wk, run_tag, h):
    cur = requests.get(f"{API}/tasks/{tid}", headers=h, timeout=60).json()
    cf = cur.get("custom_fields") or {}
    want = {"qc_bucket": bk["bucket"], "qc_status": bk["status"] or ROLLUP.get(bk["bucket"], ""),
            "qc_remediation": bk["remediation"], "qc_priority": bk["priority"],
            "qc_confidence": bk["confidence"], "qc_run": run_tag}
    if all(cf.get(k) == v for k, v in want.items()):
        return "skipped"
    r = requests.patch(f"{API}/tasks/{tid}", headers=h,
                       data=json.dumps({"custom_fields": {**cf, **want}}), timeout=60)
    return "ok" if r.status_code == 200 else f"http{r.status_code}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="_local/full_qc")
    ap.add_argument("--world", default=studio_pull.WORLD)
    ap.add_argument("--run-tag", default="static")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--delay", type=float, default=0.1)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    work = os.path.join(args.out, "work"); os.makedirs(work, exist_ok=True)
    done_path = os.path.join(args.out, "done.txt")
    results_path = os.path.join(args.out, "results.csv")
    done = set(l.strip() for l in open(done_path)) if os.path.exists(done_path) else set()

    rkey = studio_pull.load_key()
    wk = write_key(); h = wheaders(wk)
    tasks = studio_pull.list_tasks(rkey, args.world)
    todo = [t for t in tasks if t.get("task_name") not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"world {args.world}: {len(tasks)} tasks, {len(done)} already done, "
          f"{len(todo)} to process. mode={'APPLY' if args.apply else 'DRY-RUN'}", flush=True)

    new_results = not os.path.exists(results_path)
    rf = open(results_path, "a")
    if new_results:
        rf.write("task,bucket,status,remediation,priority,confidence,label\n")
    counts = {"labeled": 0, "skipped": 0, "fail": 0}
    bk_hist = {}
    lock = threading.Lock()
    progress = {"n": 0}

    def handle(t):
        name = t.get("task_name"); tid = t["task_id"]
        troot = os.path.join(work, name)
        try:
            studio_pull.pull_task(rkey, t, work)
            bk = qc_one(name, troot)
            res = "dry"
            if args.apply:
                res = label(tid, bk, wk, args.run_tag, h)
            with lock:
                bk_hist[bk["bucket"]] = bk_hist.get(bk["bucket"], 0) + 1
                counts["skipped" if res == "skipped" else
                       "labeled" if res in ("ok", "dry") else "fail"] += 1
                rf.write(f"{name},{bk['bucket']},{bk['status']},{bk['remediation']},"
                         f"{bk['priority']},{bk['confidence']},{res}\n"); rf.flush()
                with open(done_path, "a") as df:
                    df.write(name + "\n")
        except Exception as e:
            with lock:
                counts["fail"] += 1
            print(f"  [fail] {name}: {e}", flush=True)
        finally:
            shutil.rmtree(troot, ignore_errors=True)
            with lock:
                progress["n"] += 1
                if progress["n"] % 200 == 0:
                    print(f"  {progress['n']}/{len(todo)} | buckets={bk_hist} | {counts}", flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(handle, t) for t in todo]
        for _ in as_completed(futs):
            pass
    rf.close()
    print(f"DONE. processed {len(todo)} | buckets={bk_hist} | {counts}", flush=True)


if __name__ == "__main__":
    main()
