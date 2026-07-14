#!/usr/bin/env python3
"""Import the QC-passed GDM OTS tasks into the new RLS world 'GDM OTS Delivery'.

  --import : bulk-create task records (task_name -> task_id), resumable
  --upload : push each task's filesystem snapshot (concurrent, 429-backoff, resumable)
  --label  : set custom_fields (delivery, delivered_at, qc_status, difficulty, category)

Adapted from rls_import.py. Reads RLS_KEY from repo .env.
"""
import argparse, concurrent.futures as cf, json, os, re, threading, time
import urllib.request, urllib.error, requests

API="https://api.studio.mercor.com"
CAMP="camp_4e196b1414a1499db54b43233104b0a7"; COMP="comp_2fa4115109d741cd94a3c409ed89e61f"; ACCT="acct_85b680d4c5ba49a29f19c173672aebea"
WORLD="world_e8a0072afc594222bce55a1b1db27e72"
ROOT="/Users/mahmoodmapara/Desktop/terminal-bench-qc"
TASKS="/private/tmp/claude-501/-Users-mahmoodmapara-Desktop-terminal-bench-qc/1f204211-5ba8-469d-bfaa-7ae458192941/scratchpad/OTS-GDM-Terminal-Bench/tasks"
G=f"{ROOT}/_local/gdm_ots"
ELIG=f"{G}/qc_pass.txt"; IDMAP=f"{G}/rls_taskids.json"; UPDONE=f"{G}/rls_uploaded.txt"
NOTES="gdm-ots-delivery-2026-07-10"
K=[l.split("=",1)[1].strip().strip('"').strip("'") for l in open(f"{ROOT}/.env") if l.startswith("RLS_KEY=")][0]
H={"Authorization":f"Bearer {K}","X-Campaign-Id":CAMP,"X-Company-Id":COMP,"X-Account-Id":ACCT,"User-Agent":"curl/8.7.1"}
HJ={**H,"Content-Type":"application/json"}
lock=threading.Lock()

def meta(name):
    diff=cat=sub=None
    try:
        for line in open(f"{TASKS}/{name}/task.toml"):
            s=line.strip()
            if s.startswith("difficulty") and diff is None: diff=s.split("=",1)[1].strip().strip('"')
            elif s.startswith("category") and cat is None: cat=s.split("=",1)[1].strip().strip('"')
            elif s.startswith("subcategory") and sub is None: sub=s.split("=",1)[1].strip().strip('"')
    except Exception: pass
    return diff,cat,sub

def do_import():
    names=[t for t in open(ELIG).read().split() if t and os.path.isdir(f"{TASKS}/{t}")]
    idmap=json.load(open(IDMAP)) if os.path.exists(IDMAP) else {}
    todo=[n for n in names if n not in idmap]
    print(f"importing {len(todo)} records (of {len(names)}); {len(idmap)} already mapped",flush=True)
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
    print(f"idmap saved: {len(idmap)} -> {IDMAP}",flush=True)

def upload_one(name,tid):
    tdir=f"{TASKS}/{name}"; paths=[]
    for dp,_,fns in os.walk(tdir):
        if "__pycache__" in dp: continue
        for fn in fns:
            if fn.endswith((".orig",".refactored",".bak",".pyc")) or fn in ("qc_check.md",".DS_Store"): continue
            full=os.path.join(dp,fn); paths.append((f"filesystem/{os.path.relpath(full,tdir)}",full))
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
    print(f"uploading {len(todo)} snapshots, {len(done)} done, {workers} workers",flush=True)
    cnt={};t0=time.time();n=0
    with cf.ThreadPoolExecutor(workers) as ex:
        futs={ex.submit(upload_one,nm,t):nm for nm,t in todo}
        for fut in cf.as_completed(futs):
            nm=futs[fut]; kind,detail=fut.result(); cnt[kind]=cnt.get(kind,0)+1; n+=1
            if kind=="OK":
                with lock: open(UPDONE,"a").write(nm+"\n")
            else: print(f"  [{kind}] {nm}: {detail}",flush=True)
            if n%100==0 or n==len(todo): print(f"  [{n}/{len(todo)}] {cnt} ({n/(time.time()-t0)*60:.0f}/min)",flush=True)
    print("UPLOAD DONE",cnt,flush=True)

def do_label():
    idmap=json.load(open(IDMAP))
    rows=[]
    for name,tid in idmap.items():
        diff,cat,sub=meta(name)
        ups=[("delivery","GDM OTS Delivery"),("delivered_at","2026-07-10"),("qc_status","pass"),
             ("difficulty",diff),("category",cat),("subcategory",sub)]
        rows.append({"task_id":tid,"updates":[{"column_key":f"custom_fields->>'{k}'","value":v} for k,v in ups if v not in (None,"")]})
    ok=0
    for i in range(0,len(rows),500):
        for attempt in range(6):
            r=requests.post(f"{API}/tasks/bulk-update",headers=HJ,json={"updates":rows[i:i+500]},timeout=300)
            if r.status_code in (200,201):
                res=r.json().get("results",[]); ok+=sum(1 for x in res if x.get("success")); break
            if r.status_code==429: time.sleep(13*(attempt+1)); continue
            print(f"  bulk-update {i}: {r.status_code} {r.text[:120]}"); break
        print(f"  labeled {min(i+500,len(rows))}/{len(rows)}",flush=True); time.sleep(2)
    print(f"LABEL DONE ok={ok}/{len(rows)}",flush=True)

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
