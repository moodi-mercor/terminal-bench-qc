#!/usr/bin/env python3
"""Fresh paced GLM-5.2 pass@N over an arbitrary task set, TB settings.

Each task targets N genuine graded attempts. Every wave dispatches the full remaining
deficit (rate-limit bounces are free); genuine banked, rate-limited retried, broken set
aside. Same config as the Terminal Bench runs: vercel zai/glm-5.2 orch_174947b1,
Terminus agent_ef13be96 v2, standard system prompt, no judges.

Usage:
  RLS_RECOVER_KEY=... python dispatch_glm_fresh.py --camp camp_... --tasks _local/redo_x.json --runs 5 --execute
"""
import argparse, json, os, sys, time
from collections import defaultdict
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0,HERE)
import recover_glm_batch as R  # reuse hdrs/list_batch/classify_many/wait_drain
import glm_retry_lib as L
import requests

ORCH=("orch_174947b124e44793ad1d6ce004c45696",1)
AGENT=("agent_ef13be96aaf149d39d5bf5fdbc5077f9",2)

def classify_run(H, tid):
    """Genuine iff the model produced a graded attempt. This task set is known-runnable
    (GPT ran all of it), so no-score is ALWAYS infra (rate-limit / congestion casualty /
    empty-error) -> retry, never permanent 'broken'. MAX_ROUNDS is the global backstop."""
    import requests as _rq, time as _t
    for _ in range(5):
        try:
            r=_rq.get(f"{L.API}/trajectories/{tid}",headers=H,timeout=60)
            if r.status_code==200:
                to=r.json().get("trajectory_output") or {}
                em=str(to.get("error_message") or "").lower()
                tok=(to.get("usage_metrics") or {}).get("total_tokens",0) or 0
                sc=to.get("score")
                if "agenttimeouterror" in em and tok>0: return "genuine",0.0
                if sc is None: return "retry",None
                return "genuine",float(sc)
        except Exception: pass
        _t.sleep(2)
    return "retry",None

def classify_many_run(H, ids, workers=24):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return dict(zip(ids, ex.map(lambda t: classify_run(H,t), ids)))
SP="\nYou are an agent that completes tasks independently.\nUse the tools provided to you to complete the task to the best of your ability.\n"
MAX_ROUNDS=40

