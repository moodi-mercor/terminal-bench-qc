#!/usr/bin/env python3
"""Label the final delivery tasks in Studio via merge-safe custom_fields PATCH.
Marks each as delivery-ready with the Gemini-3.5-Flash-hard cohort + grade.
Dry-run default; --apply to PATCH. Resumable + concurrent.
"""
import json, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

API="https://api.studio.mercor.com"; KEY="rls-sk-iuA_SCYWiui2xXr_HD7Rab0BfF5VPmDNlK6eAyLzcYw"
H={"Authorization":f"Bearer {KEY}","X-Campaign-Id":"camp_0c1f9a9809604271a534edd77c3cbec1","X-Company-Id":"comp_2fa4115109d741cd94a3c409ed89e61f","X-Account-Id":"acct_85b680d4c5ba49a29f19c173672aebea","User-Agent":"curl/8.7.1","Content-Type":"application/json"}
ROOT="/Users/mahmoodmapara/Desktop/terminal-bench-qc"; G=f"{ROOT}/_local/gemini_flash_qc"
STATE=f"{G}/label_state.txt"; WORKERS=8

LABEL={"qc_delivery": "ready",
       "qc_delivery_grade": "7/10",
       "qc_delivery_cohort": "gemini-3.5-flash-hard",
       "qc_delivery_pass_bucket": None}  # filled per task: "0/8" or "1-2/8"

def one(name, tid, bucket):
    hh={k:v for k,v in H.items() if k!="Content-Type"}
    try:
        cur=requests.get(f"{API}/tasks/{tid}", headers=hh, timeout=60).json()
        cf=cur.get("custom_fields") or {}
        merged={**cf, "qc_delivery":"ready", "qc_delivery_grade":"7/10",
                "qc_delivery_cohort":"gemini-3.5-flash-hard", "qc_delivery_pass_bucket":bucket}
        for i in range(5):
            r=requests.patch(f"{API}/tasks/{tid}", headers=H, data=json.dumps({"custom_fields":merged}), timeout=60)
            if r.status_code in (200,201): return True
            if r.status_code in (429,500,502,503,504): time.sleep(3*(i+1)); continue
            return False
    except Exception:
        return False
    return False

def main():
    apply="--apply" in sys.argv
    per=json.load(open(f"{ROOT}/_local/batch_gemini_flash/per_task.json"))
    final=[t for t in open(f"{G}/final_delivery.txt").read().split() if t]
    done=set(open(STATE).read().split()) if os.path.exists(STATE) else set()
    todo=[t for t in final if t not in done and t in per and per[t].get("task_id")]
    print(f"{'APPLY' if apply else 'DRY-RUN'}: label {len(todo)} tasks ({len(done)} done). Fields: qc_delivery=ready, qc_delivery_grade=7/10, qc_delivery_cohort=gemini-3.5-flash-hard, qc_delivery_pass_bucket=0/8|1-2/8")
    if not apply:
        return
    sf=open(STATE,"a"); ok=0
    def work(t):
        b="0/8" if per[t]["passes"]==0 else "1-2/8"
        return t, one(t, per[t]["task_id"], b)
    with ThreadPoolExecutor(WORKERS) as ex:
        futs={ex.submit(work,t):t for t in todo}
        for n,fut in enumerate(as_completed(futs),1):
            t,good=fut.result()
            if good: sf.write(t+"\n"); sf.flush(); ok+=1
            if n%200==0 or not good: print(f"  [{n}/{len(todo)}] ok={ok}",flush=True)
    print(f"done. labeled {ok}/{len(todo)}")

if __name__=="__main__": main()
