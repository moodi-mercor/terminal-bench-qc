#!/usr/bin/env python3
"""Dispatch an eval batch for the ~3,400 Canonical-Tasks tasks that have no
trajectory data yet (the world minus batch_c5e617e gap).

Config mirrors the Create Batch Run dialog the user specified:
  Harness (API `agent`)       : Lighthouse Harbor (Terminus)  agent_ef13be96... v2
  Model   (API `orchestrator`): Baseten / GLM 5.1             orch_14e61ca2... v1
  Judge                        : anthropic/claude-sonnet-4-5   judge_f3c8f130...
  Platform                     : No Environment (omit -> world default)
  Runs per task                : 3

The avg@N is expressed by repeating each task_id `--runs` times in
trajectory_request (the API has no n/seeds field). 3,407 tasks x 3 = 10,221
trajectories (< 50k hard cap; > 1k customer cap, so requires campaign_admin —
which this campaign role is).

Endpoint: POST /orchestration/trajectories/batch  (rate limit 5/min).

Dry-run by default: builds + validates the body, writes it to disk, prints a
summary and a sample item. Pass --execute to actually dispatch.

Usage:
  python dispatch_gap_batch.py                       # dry-run
  python dispatch_gap_batch.py --name "TB gap avg@3" # set batch name (dry-run)
  python dispatch_gap_batch.py --name "TB gap avg@3" --execute
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

# Resolved from the Studio campaign (see conversation). UI label -> API field is
# inverted: dialog "Harness" == API agent; dialog "Model" == API orchestrator.
AGENT_ID = "agent_ef13be96aaf149d39d5bf5fdbc5077f9"        # Lighthouse Harbor (Terminus)
AGENT_VERSION = 2
# Baseten / GLM 5.1 (orch_14e61ca2 v1) was the original choice but its endpoint
# returns HTTP 402 (unpaid) -> all trajectories failed. Switched to Kimi K2.7 on
# Fireworks (approved, working billing) per user.
ORCH_ID = "orch_506a4bcb46254151bbe2eec9f252f35a"          # Kimi K2.7 (Fireworks)
ORCH_VERSION = 1
JUDGE_ID = "judge_f3c8f130cc6444b582f0d2ce18c891a4"        # anthropic/claude-sonnet-4-5
# Terminus agent's built-in default system prompt (verbatim).
SYSTEM_PROMPT = ("\nYou are an agent that completes tasks independently.\n"
                 "Use the tools provided to you to complete the task to the "
                 "best of your ability.\n")

GAP_F = f"{ROOT}/_local/gap/missing_tasks.jsonl"
BODY_F = f"{ROOT}/_local/gap/dispatch_body.json"


def key():
    if os.environ.get("RLS_KEY"):
        return os.environ["RLS_KEY"]
    for line in open(f"{ROOT}/.env"):
        if line.startswith("RLS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("RLS_KEY not found")


def headers():
    return {"Authorization": f"Bearer {key()}", "X-Campaign-Id": CAMP,
            "X-Company-Id": COMP, "X-Account-Id": ACCT,
            "User-Agent": "curl/8.7.1", "Content-Type": "application/json"}


def load_gap():
    if not os.path.isfile(GAP_F):
        sys.exit(f"missing gap file: {GAP_F}")
    ids = []
    with open(GAP_F) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            tid = row.get("task_id")
            if tid:
                ids.append(tid)
    # de-dup, preserve order
    seen, out = set(), []
    for t in ids:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def build_body(task_ids, runs, name):
    item = lambda tid: {
        "task_id": tid,
        "orchestrator_id": ORCH_ID,
        "orchestrator_version": ORCH_VERSION,
        "agent_id": AGENT_ID,
        "agent_version": AGENT_VERSION,
        "system_prompt": SYSTEM_PROMPT,
    }
    traj = [item(t) for t in task_ids for _ in range(runs)]
    return {
        "trajectory_batch_name": name,
        "orchestrator_ids": [ORCH_ID],
        "judge_ids": [JUDGE_ID],
        "trajectory_request": traj,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3, help="runs per task (default 3)")
    ap.add_argument("--name", default="TB Canonical gap (no-trajectory) avg@3 — KimiK2.7/Terminus")
    ap.add_argument("--execute", action="store_true", help="actually POST the batch")
    args = ap.parse_args()

    task_ids = load_gap()
    body = build_body(task_ids, args.runs, args.name)
    n_traj = len(body["trajectory_request"])

    os.makedirs(os.path.dirname(BODY_F), exist_ok=True)
    with open(BODY_F, "w") as f:
        json.dump(body, f)

    print(f"tasks (unique)     : {len(task_ids)}")
    print(f"runs per task      : {args.runs}")
    print(f"trajectories       : {n_traj}   (grading runs: {n_traj} x 1 judge)")
    print(f"batch name         : {args.name!r}")
    print(f"agent (harness)    : {AGENT_ID} v{AGENT_VERSION}  (Lighthouse Harbor / Terminus)")
    print(f"orchestrator(model): {ORCH_ID} v{ORCH_VERSION}  (Kimi K2.7 / Fireworks)")
    print(f"judge              : {JUDGE_ID}  (claude-sonnet-4-5)")
    print(f"platform           : world default (No Environment override)")
    print(f"body written to    : {BODY_F}")
    print("sample item        : " + json.dumps(body["trajectory_request"][0]))

    if n_traj > 50000:
        sys.exit(f"ABORT: {n_traj} trajectories exceeds 50,000 hard cap.")

    if not args.execute:
        print("\nDRY-RUN — nothing dispatched. Re-run with --execute to fire.")
        return

    print("\nDispatching POST /orchestration/trajectories/batch ...")
    r = requests.post(f"{API}/orchestration/trajectories/batch",
                      headers=headers(), data=json.dumps(body), timeout=300)
    print(f"HTTP {r.status_code}")
    try:
        resp = r.json()
        print(json.dumps(resp, indent=2)[:2000])
        bid = resp.get("trajectory_batch_id") or resp.get("batch_id") or resp.get("id")
        if bid:
            print(f"\nNEW BATCH ID: {bid}")
    except Exception:
        print(r.text[:2000])
    r.raise_for_status()


if __name__ == "__main__":
    main()
