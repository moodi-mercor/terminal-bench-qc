#!/usr/bin/env python3
"""Dispatch Opus-4.8 (xhigh) eval on the v9-untested tasks to prove solvability (OR-clause)."""
import argparse,json,sys,requests
API="https://api.studio.mercor.com"; CAMP="camp_4e196b1414a1499db54b43233104b0a7"
COMP="comp_2fa4115109d741cd94a3c409ed89e61f"; ACCT="acct_85b680d4c5ba49a29f19c173672aebea"
ROOT="/Users/mahmoodmapara/Desktop/terminal-bench-qc"
AGENT_ID="agent_ef13be96aaf149d39d5bf5fdbc5077f9"; AGENT_VERSION=2
SYS="\nYou are an agent that completes tasks independently.\nUse the tools provided to you to complete the task to the best of your ability.\n"
OPUS=("orch_e3599ac0f823422c928fbd2982aa3116",4,"claude-opus-4-8 (effort=high)")
GPT=("orch_dfafb7e86f4442728e9584f22ff67f70",12,"gpt-5.4 (effort=high)")
def key():
    for l in open(f"{ROOT}/.env"):
        if l.startswith("RLS_KEY="): return l.split("=",1)[1].strip().strip('"').strip("'")
    sys.exit("no RLS_KEY")
H={"Authorization":f"Bearer {key()}","X-Campaign-Id":CAMP,"X-Company-Id":COMP,"X-Account-Id":ACCT,"User-Agent":"curl/8.7.1","Content-Type":"application/json"}
ap=argparse.ArgumentParser(); ap.add_argument("--runs",type=int,default=8); ap.add_argument("--gpt",action="store_true"); ap.add_argument("--execute",action="store_true")
ap.add_argument("--name",default="v9-untested recovery — Opus-4.8 avg@8 (solvability)")
a=ap.parse_args()
ids=[t["task_id"] for t in json.load(open(f"{ROOT}/_local/v9_recover_tasks.json"))]
orchs=[OPUS]+([GPT] if a.gpt else [])
traj=[{"task_id":t,"orchestrator_id":o[0],"orchestrator_version":o[1],"agent_id":AGENT_ID,"agent_version":AGENT_VERSION,"system_prompt":SYS}
      for o in orchs for t in ids for _ in range(a.runs)]
body={"trajectory_batch_name":a.name,"orchestrator_ids":[o[0] for o in orchs],"judge_ids":[],"trajectory_request":traj}
print(f"tasks {len(ids)} | runs {a.runs} | models {[o[2] for o in orchs]} | trajectories {len(traj)}")
if len(traj)>50000: sys.exit("ABORT >50k")
if not a.execute: print("DRY-RUN — re-run with --execute"); sys.exit()
r=requests.post(f"{API}/orchestration/trajectories/batch",headers=H,data=json.dumps(body),timeout=300)
print("HTTP",r.status_code); resp=r.json() if r.headers.get("content-type","").startswith("application/json") else {}
print(json.dumps(resp,indent=1)[:800]); bid=resp.get("trajectory_batch_id") or resp.get("batch_id")
if bid: print("NEW BATCH:",bid); open(f"{ROOT}/_local/v9_recover_batch.txt","w").write(bid+"\n")
