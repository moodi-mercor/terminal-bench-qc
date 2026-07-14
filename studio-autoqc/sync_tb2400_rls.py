#!/usr/bin/env python3
"""Sync the Studio qc_tb2400 delivery flag to the final (v3) 2,400 set.

Phase 1 (untag): null the qc_* fields on tasks tagged but no longer in the set.
Phase 2 (tag/update): set the flag + real Gemini pass@8 numbers on the final 2,400.
Merge-safe PATCH, concurrent, resumable. Dry-run default; --apply to write.
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
STATE=f"{G}/rls_sync_state.txt"; WORKERS=10

QC_KEYS=["qc_tb2400","qc_tb2400_source","qc_difficulty_source","qc_flash_passes",
         "qc_flash_runs","qc_oracle","qc_leak_probe","qc_verdict","qc_basis"]

def patch(tid, fields):
    try:
        cur=requests.get(f"{API}/tasks/{tid}",headers=HG,timeout=60).json()
        cf=cur.get("custom_fields") or {}
        cf.update(fields)
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
    final=json.load(open(f"{G}/final_2400_v3.json"))
    untag=json.load(open(f"{G}/rls_untag.json"))
    done=set(open(STATE).read().split()) if os.path.exists(STATE) else set()
    jobs=[]
    for tid in untag:
        if tid in done: continue
        jobs.append((tid, {k:None for k in QC_KEYS}))
    for tid,v in final.items():
        if tid in done: continue
        jobs.append((tid, {"qc_tb2400":"delivery-candidate","qc_difficulty_source":"gemini_flash",
                           "qc_flash_passes":v["gemini_passes"],"qc_flash_runs":v["gemini_runs"],
                           "qc_basis":v["qc_basis"],"qc_verdict":"READY"}))
    print(f"{'APPLY' if apply else 'DRY-RUN'}: {len(untag)} untag + {len(final)} tag/update = {len(jobs)} PATCHes ({len(done)} done)")
    if not apply: return
    sf=open(STATE,"a"); ok=0
    with ThreadPoolExecutor(WORKERS) as ex:
        futs={ex.submit(patch,t,f):t for t,f in jobs}
        for n,fut in enumerate(as_completed(futs),1):
            t=futs[fut]
            if fut.result(): sf.write(t+"\n"); sf.flush(); ok+=1
            if n%300==0 or not fut.result(): print(f"  [{n}/{len(jobs)}] ok={ok}",flush=True)
    print(f"done. {ok}/{len(jobs)}")

if __name__=="__main__": main()
