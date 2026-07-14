#!/usr/bin/env python3
"""Dispatch Opus-4.8 pass@4 over the 811 unknown-difficulty (healthy, no-rollout) tasks
to measure their difficulty. 811 x 4 = 3,244 trajectories. Dry-run unless --execute.

Same harness as the avg@8 difficulty eval:
  agent : Lighthouse Harbor (Terminus)  agent_ef13be96... v2
  orch  : Opus-4.8  orch_e3599ac0... v4 (adaptive thinking, effort=high)
  world : Canonical Tasks
"""
import argparse, json, os, sys, requests
API="https://api.studio.mercor.com"
CAMP="camp_4e196b1414a1499db54b43233104b0a7"; COMP="comp_2fa4115109d741cd94a3c409ed89e61f"; ACCT="acct_85b680d4c5ba49a29f19c173672aebea"
ROOT="/Users/mahmoodmapara/Desktop/terminal-bench-qc"
AGENT_ID="agent_ef13be96aaf149d39d5bf5fdbc5077f9"; AGENT_VERSION=2
SYSTEM_PROMPT=("\nYou are an agent that completes tasks independently.\n"
               "Use the tools provided to you to complete the task to the best of your ability.\n")
OPUS=("orch_e3599ac0f823422c928fbd2982aa3116",4,"claude-opus-4-8 (effort=high)")

def key():
    for l in open(f"{ROOT}/.env"):
        if l.startswith("RLS_KEY="): return l.split("=",1)[1].strip().strip('"').strip("'")
    sys.exit("no RLS_KEY")
H={"Authorization":f"Bearer {key()}","X-Campaign-Id":CAMP,"X-Company-Id":COMP,"X-Account-Id":ACCT,"User-Agent":"curl/8.7.1","Content-Type":"application/json"}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--runs",type=int,default=4); ap.add_argument("--execute",action="store_true")
    ap.add_argument("--name",default="QC difficulty — 811 unknown, Opus-4.8 pass@4"); a=ap.parse_args()
    ids=[l.strip() for l in open(f"{ROOT}/_local/unknown_diff_ids.txt") if l.strip()]
    traj=[{"task_id":t,"orchestrator_id":OPUS[0],"orchestrator_version":OPUS[1],"agent_id":AGENT_ID,
           "agent_version":AGENT_VERSION,"system_prompt":SYSTEM_PROMPT} for t in ids for _ in range(a.runs)]
    body={"trajectory_batch_name":a.name,"orchestrator_ids":[OPUS[0]],"judge_ids":[],"trajectory_request":traj}
    os.makedirs(f"{ROOT}/_local/opus_pass4",exist_ok=True); json.dump(body,open(f"{ROOT}/_local/opus_pass4/body.json","w"))
    n=len(traj)
    print(f"tasks: {len(ids)} | runs/task: {a.runs} | model: {OPUS[2]} | trajectories: {n}")
    if n>50000: sys.exit("ABORT >50k")
    if not a.execute:
        print("DRY-RUN — nothing dispatched. Re-run with --execute."); return
    r=requests.post(f"{API}/orchestration/trajectories/batch",headers=H,data=json.dumps(body),timeout=300)
    print("HTTP",r.status_code)
    try:
        resp=r.json(); bid=resp.get("trajectory_batch_id") or resp.get("batch_id") or resp.get("id")
        print("BATCH ID:",bid); open(f"{ROOT}/_local/opus_pass4/batch_id.txt","w").write(str(bid or ""))
    except Exception: print(r.text[:800])
    r.raise_for_status()

if __name__=="__main__": main()
