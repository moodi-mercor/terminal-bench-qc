#!/usr/bin/env python3
"""Write qc_final_bucket onto every task (merge-safe, resumable, idempotent).

One field capturing the consolidated bucket so each bucket — including the healthy
ones — is queryable/dashboardable. Does NOT touch other qc_* fields.
"""
import json, os, sys, time, requests
ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
API = "https://api.studio.mercor.com"
def k(n):
    for l in open(f"{ROOT}/.env"):
        if l.startswith(n + "="): return l.split("=",1)[1].strip().strip('"').strip("'")
    sys.exit(n)
H={"Authorization":f"Bearer {k('RLS_WRITE_KEY')}","X-Campaign-Id":"camp_4e196b1414a1499db54b43233104b0a7","X-Company-Id":"comp_2fa4115109d741cd94a3c409ed89e61f","Content-Type":"application/json"}
M=json.load(open(f"{ROOT}/_local/final_bucket_map.json"))
items=list(M.items())
applied=skipped=failed=0
for i,(tid,bucket) in enumerate(items,1):
    try:
        cf=requests.get(f"{API}/tasks/{tid}",headers=H,timeout=60).json().get("custom_fields") or {}
    except Exception: failed+=1; continue
    if cf.get("qc_final_bucket")==bucket: skipped+=1; continue
    try:
        r=requests.patch(f"{API}/tasks/{tid}",headers=H,data=json.dumps({"custom_fields":{**cf,"qc_final_bucket":bucket}}),timeout=60)
        applied += 1 if r.status_code==200 else 0
        failed += 0 if r.status_code==200 else 1
    except Exception: failed+=1
    time.sleep(0.04)
    if i%500==0: print(f"  {i}/{len(items)} applied={applied} skipped={skipped} failed={failed}",flush=True)
print(f"done: applied {applied}, skipped {skipped}, failed {failed}")