def main():
    global ORCH
    ap=argparse.ArgumentParser()
    ap.add_argument("--camp",required=True)
    ap.add_argument("--tasks",required=True)   # json with per_task keys
    ap.add_argument("--runs",type=int,default=5)
    ap.add_argument("--wave",type=int,default=1000)   # cap concurrency near the proven-clean width
    ap.add_argument("--tag",default="glm52")
    ap.add_argument("--gate",type=int,default=800)    # wait until total pool in-flight < gate before firing a wave (0=off)
    ap.add_argument("--pool-batches",default="")      # json list of pre-existing batch ids to also count as in-flight
    ap.add_argument("--orch",default=ORCH[0])          # comma-separated for multi-route round-robin
    ap.add_argument("--orch-ver",default=str(ORCH[1]))  # comma-separated, matched to --orch
    ap.add_argument("--execute",action="store_true")
    a=ap.parse_args()
    oids=[x.strip() for x in a.orch.split(",") if x.strip()]
    overs=[int(x) for x in str(a.orch_ver).split(",")]
    if len(overs)==1: overs=overs*len(oids)
    ORCHS=list(zip(oids,overs))          # round-robin these across each wave's trajectories
    ORCH=ORCHS[0]
    print(f"routes: {ORCHS}",flush=True)
    H=R.hdrs(a.camp)
    tasks=list(json.load(open(a.tasks))["per_task"].keys())
    # in-flight gate: never fire onto a busy system (that is what broke the pile-on batch).
    fired=[]
    seed_bids=json.load(open(a.pool_batches)) if a.pool_batches and os.path.isfile(a.pool_batches) else []
    def inflight():
        bids=list(dict.fromkeys(seed_bids+fired))
        if not bids: return 0
        inlist="','".join(bids)
        for _ in range(4):
            rr=requests.post(f"{L.API}/querier/unstructured",headers=H,
                             json={"query":f"SELECT COUNT(*) n FROM trajectories WHERE trajectory_batch_id IN ('{inlist}') AND trajectory_status IN ('pending','running')"},timeout=180)
            if rr.status_code==200:
                rows=rr.json().get("rows",[]); return rows[0]["n"] if rows else 0
            time.sleep(5)
        return 0
    def wait_clear():
        if a.gate<=0: return
        while True:
            fl=inflight()
            if fl<a.gate:
                print(f"  system clear (in-flight {fl} < {a.gate}) — firing",flush=True); return
            print(f"  in-flight {fl} >= {a.gate}, waiting for pool to drain...",flush=True); time.sleep(90)
    out=f"{L.ROOT}/_local/fresh_{a.tag}"; os.makedirs(out,exist_ok=True)
    spath=f"{out}/state.json"
    s=json.load(open(spath)) if os.path.isfile(spath) else {"genuine":{t:[] for t in tasks},"broken":{},"round":0}
    def deficit():
        return {t:a.runs-len(s["genuine"].get(t,[]))-s["broken"].get(t,0) for t in tasks
                if a.runs-len(s["genuine"].get(t,[]))-s["broken"].get(t,0)>0}
    d=deficit(); remaining=sum(d.values())
    print(f"{a.tag}: {len(tasks)} tasks x {a.runs} | to run {remaining}",flush=True)
    if not a.execute:
        print("DRY-RUN"); return
    cur_wave=a.wave   # adaptive: shrinks when a wave's failure rate spikes, recovers when clean
    while remaining>0 and s["round"]<MAX_ROUNDS:
        wait_clear()   # hold until the pool is nearly drained — no pile-on
        s["round"]+=1
        items=[t for t,n in d.items() for _ in range(n)][:cur_wave]
        traj=[{"task_id":t,"orchestrator_id":ORCHS[i%len(ORCHS)][0],"orchestrator_version":ORCHS[i%len(ORCHS)][1],
               "agent_id":AGENT[0],"agent_version":AGENT[1],"system_prompt":SP} for i,t in enumerate(items)]
        body={"trajectory_batch_name":f"{a.tag} GLM-5.2 pass@{a.runs} round {s['round']}",
              "orchestrator_ids":[o[0] for o in ORCHS],"judge_ids":[],"trajectory_request":traj}
        r=requests.post(f"{L.API}/orchestration/trajectories/batch",headers=H,data=json.dumps(body),timeout=300)
        r.raise_for_status(); bid=r.json().get("trajectory_batch_id")
        fired.append(bid)
        print(f"round {s['round']}: dispatched {len(items)} -> {bid}",flush=True)
        R.wait_drain(H,bid)
        rows=R.list_batch(H,bid)
        res=classify_many_run(H,[x["trajectory_id"] for x in rows])
        g=0
        for x in rows:
            v,sc=res[x["trajectory_id"]]; t=x["task_id"]
            if len(s["genuine"].get(t,[]))>=a.runs: continue
            if v=="genuine": s["genuine"].setdefault(t,[]).append(sc); g+=1
        d=deficit(); remaining=sum(d.values())
        json.dump(s,open(spath,"w"))
        frate=1-g/max(1,len(items))
        print(f"  genuine {g}/{len(items)} (fail {frate:.0%}) | remaining {remaining}",flush=True)
        # iterate on measured data: heavy failure = platform congestion -> shrink next wave;
        # clean wave -> step back toward the proven-best width.
        if frate>0.30: cur_wave=max(800,cur_wave//2)
        elif frate<0.15: cur_wave=min(a.wave,cur_wave*2)
        if cur_wave!=len(items): print(f"  next wave width -> {cur_wave}",flush=True)
    done=sum(1 for t in tasks if len(s["genuine"].get(t,[]))>=a.runs or len(s["genuine"].get(t,[]))+s["broken"].get(t,0)>=a.runs)
    print(f"DONE round {s['round']} | tasks settled {done}/{len(tasks)} | broken {sum(s['broken'].values())}",flush=True)

if __name__=="__main__":
    main()
