#!/usr/bin/env python3
"""Dispatch the production avg@8 eval over the 824 difficulty-passing (Sonnet
avg@3 <= 0.5), QC-passing OTS tasks, across GPT-5.4 and Opus-4.8.

  824 tasks x 8 runs x 2 models = 13,184 trajectories (< 50k hard cap).

Harness matches the difficulty-filter run:
  agent    : Lighthouse Harbor (Terminus)  agent_ef13be96... v2
  world    : Canonical Tasks               world_2c7cdb...
  platform : No Environment (world default)

Orchestrators (override with --opus / --gpt):
  Opus-4.8 : orch_e3599ac0... v4  (adaptive thinking, effort=high)   [default]
  GPT-5.4  : orch_dfafb7e8... v12 (reasoning effort=high)            [default]

Dry-run by default; pass --execute to POST.
  python dispatch_eval_avg8.py                 # dry-run
  python dispatch_eval_avg8.py --execute
"""
import argparse
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

# (orchestrator_id, version, label)
OPUS = ("orch_e3599ac0f823422c928fbd2982aa3116", 4, "claude-opus-4-8 (effort=high)")
GPT = ("orch_dfafb7e86f4442728e9584f22ff67f70", 12, "gpt-5.4 (effort=high)")

HARD_F = f"{ROOT}/_local/ots_difficulty/hard_tasks.json"
BODY_F = f"{ROOT}/_local/ots_difficulty/eval_avg8_body.json"


def key():
    for line in open(f"{ROOT}/.env"):
        if line.startswith("RLS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("RLS_KEY not found")


def headers():
    return {"Authorization": f"Bearer {key()}", "X-Campaign-Id": CAMP,
            "X-Company-Id": COMP, "X-Account-Id": ACCT,
            "User-Agent": "curl/8.7.1", "Content-Type": "application/json"}


def build(task_ids, runs, name, orchs):
    traj = []
    for oid, over, _ in orchs:
        for tid in task_ids:
            for _ in range(runs):
                traj.append({"task_id": tid, "orchestrator_id": oid,
                             "orchestrator_version": over, "agent_id": AGENT_ID,
                             "agent_version": AGENT_VERSION,
                             "system_prompt": SYSTEM_PROMPT})
    return {"trajectory_batch_name": name,
            "orchestrator_ids": [o[0] for o in orchs],
            "judge_ids": [], "trajectory_request": traj}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=8)
    ap.add_argument("--name", default="OTS difficulty-pass avg@8 — GPT-5.4 + Opus-4.8")
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()

    hard = json.load(open(HARD_F))
    task_ids = [t["task_id"] for t in hard]
    orchs = [OPUS, GPT]
    body = build(task_ids, args.runs, args.name, orchs)
    n = len(body["trajectory_request"])

    os.makedirs(os.path.dirname(BODY_F), exist_ok=True)
    json.dump(body, open(BODY_F, "w"))

    print(f"tasks               : {len(task_ids)}")
    print(f"runs per task/model : {args.runs}")
    print(f"models              :")
    for o in orchs:
        print(f"   {o[0]} v{o[1]}  {o[2]}")
    print(f"trajectories        : {n}  ({len(task_ids)} x {args.runs} x {len(orchs)})")
    print(f"agent               : {AGENT_ID} v{AGENT_VERSION} (Terminus)")
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
        print(json.dumps(resp, indent=2)[:1500])
        bid = resp.get("trajectory_batch_id") or resp.get("batch_id") or resp.get("id")
        if bid:
            print(f"\nNEW BATCH ID: {bid}")
    except Exception:
        print(r.text[:1500])
    r.raise_for_status()


if __name__ == "__main__":
    main()
