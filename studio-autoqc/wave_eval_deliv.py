#!/usr/bin/env python3
"""Throttled avg@8 eval for the Reflection d2 world — dispatch in waves so the RLS
platform can build images without ResourceExhausted (firing all 45k at once fails).

Keeps in-flight (pending+running) under CEILING by dispatching WAVE tasks at a time and
waiting when the queue is full. Resumable via a dispatched-state file. GPT-5.4 x8, Terminus-2.
"""
import json, os, time, requests
API = "https://api.studio.mercor.com"
CAMP="camp_4e196b1414a1499db54b43233104b0a7";COMP="comp_2fa4115109d741cd94a3c409ed89e61f";ACCT="acct_85b680d4c5ba49a29f19c173672aebea"
WORLD="world_d07785c2757b4a5cb643517cbea8ec98"  # delivery_2 top-up
ROOT="/Users/mahmoodmapara/Desktop/terminal-bench-qc";OUT=f"{ROOT}/_local/eval_deliv"
K=[l.split("=",1)[1].strip().strip('"').strip("'") for l in open(f"{ROOT}/.env") if l.startswith("RLS_KEY=")][0]
H={"Authorization":f"Bearer {K}","X-Campaign-Id":CAMP,"X-Company-Id":COMP,"X-Account-Id":ACCT,"User-Agent":"curl/8.7.1","Content-Type":"application/json"}
AGENT="agent_ef13be96aaf149d39d5bf5fdbc5077f9";GPT=("orch_dfafb7e86f4442728e9584f22ff67f70",12)
SP="\nYou are an agent that completes tasks independently.\nUse the tools provided to you to complete the task to the best of your ability.\n"
WAVE=200; CEILING=1600; RUNS=8  # 200 tasks x8 = 1600 traj/wave, <=1600 in-flight
STATE=f"{OUT}/wave_dispatched.txt"; BIDS=f"{OUT}/wave_batch_ids.txt"

def q(sql):
    for _ in range(4):
        r=requests.post(f"{API}/querier/unstructured",headers=H,json={"query":sql},timeout=180)
        if r.status_code==200: return r.json().get("rows",[])
        time.sleep(5)
    return []

def inflight():
    bids=[b for b in open(BIDS).read().split() if b] if os.path.exists(BIDS) else []
    if not bids: return 0
    inlist="','".join(bids)
    rows=q(f"SELECT COUNT(*) n FROM trajectories WHERE trajectory_batch_id IN ('{inlist}') AND trajectory_status IN ('pending','running')")
    return rows[0]["n"] if rows else 0

def main():
    allids=sorted(set(l.strip() for l in open(f"{ROOT}/_local/deliv2_topup.txt") if l.strip()))
    done=set(open(STATE).read().split()) if os.path.exists(STATE) else set()
    todo=[t for t in allids if t not in done]
    print(f"[wave-eval] {len(todo)} tasks to dispatch (of {len(allids)}), wave={WAVE}, ceiling={CEILING}",flush=True)
    wi=0
    while todo:
        fl=inflight()
        if fl>=CEILING:
            print(f"  in-flight {fl} >= {CEILING}, waiting...",flush=True); time.sleep(180); continue
        wave=todo[:WAVE]; todo=todo[WAVE:]; wi+=1
        traj=[{"task_id":t,"orchestrator_id":GPT[0],"orchestrator_version":GPT[1],"agent_id":AGENT,"agent_version":2,"system_prompt":SP} for t in wave for _ in range(RUNS)]
        body={"trajectory_batch_name":f"delivery_2 topup avg@8 wave{wi} — 2026-07-08","orchestrator_ids":[GPT[0]],"judge_ids":[],"trajectory_request":traj}
        r=requests.post(f"{API}/orchestration/trajectories/batch",headers=H,data=json.dumps(body),timeout=300)
        if r.status_code!=200:
            print(f"  wave{wi} dispatch FAILED {r.status_code}: {r.text[:150]} — retrying later",flush=True)
            todo=wave+todo; time.sleep(60); continue
        bid=r.json().get("trajectory_batch_id") or r.json().get("batch_id")
        with open(STATE,"a") as f: f.write("\n".join(wave)+"\n")
        with open(BIDS,"a") as f: f.write((bid or "")+"\n")
        print(f"  wave{wi}: +{len(wave)} tasks ({len(wave)*RUNS} traj) batch={bid} | in-flight was {fl} | {len(todo)} left",flush=True)
        time.sleep(300)
    print("ALL WAVES DISPATCHED",flush=True)

if __name__=="__main__":
    main()
