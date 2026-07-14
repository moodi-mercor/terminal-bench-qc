#!/usr/bin/env python3
"""Pull the full Reflection RLS world task list and cache it."""
import os,json,requests,sys
def load_env():
    e={}
    for ln in open(".env"):
        ln=ln.strip()
        if '=' in ln and not ln.startswith('#'):
            k,v=ln.split('=',1); e.setdefault(k,v.strip().strip('"'))
    return e
E=load_env()
API="https://api.studio.mercor.com"
WORLD="world_d07785c2757b4a5cb643517cbea8ec98"
H={"Authorization":f"Bearer {E['RLS_KEY']}","X-Campaign-Id":"camp_4e196b1414a1499db54b43233104b0a7","X-Company-Id":"comp_2fa4115109d741cd94a3c409ed89e61f"}
print("fetching world/full ...",flush=True)
r=requests.get(f"{API}/tasks/world/{WORLD}/full",headers=H,timeout=300)
print("status",r.status_code)
d=r.json()
tasks=d.get("result",{}).get("tasks") if isinstance(d.get("result"),dict) else d.get("tasks")
if tasks is None and isinstance(d.get("result"),list): tasks=d["result"]
print("n tasks:",len(tasks) if tasks else 0)
json.dump(tasks, open("_local/refl_world_tasks.json","w"))
if tasks:
    t=tasks[0]; print("keys:",list(t)[:20])
    for k in ('task_id','task_name','status'):
        if k in t: print(" ",k,"=",t[k])
