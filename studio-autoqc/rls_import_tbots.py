#!/usr/bin/env python3
"""Import the tb-ots Gemini-hard delivery (net-new, deduped) into the Canonical world.

Pipeline (per the RLS import runbook):
  --import : bulk-create records in world_2c7cdb (chunked, 5/min, resumable)
  --upload : push each task filesystem as a snapshot (concurrent, 429-backoff, resumable)
  --label  : bulk-update canonical fields (difficulty/domain/subcategory/…) + our
             Gemini + qc fields, keyed custom_fields->>'field_id'
Reads use RLS_KEY (import + upload + label are all authorized with it).
"""
import argparse, concurrent.futures as cf, json, os, re, threading, time
import urllib.request, urllib.error, requests

API="https://api.studio.mercor.com"
CAMP="camp_4e196b1414a1499db54b43233104b0a7";COMP="comp_2fa4115109d741cd94a3c409ed89e61f";ACCT="acct_85b680d4c5ba49a29f19c173672aebea"
WORLD="world_2c7cdb23737845ad83a9acfa1aa8c25b"
ROOT="/Users/mahmoodmapara/Desktop/terminal-bench-qc"; G=f"{ROOT}/_local/tb2400"
TASKS=os.environ.get("IMP_TASKS",f"{G}/import_src")
IDMAP=os.environ.get("IMP_IDMAP",f"{G}/tbots_taskids.json"); UPDONE=os.environ.get("IMP_UPDONE",f"{G}/tbots_uploaded.txt")
IMPIDS=os.environ.get("IMP_IDS",f"{G}/import_ids.json")
NOTES=os.environ.get("IMP_NOTES","terminal-bench-ots gemini-hard 2026-07-10")
K=[l.split("=",1)[1].strip().strip('"').strip("'") for l in open(f"{ROOT}/.env") if l.startswith("RLS_KEY=")][0]
H={"Authorization":f"Bearer {K}","X-Campaign-Id":CAMP,"X-Company-Id":COMP,"X-Account-Id":ACCT,"User-Agent":"curl/8.7.1"}
HJ={**H,"Content-Type":"application/json"}
lock=threading.Lock()
GEM=json.load(open(os.environ.get("IMP_GEM",f"{G}/rls_expanded.json")))

def toml_meta(tid):
    """minimal [metadata] reader from task.toml"""
    p=f"{TASKS}/{tid}/task.toml"; m={}
    if not os.path.exists(p): return m
    inmeta=False
    for line in open(p,encoding="utf-8",errors="replace"):
        s=line.strip()
        if s.startswith("["): inmeta = s=="[metadata]"; continue
        if inmeta and "=" in s:
            k,v=s.split("=",1); k=k.strip(); v=v.strip()
            if v.startswith("[") :
                v=[x.strip().strip('"') for x in v.strip("[]").split(",") if x.strip()]
            else:
                v=v.strip('"')
            m[k]=v
    return m

def do_import():
    ids=json.load(open(IMPIDS))
    ids=[t for t in ids if os.path.isdir(f"{TASKS}/{t}")]
    idmap=json.load(open(IDMAP)) if os.path.exists(IDMAP) else {}
    todo=[t for t in ids if t not in idmap]
    print(f"importing {len(todo)} records (of {len(ids)}), {len(idmap)} done",flush=True)
    B=2000
    for i in range(0,len(todo),B):
        chunk=todo[i:i+B]
        body={"tasks":[{"task_name":n,"notes":NOTES,"custom_fields":{}} for n in chunk]}
        req=urllib.request.Request(f"{API}/worlds/{WORLD}/import-tasks",data=json.dumps(body).encode(),method="POST",headers=HJ)
        for attempt in range(6):
            try:
                with urllib.request.urlopen(req,timeout=300) as r: resp=json.loads(r.read()); break
            except urllib.error.HTTPError as e:
                if e.code==429 and attempt<5: time.sleep(15*(attempt+1)); continue
                raise SystemExit(f"import failed {e.code}: {e.read()[:300].decode(errors='replace')}")
        for res in resp["results"]: idmap[res["task_name"]]=res["task_id"]
        json.dump(idmap,open(IDMAP,"w"))
        print(f"  imported {i+len(chunk)}/{len(todo)}",flush=True); time.sleep(13)
    print(f"records done: {len(idmap)} -> {IDMAP}",flush=True)

