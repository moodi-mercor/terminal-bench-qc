#!/usr/bin/env python3
"""Dispatch an avg@8 difficulty eval over the (now-healthy) broken-oracle bucket,
Opus-4.8 only, so the subsequent relabel can bucket by REAL solve-rate difficulty.

Task set = all tasks in the export manifest MINUS the culled ones (default:
_local/broken_oracle_export/manifest.csv minus _local/bo_cull.txt). Includes the
16 fixed tasks (now uploaded to Studio) + the ~2,491 confirmed-healthy tasks.

  ~2,507 tasks x 8 runs x 1 model = ~20,056 trajectories (< 50k hard cap).

Reuses the same agent/world/orchestrator as dispatch_eval_avg8.py.
Dry-run by default; pass --execute to POST.
"""
import argparse
import csv
import json
import os
import sys
import requests

API = "https://api.studio.mercor.com"
CAMP = "camp_4e196b1414a1499db54b43233104b0a7"
COMP = "comp_2fa4115109d741cd94a3c409ed89e61f"
ACCT = "acct_85b680d4c5ba49a29f19c173672aebea"
ROOT = "/Users/mahmoodmapara/Desktop/terminal-bench-qc"

AGENT_ID = "agent_ef13be96aaf149d39d5bf5fdbc5077f9"
AGENT_VERSION = 2
SYSTEM_PROMPT = ("\nYou are an agent that completes tasks independently.\n"
                 "Use the tools provided to you to complete the task to the "
                 "best of your ability.\n")
OPUS = ("orch_e3599ac0f823422c928fbd2982aa3116", 4, "claude-opus-4-8 (effort=high)")

MANIFEST = f"{ROOT}/_local/broken_oracle_export/manifest.csv"
CULL_F = f"{ROOT}/_local/bo_cull.txt"
BODY_F = f"{ROOT}/_local/difficulty_opus/eval_body.json"


def key():
    for line in open(f"{ROOT}/.env"):
        if line.startswith("RLS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("RLS_KEY not found")


def headers():
    return {"Authorization": f"Bearer {key()}", "X-Campaign-Id": CAMP,
            "X-Company-Id": COMP, "X-Account-Id": ACCT,
            "User-Agent": "curl/8.7.1", "Content-Type": "application/json"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=8)
    ap.add_argument("--name", default="Broken-oracle bucket difficulty avg@8 — Opus-4.8")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--exclude-scored", default="",
                    help="path to avg8_scores.json; drop task_ids already scored there")
    ap.add_argument("--limit", type=int, default=0, help="chunk size (0=all)")
    ap.add_argument("--offset", type=int, default=0, help="chunk offset")
    ap.add_argument("--manifest", default=MANIFEST)
    args = ap.parse_args()

    cull = set()
    if os.path.isfile(CULL_F):
        cull = {ln.strip() for ln in open(CULL_F) if ln.strip()}
    scored = set()
    if args.exclude_scored and os.path.isfile(args.exclude_scored):
        scored = {tid for tid, s in json.load(open(args.exclude_scored)).items()
                  if (s or {}).get("avg") is not None}
    rows = list(csv.DictReader(open(args.manifest)))
    task_ids, skipped = [], 0
    for r in rows:
        if r["task_name"] in cull or not r.get("task_id") or r["task_id"] in scored:
            skipped += 1
            continue
        task_ids.append(r["task_id"])
    task_ids.sort()
    if args.limit:
        task_ids = task_ids[args.offset:args.offset + args.limit]

    traj = [{"task_id": tid, "orchestrator_id": OPUS[0],
             "orchestrator_version": OPUS[1], "agent_id": AGENT_ID,
             "agent_version": AGENT_VERSION, "system_prompt": SYSTEM_PROMPT}
            for tid in task_ids for _ in range(args.runs)]
    body = {"trajectory_batch_name": args.name, "orchestrator_ids": [OPUS[0]],
            "judge_ids": [], "trajectory_request": traj}
    n = len(traj)

    os.makedirs(os.path.dirname(BODY_F), exist_ok=True)
    json.dump(body, open(BODY_F, "w"))

    print(f"tasks               : {len(task_ids)}  (culled/skipped: {skipped})")
    print(f"runs per task       : {args.runs}")
    print(f"model               : {OPUS[0]} v{OPUS[1]}  {OPUS[2]}")
    print(f"trajectories        : {n}  ({len(task_ids)} x {args.runs} x 1)")
    print(f"batch name          : {args.name!r}")
    print(f"body -> {BODY_F}")
    if n > 50000:
        sys.exit(f"ABORT: {n} > 50,000 hard cap")
    if not args.execute:
        print("\nDRY-RUN — nothing dispatched. Re-run with --execute.")
        return
    print("\nPOST /orchestration/trajectories/batch ...")
    r = requests.post(f"{API}/orchestration/trajectories/batch",
                      headers=headers(), data=json.dumps(body), timeout=300)
    print(f"HTTP {r.status_code}")
    try:
        resp = r.json()
        bid = resp.get("trajectory_batch_id") or resp.get("batch_id") or resp.get("id")
        print(json.dumps(resp, indent=2)[:800])
        if bid:
            print(f"\nNEW BATCH ID: {bid}")
    except Exception:
        print(r.text[:800])
    r.raise_for_status()


if __name__ == "__main__":
    main()
