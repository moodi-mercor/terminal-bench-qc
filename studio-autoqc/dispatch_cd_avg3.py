#!/usr/bin/env python3
"""avg@3 GPT-5.4 eval over the QC-pass net-new client-delivery tasks (Canonical world)."""
import argparse, json, sys, requests
API="https://api.studio.mercor.com"
CAMP="camp_4e196b1414a1499db54b43233104b0a7";COMP="comp_2fa4115109d741cd94a3c409ed89e61f";ACCT="acct_85b680d4c5ba49a29f19c173672aebea"
ROOT="/Users/mahmoodmapara/Desktop/terminal-bench-qc"
ELIG=f"/private/tmp/claude-501/-Users-mahmoodmapara-Desktop-terminal-bench-qc/1f204211-5ba8-469d-bfaa-7ae458192941/scratchpad/client_del/eval_eligible.json"
AGENT_ID="agent_ef13be96aaf149d39d5bf5fdbc5077f9"; AGENT_VERSION=2
SYSTEM_PROMPT=("\nYou are an agent that completes tasks independently.\n"
               "Use the tools provided to you to complete the task to the best of your ability.\n")
GPT=("orch_dfafb7e86f4442728e9584f22ff67f70",12,"gpt-5.4 (effort=high)")
def key():
    for l in open(f"{ROOT}/.env"):
        if l.startswith("RLS_KEY="): return l.split("=",1)[1].strip().strip('"').strip("'")
    sys.exit("no key")
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--runs",type=int,default=3); ap.add_argument("--execute",action="store_true"); a=ap.parse_args()
    orch=GPT
    elig=json.load(open(ELIG)); tids=sorted(set(elig.values()))
    name="Client TB deliveries — QC-pass avg@3 — gpt-5.4 (effort=high) — 2026-07-10"
    traj=[{"task_id":t,"orchestrator_id":orch[0],"orchestrator_version":orch[1],
           "agent_id":AGENT_ID,"agent_version":AGENT_VERSION,"system_prompt":SYSTEM_PROMPT}
          for t in tids for _ in range(a.runs)]
    body={"trajectory_batch_name":name,"orchestrator_ids":[orch[0]],"judge_ids":[],"trajectory_request":traj}
    print(f"tasks {len(tids)} x runs {a.runs} = {len(traj)} trajectories on {orch[2]}")
    print("batch:",name)
    if len(traj)>50000: sys.exit("ABORT >50k")
    if not a.execute: print("DRY-RUN — re-run with --execute"); return
    H={"Authorization":f"Bearer {key()}","X-Campaign-Id":CAMP,"X-Company-Id":COMP,"X-Account-Id":ACCT,"User-Agent":"curl/8.7.1","Content-Type":"application/json"}
    r=requests.post(f"{API}/orchestration/trajectories/batch",headers=H,data=json.dumps(body),timeout=600)
    print("HTTP",r.status_code); j=r.json(); print(json.dumps(j,indent=2)[:600])
    bid=j.get("trajectory_batch_id") or j.get("batch_id") or j.get("id")
    if bid:
        print("NEW BATCH ID:",bid); open(f"{ROOT}/_local/client_del_qc/eval_batch_id.txt","w").write(bid+"\n")
if __name__=="__main__": main()
