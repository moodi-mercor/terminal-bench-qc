#!/usr/bin/env python3
"""Dispatch GLM-5.2 pass@5 over the strong/weak-split candidate pool (healthy-hard,
Opus-solvable, not Opus-easy). Weak-model side of the Opus-4.8/GLM-5.2 split:
keep tasks where GLM-5.2 solves <3/5.

Same harness as the Opus difficulty evals:
  agent : Lighthouse Harbor (Terminus)  agent_ef13be96... v2
  orch  : GLM-5.2 (chosen via smoke test; default fireworks zai-org/GLM-5.2)

Dry-run unless --execute.
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
                 "Use the tools provided to you to complete the task to the best of your ability.\n")
ORCHS = {
    "fireworks": ("orch_8b6b128935264db18636e2cf83c63e7f", 1, "fireworks zai-org/GLM-5.2"),
    "modal-fp8": ("orch_4ee99c60baf94438b6c6a44642c51f84", 2, "modal GLM-5.2-FP8"),
}


def key():
    for l in open(f"{ROOT}/.env"):
        if l.startswith("RLS_KEY="):
            return l.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("no RLS_KEY")


H = {"Authorization": f"Bearer {key()}", "X-Campaign-Id": CAMP, "X-Company-Id": COMP,
     "X-Account-Id": ACCT, "User-Agent": "curl/8.7.1", "Content-Type": "application/json"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--ids", default=f"{ROOT}/_local/glm52_pool.txt")
    ap.add_argument("--orch", choices=ORCHS, default="fireworks")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--name", default="strong/weak split — GLM-5.2 pass@5 over Opus-solvable healthy-hard pool")
    a = ap.parse_args()
    oid, over, label = ORCHS[a.orch]
    ids = [l.strip() for l in open(a.ids) if l.strip()]
    traj = [{"task_id": t, "orchestrator_id": oid, "orchestrator_version": over,
             "agent_id": AGENT_ID, "agent_version": AGENT_VERSION,
             "system_prompt": SYSTEM_PROMPT} for t in ids for _ in range(a.runs)]
    body = {"trajectory_batch_name": a.name, "orchestrator_ids": [oid],
            "judge_ids": [], "trajectory_request": traj}
    os.makedirs(f"{ROOT}/_local/glm52_pass5", exist_ok=True)
    json.dump(body, open(f"{ROOT}/_local/glm52_pass5/body.json", "w"))
    n = len(traj)
    print(f"tasks: {len(ids)} | runs/task: {a.runs} | model: {label} | trajectories: {n}")
    if n > 50000:
        sys.exit("ABORT >50k")
    if not a.execute:
        print("DRY-RUN — nothing dispatched. Re-run with --execute.")
        return
    r = requests.post(f"{API}/orchestration/trajectories/batch", headers=H,
                      data=json.dumps(body), timeout=300)
    print("HTTP", r.status_code)
    try:
        resp = r.json()
        bid = resp.get("trajectory_batch_id") or resp.get("batch_id") or resp.get("id")
        print("BATCH ID:", bid)
        open(f"{ROOT}/_local/glm52_pass5/batch_id.txt", "w").write(str(bid or ""))
    except Exception:
        print(r.text[:800])
    r.raise_for_status()


if __name__ == "__main__":
    main()
