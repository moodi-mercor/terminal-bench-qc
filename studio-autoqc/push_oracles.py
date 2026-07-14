#!/usr/bin/env python3
"""Push the air-gap-validated offline oracle fixes (solution/solve.sh) to Studio as new
immutable snapshots. Only the 337 tasks that passed the network-blocked gate. Resumable.
Dry-run default; --apply to POST.
"""
import json, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

API="https://api.studio.mercor.com"; KEY="rls-sk-iuA_SCYWiui2xXr_HD7Rab0BfF5VPmDNlK6eAyLzcYw"
CAMP="camp_0c1f9a9809604271a534edd77c3cbec1"; COMP="comp_2fa4115109d741cd94a3c409ed89e61f"; ACCT="acct_85b680d4c5ba49a29f19c173672aebea"
ROOT="/Users/mahmoodmapara/Desktop/terminal-bench-qc"; G=f"{ROOT}/_local/gemini_flash_qc"
H={"Authorization":f"Bearer {KEY}","X-Campaign-Id":CAMP,"X-Company-Id":COMP,"X-Account-Id":ACCT,"User-Agent":"curl/8.7.1"}
STATE=f"{G}/push_oracles_state.jsonl"; WORKERS=4

def upload(tid, content):
    files=[("files",("filesystem/solution/solve.sh",content,"text/x-sh"))]
    for i in range(6):
        r=requests.post(f"{API}/snapshots/task/{tid}/update",headers=H,files=files,timeout=180)
        if r.status_code in (200,201): return True,r.status_code
        if r.status_code in (429,500,502,503,504): time.sleep(13*(i+1)); continue
        return False,f"{r.status_code}:{r.text[:100]}"
    return False,"retries"

def main():
    apply="--apply" in sys.argv
    per=json.load(open(f"{ROOT}/_local/batch_gemini_flash/per_task.json"))
    tasks=[t for t in open(f"{G}/airgap_ok.txt").read().split() if t]
    done=set()
    if os.path.exists(STATE):
        for l in open(STATE):
            d=json.loads(l)
            if d.get("ok"): done.add(d["task"])
    todo=[t for t in tasks if t not in done and t in per and per[t].get("task_id")]
    print(f"{'APPLY' if apply else 'DRY-RUN'}: {len(todo)} solve.sh to push ({len(done)} done)")
    if not apply:
        print("re-run with --apply"); return
    sf=open(STATE,"a"); ok=0
    def work(t):
        content=open(f"{G}/tasks/{t}/solution/solve.sh","rb").read()
        return t, upload(per[t]["task_id"], content)
    with ThreadPoolExecutor(WORKERS) as ex:
        futs={ex.submit(work,t):t for t in todo}
        for n,fut in enumerate(as_completed(futs),1):
            t,(good,info)=fut.result(); ok+=good
            sf.write(json.dumps({"task":t,"ok":good,"info":info})+"\n"); sf.flush()
            if not good or n%25==0: print(f"  [{n}/{len(todo)}] {t}: {info} (ok={ok})",flush=True)
    print(f"done. pushed {ok}/{len(todo)}")

if __name__=="__main__": main()
