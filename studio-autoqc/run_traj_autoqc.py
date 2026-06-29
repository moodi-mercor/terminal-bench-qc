#!/usr/bin/env python3
"""Run the Verifier-Audit (trajectory) AutoQC module over a list of trajectory_ids.

Same resumable trigger->collect->report shape as run_autoqc_full.py, but
subject_kind=trajectory. The module judges, per trajectory:
  - Verifier False Negative (a correct solution the verifier failed)
  - Verifier False Positive (an incorrect solution the verifier passed)
  - Score / Status Consistency
A FAIL on any dim = a verifier defect surfaced by a real rollout.

Usage:
  python run_traj_autoqc.py --ids-file _local/traj_audit/sample_fn300.txt          # trigger
  python run_traj_autoqc.py --ids-file ... --collect                               # poll/collect
  python run_traj_autoqc.py --ids-file ... --report                                # summarize
  python run_traj_autoqc.py --ids-file ... --out _local/traj_audit/fn_full         # custom dir
"""
import argparse
import collections
import json
import os
import sys
import time
import requests

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
API = "https://api.studio.mercor.com"
SPEC = "qcspec_ece2ca798fd2580188abd82c"  # verifier_audit_trajectory


def key():
    for line in open(f"{ROOT}/.env"):
        if line.startswith("RLS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("no RLS_KEY")


H = {"Authorization": f"Bearer {key()}", "X-Campaign-Id": "camp_4e196b1414a1499db54b43233104b0a7",
     "X-Company-Id": "comp_2fa4115109d741cd94a3c409ed89e61f",
     "X-Account-Id": "acct_85b680d4c5ba49a29f19c173672aebea",
     "User-Agent": "curl/8.7.1", "Content-Type": "application/json"}


def verdict(outcome):
    gp, dims = None, []
    if isinstance(outcome, dict):
        gp = outcome.get("global_pass")
        for sec in (outcome.get("sections") or []):
            for d in (sec.get("dimensions") or []):
                dims.append(((d.get("dimension") or d.get("name") or d.get("key") or ""),
                             str(d.get("status", "")).lower()))
    return gp, dims


def trigger(ids, out):
    os.makedirs(out, exist_ok=True)
    log = f"{out}/triggered.txt"
    done = set(l.strip() for l in open(log)) if os.path.exists(log) else set()
    todo = [t for t in ids if t not in done]
    print(f"trigger: {len(done)} fired, {len(todo)} to go", flush=True)
    with open(log, "a") as f:
        for i, tid in enumerate(todo):
            try:
                requests.post(f"{API}/qc-audits/", headers=H, timeout=60,
                              data=json.dumps({"qc_spec_id": SPEC, "subject_kind": "trajectory",
                                               "subject_id": tid, "source": "automatic",
                                               "subject_params": None}))
                f.write(tid + "\n"); f.flush()
            except Exception as e:
                print(f"  fail {tid}: {e}", flush=True); continue
            if (i + 1) % 100 == 0:
                print(f"  triggered {i+1}/{len(todo)}", flush=True)
            time.sleep(0.12)
    print(f"trigger done: {len(done)+len(todo)} total", flush=True)


def collect(ids, out):
    os.makedirs(out, exist_ok=True)
    res = f"{out}/results.jsonl"
    have = set()
    if os.path.exists(res):
        for l in open(res):
            try:
                have.add(json.loads(l)["trajectory_id"])
            except Exception:
                pass
    todo = [t for t in ids if t not in have]
    print(f"collect: {len(have)} done, {len(todo)} pending", flush=True)
    got = 0
    with open(res, "a") as f:
        for i, tid in enumerate(todo):
            try:
                d = requests.get(f"{API}/qc-audits/", headers=H, timeout=45,
                                 params={"subject_kind": "trajectory", "subject_id": tid,
                                         "qc_spec_id": SPEC}).json()
            except Exception:
                continue
            rows = d.get("audits", d if isinstance(d, list) else [])
            row = rows[0] if rows else None
            st = (row or {}).get("status")
            if st and st not in ("pending", "queued", "running", "in_progress"):
                gp, dims = verdict(row.get("outcome") or row.get("result") or {})
                f.write(json.dumps({"trajectory_id": tid, "status": st,
                                    "global_pass": gp, "dims": dims}) + "\n")
                f.flush(); got += 1
            if (i + 1) % 100 == 0:
                print(f"  scanned {i+1}/{len(todo)}, new {got}", flush=True)
            time.sleep(0.05)
    print(f"collect pass: +{got} (total {len(have)+got})", flush=True)


def report(out):
    res = f"{out}/results.jsonl"
    rows = [json.loads(l) for l in open(res)] if os.path.exists(res) else []
    fail = collections.Counter(); fail_t = 0
    for r in rows:
        f = [dn for dn, s in r["dims"] if s == "fail"]
        if f:
            fail_t += 1; fail.update(f)
    n = len(rows)
    print(f"TRAJ-AUDIT complete: {n}")
    print(f"  FAIL (>=1 verifier defect): {fail_t}/{n} ({100*fail_t/max(1,n):.1f}%)")
    print(f"  by dim: {json.dumps(dict(fail.most_common()), indent=1)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids-file", required=True)
    ap.add_argument("--out", default=f"{ROOT}/_local/traj_audit/run")
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    ids = [l.strip() for l in open(args.ids_file) if l.strip()]
    if args.report:
        report(args.out)
    elif args.collect:
        collect(ids, args.out)
    else:
        trigger(ids, args.out)


if __name__ == "__main__":
    main()
