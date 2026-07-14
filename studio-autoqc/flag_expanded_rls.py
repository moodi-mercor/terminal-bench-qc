#!/usr/bin/env python3
"""Flag the expanded QC-passing set (any difficulty) in Studio, difficulty-labeled.

Sets on each of the ~2,787 tasks (merge-safe PATCH):
  qc_tb2400            = delivery-candidate
  qc_flash_passes/runs = real Gemini 3.5 Flash pass@8
  qc_difficulty_band   = 0-4/8 | 5-8/8
  qc_in_difficulty_spec= yes | no   (yes = meets the 0-4/8 delivery bar)
  qc_basis             = panel_approved | pipeline_qc
  qc_verdict           = READY
Dry-run default; --apply. Concurrent, resumable.
"""
import json, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

API="https://api.studio.mercor.com"; KEY="rls-sk-iuA_SCYWiui2xXr_HD7Rab0BfF5VPmDNlK6eAyLzcYw"
H={"Authorization":f"Bearer {KEY}","X-Campaign-Id":"camp_0c1f9a9809604271a534edd77c3cbec1",
   "X-Company-Id":"comp_2fa4115109d741cd94a3c409ed89e61f","X-Account-Id":"acct_85b680d4c5ba49a29f19c173672aebea",
   "User-Agent":"curl/8.7.1","Content-Type":"application/json"}
HG={k:v for k,v in H.items() if k!="Content-Type"}
G="/Users/mahmoodmapara/Desktop/terminal-bench-qc/_local/tb2400"
STATE=f"{G}/rls_expanded_state.txt"; WORKERS=10

def patch(tid,v):
    f={"qc_tb2400":"delivery-candidate","qc_flash_passes":v["gemini_passes"],
       "qc_flash_runs":v["gemini_runs"],"qc_difficulty_band":v["difficulty_band"],
       "qc_in_difficulty_spec":v["in_difficulty_spec"],"qc_basis":v["qc_basis"],
       "qc_verdict":"READY","qc_difficulty_source":"gemini_flash"}
    try:
        cur=requests.get(f"{API}/tasks/{tid}",headers=HG,timeout=60).json()
        cf=cur.get("custom_fields") or {}; cf.update(f)
        for i in range(5):
            r=requests.patch(f"{API}/tasks/{tid}",headers=H,data=json.dumps({"custom_fields":cf}),timeout=60)
            if r.status_code in (200,201): return True
            if r.status_code in (429,500,502,503,504): time.sleep(3*(i+1)); continue
            return False
    except Exception:
        return False
    return False

def main():
    apply="--apply" in sys.argv
    sel=json.load(open(f"{G}/rls_expanded.json"))
    done=set(open(STATE).read().split()) if os.path.exists(STATE) else set()
    todo={t:v for t,v in sel.items() if t not in done}
    print(f"{'APPLY' if apply else 'DRY-RUN'}: flag {len(todo)} tasks ({len(done)} done) of {len(sel)}")
    if not apply: return
    sf=open(STATE,"a"); ok=0
    with ThreadPoolExecutor(WORKERS) as ex:
        futs={ex.submit(patch,t,v):t for t,v in todo.items()}
        for n,fut in enumerate(as_completed(futs),1):
            t=futs[fut]
            if fut.result(): sf.write(t+"\n"); sf.flush(); ok+=1
            if n%300==0 or not fut.result(): print(f"  [{n}/{len(todo)}] ok={ok}",flush=True)
    print(f"done. {ok}/{len(todo)}")

if __name__=="__main__": main()
