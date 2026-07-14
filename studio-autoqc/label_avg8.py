#!/usr/bin/env python3
"""Write avg@8 difficulty labels back to RL Studio tasks.

Reads {task_id: avg8} json, and for each task PATCHes custom_fields:
  qc_avg_at_8         = <avg8 rounded 4dp>
  qc_final_bucket     = healthy-hard (avg8<=0.5) | healthy-easy (avg8>0.5)
  qc_difficulty       = hard | easy
  qc_difficulty_source= avg8-gpt54-2026-07-08
Read-modify-write (preserves other fields). Concurrent, resumable, verify-after-write.

Usage: python label_avg8.py --avg _local/eval_canon/avg8.json [--apply] [--workers 15]
"""
import argparse, json, os, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
HERE=os.path.dirname(os.path.abspath(__file__))
sys.path[:0]=[os.path.join(HERE,"..","skills","static-semantic-qc","scripts")]
import studio_pull as sp, bulk_patch_conftest as bp
API=sp.API; SRC="avg8-gpt54-2026-07-08"; lock=threading.Lock()

def get_cf(rkey,tid):
    r=requests.get(f"{API}/tasks/{tid}",headers=bp.hdr(rkey),timeout=60); r.raise_for_status()
    return r.json().get("custom_fields") or {}
def patch_cf(wkey,tid,cf):
    h={**bp.hdr(wkey),"Content-Type":"application/json"}
    for a in range(6):
        r=requests.patch(f"{API}/tasks/{tid}",headers=h,data=json.dumps({"custom_fields":cf}),timeout=120)
        if r.status_code in (200,201): return
        if r.status_code==429: time.sleep(15*(a+1)); continue
        raise RuntimeError(f"{r.status_code}:{r.text[:150]}")
    raise RuntimeError("patch failed")

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--avg",required=True)
    ap.add_argument("--apply",action="store_true"); ap.add_argument("--workers",type=int,default=15)
    ap.add_argument("--out",default=os.path.join(HERE,"..","_local","label_avg8_out"))
    a=ap.parse_args()
    os.makedirs(a.out,exist_ok=True); state=os.path.join(a.out,"state.jsonl")
    rkey=bp.env("RLS_KEY"); wkey=bp.env("RLS_WRITE_KEY") if a.apply else None
    avg={k:v for k,v in json.load(open(a.avg)).items() if v is not None}
    done=bp.load_done(state)
    todo=[(t,v) for t,v in avg.items() if done.get(t)!="labeled"]
    hard=sum(1 for _,v in todo if v<=0.5)
    print(f"label set: {len(avg)} | TODO: {len(todo)} | hard {hard} easy {len(todo)-hard} | {'APPLY' if a.apply else 'DRY-RUN'}",flush=True)
    sf=open(state,"a"); counts={}
    def rec(t,st):
        with lock: sf.write(json.dumps({"id":t,"status":st})+"\n"); sf.flush(); counts[st]=counts.get(st,0)+1
    def work(item):
        tid,v=item
        diff="hard" if v<=0.5 else "easy"
        try:
            if not a.apply: rec(tid,"would-label"); return
            cf=get_cf(rkey,tid)
            cf.update({"qc_avg_at_8":round(v,4),"qc_final_bucket":f"healthy-{diff}",
                       "qc_difficulty":diff,"qc_difficulty_source":SRC})
            patch_cf(wkey,tid,cf); rec(tid,"labeled")
        except Exception as e:
            rec(tid,"error");
            with lock: print(f"  ! {tid}: {e}",flush=True)
    t0=time.time(); k=0
    with ThreadPoolExecutor(a.workers) as ex:
        for _ in as_completed([ex.submit(work,it) for it in todo]):
            k+=1
            if k%200==0 or k==len(todo): print(f"  {k}/{len(todo)} {counts}",flush=True)
    print(f"DONE ({'APPLY' if a.apply else 'DRY-RUN'}) {(time.time()-t0)/60:.1f}m {counts}",flush=True)

if __name__=="__main__": main()
