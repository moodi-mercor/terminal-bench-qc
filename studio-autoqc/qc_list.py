#!/usr/bin/env python3
"""Run all Layer-1 static QC gates over a CSV list of tasks (dry-run, no labeling).

Pulls each task's filesystem snapshot, runs every static gate + reconcile +
bucketize with the CURRENT (hardened) detectors, writes per-task buckets and
findings, then deletes the local tree. Read-only against Studio.

Usage:
    python qc_list.py --csv ../_local/hard_passing_best300.csv --out ../_local/qc_best300
"""
import argparse, csv, os, shutil, sys, threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
L1 = os.path.normpath(os.path.join(HERE, "..", "skills", "static-semantic-qc", "scripts"))
SH = os.path.normpath(os.path.join(HERE, "..", "shared"))
sys.path[:0] = [L1, SH]

import studio_pull, aggregate
import check_structure, check_metadata, check_leakage, check_reward_hack
import check_env_fairness, check_portability, check_dockerfile, check_instructions
import check_verifier_defenses, check_security

GATES = [check_structure, check_metadata, check_leakage, check_reward_hack,
         check_env_fairness, check_portability, check_dockerfile, check_instructions,
         check_verifier_defenses, check_security]


def qc_one(name, root):
    findings = []
    for mod in GATES:
        try:
            findings += mod.check_task(name, root)
        except Exception as e:
            findings.append({"task": name, "area": "structure", "severity": "WARN",
                             "title": "qc-gate-error", "detail": f"{mod.__name__}: {e}"})
    findings, _ = aggregate.reconcile(findings)
    return aggregate.bucketize(findings), findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    work = os.path.join(args.out, "work"); os.makedirs(work, exist_ok=True)

    with open(args.csv) as f:
        rows = [r for r in csv.DictReader(f)]
    # map task_id -> full task dict (need snapshot pull metadata)
    rkey = studio_pull.load_key()
    allt = {t["task_id"]: t for t in studio_pull.list_tasks(rkey, studio_pull.WORLD)}
    todo = []
    for r in rows:
        t = allt.get(r["task_id"])
        if t:
            todo.append(t)
        else:
            print(f"  [warn] not in world: {r['task_name']} {r['task_id']}", flush=True)
    print(f"{len(todo)}/{len(rows)} tasks resolved", flush=True)

    rf = open(os.path.join(args.out, "results.csv"), "w")
    rf.write("task,bucket,status,priority,confidence,n_fail,n_warn\n")
    ff = open(os.path.join(args.out, "findings.csv"), "w")
    ff.write("task,area,severity,priority,title,detail\n")
    bk_hist, sev_hist, fail_titles = Counter(), Counter(), Counter()
    lock = threading.Lock(); prog = {"n": 0}

    def handle(t):
        name = t.get("task_name"); troot = os.path.join(work, name)
        try:
            studio_pull.pull_task(rkey, t, work)
            bk, findings = qc_one(name, troot)
            flagged = [f for f in findings if f.get("severity") in ("FAIL", "WARN")]
            nf = sum(1 for f in flagged if f["severity"] == "FAIL")
            nw = sum(1 for f in flagged if f["severity"] == "WARN")
            with lock:
                bk_hist[bk["bucket"]] += 1
                rf.write(f"{name},{bk['bucket']},{bk['status']},{bk['priority']},"
                         f"{bk['confidence']},{nf},{nw}\n"); rf.flush()
                for f in flagged:
                    sev_hist[f["severity"]] += 1
                    if f["severity"] == "FAIL":
                        fail_titles[f.get("title", "")] += 1
                    d = (f.get("detail", "") or "").replace("\n", " ").replace(",", ";")[:200]
                    ff.write(f"{name},{f.get('area','')},{f['severity']},"
                             f"{aggregate.priority_of(f)},{f.get('title','')},{d}\n")
                ff.flush()
        except Exception as e:
            print(f"  [fail] {name}: {e}", flush=True)
        finally:
            shutil.rmtree(troot, ignore_errors=True)
            with lock:
                prog["n"] += 1
                if prog["n"] % 50 == 0:
                    print(f"  {prog['n']}/{len(todo)} | buckets={dict(bk_hist)} | sev={dict(sev_hist)}", flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for _ in as_completed([ex.submit(handle, t) for t in todo]):
            pass
    rf.close(); ff.close()
    print(f"\nDONE {prog['n']} tasks")
    print(f"buckets: {dict(bk_hist)}")
    print(f"severity: {dict(sev_hist)}")
    if fail_titles:
        print(f"FAIL titles: {dict(fail_titles)}")
    else:
        print("FAIL titles: none (no hard static defects)")


if __name__ == "__main__":
    main()