def upload_one(name,tid):
    tdir=f"{TASKS}/{name}"; paths=[]
    for dp,_,fns in os.walk(tdir):
        for fn in fns: paths.append((f"filesystem/{os.path.relpath(os.path.join(dp,fn),tdir)}",os.path.join(dp,fn)))
    for attempt in range(7):
        fh=[]
        try:
            files=[]
            for rel,full in paths:
                f=open(full,"rb"); fh.append(f); files.append(("files",(rel,f,"application/octet-stream")))
            r=requests.post(f"{API}/snapshots/task/{tid}/update",headers=H,files=files,timeout=240)
            if r.status_code==201: return "OK",""
            if r.status_code==429: time.sleep(13*(attempt+1)); continue
            return f"ERR{r.status_code}",r.text[:120]
        except Exception as e:
            if attempt<6: time.sleep(5*(attempt+1)); continue
            return "EXC",str(e)[:120]
        finally:
            for f in fh:
                try: f.close()
                except: pass
    return "ERR429","exhausted"

def do_upload(workers):
    idmap=json.load(open(IDMAP))
    done=set(open(UPDONE).read().split()) if os.path.exists(UPDONE) else set()
    todo=[(n,t) for n,t in idmap.items() if n not in done and os.path.isdir(f"{TASKS}/{n}")]
    print(f"uploading {len(todo)}, {len(done)} done, {workers} workers",flush=True)
    cnt={};t0=time.time();n=0
    with cf.ThreadPoolExecutor(workers) as ex:
        futs={ex.submit(upload_one,name,tid):name for name,tid in todo}
        for fut in cf.as_completed(futs):
            name=futs[fut]; kind,detail=fut.result(); cnt[kind]=cnt.get(kind,0)+1; n+=1
            if kind=="OK":
                with lock: open(UPDONE,"a").write(name+"\n")
            else: print(f"  [{kind}] {name}: {detail}",flush=True)
            if n%100==0 or n==len(todo): print(f"  [{n}/{len(todo)}] {cnt} ({n/(time.time()-t0)*60:.0f}/min)",flush=True)
    print("UPLOAD DONE",cnt,flush=True)

def do_label():
    idmap=json.load(open(IDMAP))
    rows=[]
    for name,tid in idmap.items():
        m=toml_meta(name); g=GEM.get(name,{})
        p=g.get("gemini_passes"); r=g.get("gemini_runs")
        diff = "Hard" if (p is not None and p<=2) else ("Medium" if (p is not None and p<=4) else "Easy")
        ups={"difficulty":diff,
             "subcategory":m.get("subcategory"),
             "domain":m.get("category"),
             "operation_type":m.get("operation_type"),
             "expert_time_estimate_min":m.get("expert_time_estimate_min"),
             "junior_time_estimate_min":m.get("junior_time_estimate_min"),
             "qc_status":"pass",
             "harness":"terminal-bench",
             "qc_tb2400":"delivery-candidate",
             "qc_flash_passes":p,"qc_flash_runs":r,
             "qc_difficulty_band":g.get("difficulty_band"),
             "qc_in_difficulty_spec":g.get("in_difficulty_spec"),
             "qc_basis":g.get("qc_basis")}
        upl=[{"column_key":f"custom_fields->>'{k}'","value":v} for k,v in ups.items() if v not in (None,"")]
        rows.append({"task_id":tid,"updates":upl})
    print(f"labeling {len(rows)} tasks",flush=True)
    for i in range(0,len(rows),500):
        r=requests.post(f"{API}/tasks/bulk-update",headers=HJ,json={"updates":rows[i:i+500]},timeout=300)
        ok = r.status_code==200 and all(x.get("success") for x in r.json().get("results",[]))
        if not ok and r.status_code==429:
            time.sleep(20)
            r=requests.post(f"{API}/tasks/bulk-update",headers=HJ,json={"updates":rows[i:i+500]},timeout=300)
            ok = r.status_code==200 and all(x.get("success") for x in r.json().get("results",[]))
        print(f"  [{i+len(rows[i:i+500])}/{len(rows)}] {'ok' if ok else 'PARTIAL '+str(r.status_code)+' '+r.text[:120]}",flush=True)
        time.sleep(14)
    print("LABEL DONE",flush=True)

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--import",dest="imp",action="store_true")
    ap.add_argument("--upload",action="store_true")
    ap.add_argument("--label",action="store_true")
    ap.add_argument("--workers",type=int,default=12)
    a=ap.parse_args()
    if a.imp: do_import()
    if a.upload: do_upload(a.workers)
    if a.label: do_label()
