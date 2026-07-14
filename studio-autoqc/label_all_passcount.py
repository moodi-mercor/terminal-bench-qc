import json, os, time
from concurrent.futures import ThreadPoolExecutor
import requests
API="https://api.studio.mercor.com"; KEY="rls-sk-iuA_SCYWiui2xXr_HD7Rab0BfF5VPmDNlK6eAyLzcYw"
CAMP="camp_0c1f9a9809604271a534edd77c3cbec1"
H={"Authorization":f"Bearer {KEY}","X-Campaign-Id":CAMP,"X-Company-Id":"comp_2fa4115109d741cd94a3c409ed89e61f","X-Account-Id":"acct_85b680d4c5ba49a29f19c173672aebea","User-Agent":"curl/8.7.1"}
HJ={**H,"Content-Type":"application/json"}
G="/Users/mahmoodmapara/Desktop/terminal-bench-qc/_local/gemini_flash_qc"
per=json.load(open("/Users/mahmoodmapara/Desktop/terminal-bench-qc/_local/batch_gemini_flash/per_task.json"))
delivered=set(open(f"{G}/final_delivery.txt").read().split())
STATE=f"{G}/allpass_state.txt"
done=set(open(STATE).read().split()) if os.path.exists(STATE) else set()
# ALL scorable tasks (skip deleted -> PATCH will 403/404, we skip those)
todo=[t for t,e in per.items() if t not in done and e.get("task_id") and e.get("runs",0)>0]
print("to label (all scorable, incl skipped 3+/8):",len(todo))
def one(t):
    e=per[t]; tid=e["task_id"]
    fields={"qc_flash_passes":str(e["passes"]),"qc_flash_runs":str(e["runs"]),
            "qc_delivered":("yes" if t in delivered else "no")}
    try:
        g=requests.get(f"{API}/tasks/{tid}",headers=H,timeout=60)
        if g.status_code!=200: return t,"gone"   # deleted / inaccessible -> skip
        cf=g.json().get("custom_fields") or {}
        cf.update(fields)
        for i in range(4):
            r=requests.patch(f"{API}/tasks/{tid}",headers=HJ,data=json.dumps({"custom_fields":cf}),timeout=60)
            if r.status_code in (200,201): return t,"ok"
            if r.status_code in (429,500,502,503,504): time.sleep(3*(i+1)); continue
            return t,f"err{r.status_code}"
    except Exception as ex: return t,"exc"
    return t,"err"
sf=open(STATE,"a"); from collections import Counter; c=Counter()
with ThreadPoolExecutor(10) as ex:
    for t,st in ex.map(one, todo):
        c[st]+=1
        if st in("ok","gone"): sf.write(t+"\n"); sf.flush()
print("result:",dict(c))
