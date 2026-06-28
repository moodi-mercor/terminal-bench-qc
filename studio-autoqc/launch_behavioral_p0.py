#!/usr/bin/env python3
"""Launch the cloud behavioral-confirmation batch for the P0 QC tasks.

Uses Lighthouse's `validate_patch` oracle agent on Modal: per task it grades the
EMPTY submission (expect 0) and the GOLDEN solution/solve.sh (expect 1) — no LLM
tokens. Reads the P0 task_ids from the querier (qc_priority='P0'), builds one
trajectory batch, and POSTs it. Validated payload schema (see the 2-task test
batch_e580...): each entry needs task_id + orchestrator/version + agent/version +
system_prompt; batch needs name + orchestrator_ids[] + judge_ids[] (+ platform_id).

Read back per task from trajectory_output: validation_passed / empty_score / golden_score.

Usage:
    python launch_behavioral_p0.py            # dry-run: count + payload preview
    python launch_behavioral_p0.py --launch   # actually create the batch
"""
import argparse
import json
import os
import sys
import time
import requests

API = "https://api.studio.mercor.com"
CAMPAIGN = "camp_4e196b1414a1499db54b43233104b0a7"
COMPANY = "comp_2fa4115109d741cd94a3c409ed89e61f"
WORLD = "world_2c7cdb23737845ad83a9acfa1aa8c25b"
# validated config: Lighthouse Harbor (Oracle) validate_patch agent + No-Op orchestrator
AGENT_ID, AGENT_VER = "agent_ec6f92015c4447d3a62f3dbf0f341a93", 3
ORCH_ID, ORCH_VER = "orch_f7e3cf3c8f70464bab3d5faf6008647c", 2
PLATFORM = "platform_85f92fcdcd534f839799b876bbcc9bb6"


def key():
    for line in open(os.path.join(os.path.dirname(__file__), "..", ".env")):
        if line.startswith("RLS_WRITE_KEY="):
            return line.split("=", 1)[1].strip()
    sys.exit("RLS_WRITE_KEY not in .env")


def headers():
    return {"Authorization": f"Bearer {key()}", "X-Campaign-Id": CAMPAIGN,
            "X-Company-Id": COMPANY, "Content-Type": "application/json"}


def task_ids(where):
    r = requests.post(f"{API}/querier/task-ids", headers=headers(), timeout=120,
                      data=json.dumps({"query": f"SELECT task_id FROM tasks WHERE world_id = "
                                       f"'{WORLD}' AND {where}"}))
    r.raise_for_status()
    return r.json()["task_ids"]


def post_batch(name, ids):
    entries = [{"task_id": t, "orchestrator_id": ORCH_ID, "orchestrator_version": ORCH_VER,
                "agent_id": AGENT_ID, "agent_version": AGENT_VER, "system_prompt": ""} for t in ids]
    payload = {"trajectory_batch_name": name, "orchestrator_ids": [ORCH_ID],
               "judge_ids": [], "platform_id": PLATFORM, "trajectory_request": entries}
    r = requests.post(f"{API}/orchestration/trajectories/batch", headers=headers(),
                      data=json.dumps(payload), timeout=300)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--launch", action="store_true")
    ap.add_argument("--name", default="QC behavioral P0 oracle+noop")
    ap.add_argument("--where", default="custom_fields->>'qc_priority' = 'P0'",
                    help="SQL WHERE predicate selecting target tasks")
    ap.add_argument("--chunk", type=int, default=0, help="tasks per batch (0 = single batch)")
    ap.add_argument("--ids-file", default=None, help="read task_ids from a file (bypasses the 5k querier cap)")
    args = ap.parse_args()
    if args.ids_file:
        ids = [l.strip() for l in open(args.ids_file) if l.strip()]
    else:
        ids = task_ids(args.where)
    print(f"target tasks: {len(ids)} | agent {AGENT_ID} v{AGENT_VER} (validate_patch) | "
          f"orchestrator {ORCH_ID} v{ORCH_VER} (no-op)")
    chunk = args.chunk or len(ids)
    chunks = [ids[i:i + chunk] for i in range(0, len(ids), chunk)]
    print(f"{len(chunks)} batch(es) of up to {chunk}")
    if not args.launch:
        print("DRY-RUN."); return
    for i, c in enumerate(chunks, 1):
        nm = args.name if len(chunks) == 1 else f"{args.name} [{i}/{len(chunks)}]"
        r = post_batch(nm, c)
        if r.status_code in (200, 201):
            print(f"  LAUNCHED [{i}/{len(chunks)}] {len(c)} tasks ->", r.json().get("trajectory_batch_id"))
        else:
            print(f"  FAILED [{i}/{len(chunks)}]", r.status_code, r.text[:300])
        if i < len(chunks):
            time.sleep(15)  # 5/min batch-create rate limit


if __name__ == "__main__":
    main()
