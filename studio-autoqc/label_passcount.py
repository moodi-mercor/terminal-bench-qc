import json, os, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
API="https://api.studio.mercor.com"; KEY="rls-sk-iuA_SCYWiui2xXr_HD7Rab0BfF5VPmDNlK6eAyLzcYw"
CAMP="camp_0c1f9a9809604271a534edd77c3cbec1"
H={"Authorization":f"Bearer {KEY}","X-Campaign-Id":CAMP,"X-Company-Id":"comp_2fa4115109d741cd94a3c409ed89e61f","X-Account-Id":"acct_85b680d4c5ba49a29f19c173672aebea","User-Agent":"curl/8.7.1"}
HJ={**H,"Content-Type":"application/json"}
G="/Users/mahmoodmapara/Desktop/terminal-bench-qc/_local/gemini_flash_qc"
per=json.load(open("/Users/mahmoodmapara/Desktop/terminal-bench-qc/_local/batch_gemini_flash/per_task.json"))
final=[t for t in open(f"{G}/final_delivery.txt").read().split() if t]
STATE=f"{G}/passcount_state.txt"
done=set(open(STATE).read().split()) if os.path.exists(STATE) else set()
todo=[t for t in final if t not in done and t in per]
print("to label:",len(todo))
def one(t):
    tid=per[t]["task_id"]; pc=str(per[t]["passes"])
    try:
        cf=requests.get(f"{API}/tasks/{tid}",headers=H,timeout=60).json().get("custom_fields") or {}
        if cf.get("qc_flash_passes")==pc: return t,True
        cf["qc_flash_passes"]=pc
        for i in range(5):
            r=requests.patch(f"{API}/tasks/{tid}",headers=HJ,data=json.dumps({"custom_fields":cf}),timeout=60)
            if r.status_code in (200,201): return t,True
            if r.status_code in (429,500,502,503,504): time.sleep(3*(i+1)); continue
            return t,False
    except Exception: return t,False
    return t,False
sf=open(STATE,"a"); ok=0
with ThreadPoolExecutor(8) as ex:
    for t,g in ex.map(one, todo):
        if g: sf.write(t+"\n"); sf.flush(); ok+=1
print("labeled",ok,"/",len(todo))
