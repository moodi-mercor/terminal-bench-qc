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


def p0_task_ids():
    r = requests.post(f"{API}/querier/task-ids", headers=headers(), timeout=120,
                      data=json.dumps({"query": "SELECT task_id FROM tasks WHERE world_id = "
                                       f"'{WORLD}' AND custom_fields->>'qc_priority' = 'P0'"}))
    r.raise_for_status()
    return r.json()["task_ids"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--launch", action="store_true")
    ap.add_argument("--name", default="QC behavioral P0 oracle+noop")
    args = ap.parse_args()
    ids = p0_task_ids()
    entries = [{"task_id": t, "orchestrator_id": ORCH_ID, "orchestrator_version": ORCH_VER,
                "agent_id": AGENT_ID, "agent_version": AGENT_VER, "system_prompt": ""} for t in ids]
    payload = {"trajectory_batch_name": args.name, "orchestrator_ids": [ORCH_ID],
               "judge_ids": [], "platform_id": PLATFORM, "trajectory_request": entries}
    print(f"P0 tasks: {len(ids)} | agent {AGENT_ID} v{AGENT_VER} (validate_patch) | "
          f"orchestrator {ORCH_ID} v{ORCH_VER} (no-op)")
    if not args.launch:
        print("DRY-RUN. first entry:", json.dumps(entries[0]))
        return
    r = requests.post(f"{API}/orchestration/trajectories/batch", headers=headers(),
                      data=json.dumps(payload), timeout=180)
    if r.status_code in (200, 201):
        b = r.json()
        print("LAUNCHED batch:", b.get("trajectory_batch_id"), "status:", b.get("batch_launch_status"))
    else:
        print("FAILED", r.status_code, r.text[:400])


if __name__ == "__main__":
    main()
