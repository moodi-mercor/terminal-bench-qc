#!/usr/bin/env python3
"""avg@8 difficulty eval for the Reflection delivery_2 world (world_d07785c2...).

Spec: Terminus-2 agent, GPT-5.4 (reasoning effort high) x 8 runs/task.
5,627 tasks x 8 = 45,016 trajectories (< 50k cap). Dry-run by default; --execute to POST.
"""
import argparse, json, os, sys, requests

API = "https://api.studio.mercor.com"
CAMP = "camp_4e196b1414a1499db54b43233104b0a7"
COMP = "comp_2fa4115109d741cd94a3c409ed89e61f"
ACCT = "acct_85b680d4c5ba49a29f19c173672aebea"
ROOT = "/Users/mahmoodmapara/Desktop/terminal-bench-qc"
IDMAP = f"{ROOT}/_local/qc_out_eval_pool/rls_taskids.json"
BODY_F = f"{ROOT}/_local/qc_out_eval_pool/eval_avg8_body.json"

AGENT_ID = "agent_ef13be96aaf149d39d5bf5fdbc5077f9"; AGENT_VERSION = 2
SYSTEM_PROMPT = ("\nYou are an agent that completes tasks independently.\n"
                 "Use the tools provided to you to complete the task to the "
                 "best of your ability.\n")
GPT = ("orch_dfafb7e86f4442728e9584f22ff67f70", 12, "gpt-5.4 (effort=high)")
OPUS = ("orch_e3599ac0f823422c928fbd2982aa3116", 4, "claude-opus-4-8 (effort=high)")

def key():
    for line in open(f"{ROOT}/.env"):
        if line.startswith("RLS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("RLS_KEY not found")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=8)
    ap.add_argument("--model", choices=["gpt", "opus"], default="gpt")
    ap.add_argument("--execute", action="store_true")
    a = ap.parse_args()
    orch = GPT if a.model == "gpt" else OPUS
    idmap = json.load(open(IDMAP))
    tids = sorted(idmap.values())
    name = f"Reflection delivery_2 avg@8 — {orch[2]} — 2026-07-08"
    traj = [{"task_id": t, "orchestrator_id": orch[0], "orchestrator_version": orch[1],
             "agent_id": AGENT_ID, "agent_version": AGENT_VERSION,
             "system_prompt": SYSTEM_PROMPT}
            for t in tids for _ in range(a.runs)]
    body = {"trajectory_batch_name": name, "orchestrator_ids": [orch[0]],
            "judge_ids": [], "trajectory_request": traj}
    json.dump(body, open(BODY_F, "w"))
    print(f"tasks {len(tids)} x runs {a.runs} = {len(traj)} trajectories on {orch[2]}")
    print(f"batch name: {name!r}")
    if len(traj) > 50000: sys.exit("ABORT > 50k cap")
    if not a.execute:
        print("DRY-RUN — re-run with --execute"); return
    H = {"Authorization": f"Bearer {key()}", "X-Campaign-Id": CAMP, "X-Company-Id": COMP,
         "X-Account-Id": ACCT, "User-Agent": "curl/8.7.1", "Content-Type": "application/json"}
    r = requests.post(f"{API}/orchestration/trajectories/batch", headers=H,
                      data=json.dumps(body), timeout=600)
    print("HTTP", r.status_code)
    j = r.json()
    print(json.dumps(j, indent=2)[:800])
    bid = j.get("trajectory_batch_id") or j.get("batch_id") or j.get("id")
    if bid: print("NEW BATCH ID:", bid)

if __name__ == "__main__":
    main()
