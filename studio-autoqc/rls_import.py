#!/usr/bin/env python3
"""Import the Reflection delivery_2 eval pool into a new RLS world.

Pipeline:
  1. bulk-import all task records (one call) -> task_id per task_name
  2. upload each Harbor task filesystem as a snapshot (concurrent workers, 429-backoff, resumable)
Then bulk-tag + custom view are done separately.

Resumable: task_id map cached to rls_taskids.json; uploaded tasks recorded in rls_uploaded.txt.
Usage: python rls_import.py [--import] [--upload] [--workers 12]
"""
import argparse, concurrent.futures as cf, json, os, threading, time
import urllib.request, urllib.error, requests

API="https://api.studio.mercor.com"
CAMP="camp_4e196b1414a1499db54b43233104b0a7";COMP="comp_2fa4115109d741cd94a3c409ed89e61f";ACCT="acct_85b680d4c5ba49a29f19c173672aebea"
WORLD="world_d07785c2757b4a5cb643517cbea8ec98"
ROOT="/Users/mahmoodmapara/Desktop/terminal-bench-qc"
TASKS=f"{ROOT}/_local/refl_eval_pool/eval-candidate-pool/tasks"
OUT=f"{ROOT}/_local/qc_out_eval_pool"
ELIG=f"{OUT}/final_eligible.txt"
IDMAP=f"{OUT}/rls_taskids.json"
UPDONE=f"{OUT}/rls_uploaded.txt"
K=[l.split("=",1)[1].strip().strip('"').strip("'") for l in open(f"{ROOT}/.env") if l.startswith("RLS_KEY=")][0]
H={"Authorization":f"Bearer {K}","X-Campaign-Id":CAMP,"X-Company-Id":COMP,"X-Account-Id":ACCT,"User-Agent":"curl/8.7.1"}
HJ={**H,"Content-Type":"application/json"}
lock=threading.Lock()
# already imported+uploaded during dry run
PREDONE={"rail-journal-seal-4cfa6859":"task_69edecf2dd8c4defb9430c697e71143c"}


def do_import():
    names=[t for t in open(ELIG).read().split() if t and os.path.isdir(f"{TASKS}/{t}")]
    idmap=dict(PREDONE)
    todo=[n for n in names if n not in idmap]
    print(f"importing {len(todo)} task records (of {len(names)} eligible)...",flush=True)
    B=2000
    for i in range(0,len(todo),B):
        chunk=todo[i:i+B]
        body={"tasks":[{"task_name":n,"notes":"reflection-eval-2026-07-08","custom_fields":{}} for n in chunk]}
        req=urllib.request.Request(f"{API}/worlds/{WORLD}/import-tasks",data=json.dumps(body).encode(),method="POST",headers=HJ)
        for attempt in range(6):
            try:
                with urllib.request.urlopen(req,timeout=300) as r:
                    resp=json.loads(r.read()); break
            except urllib.error.HTTPError as e:
                if e.code==429 and attempt<5: time.sleep(15*(attempt+1)); continue
                raise SystemExit(f"import failed {e.code}: {e.read()[:300].decode(errors='replace')}")
        for res in resp["results"]: idmap[res["task_name"]]=res["task_id"]
        print(f"  imported {i+len(chunk)}/{len(todo)}",flush=True)
        time.sleep(13)  # respect 5/min
    json.dump(idmap,open(IDMAP,"w"))
    print(f"task_id map saved: {len(idmap)} -> {IDMAP}",flush=True)


def upload_one(name,tid):
    tdir=f"{TASKS}/{name}"
    paths=[]
    for dp,_,fns in os.walk(tdir):
        for fn in fns:
            if fn.endswith((".orig",".refactored",".bak")): continue
            full=os.path.join(dp,fn)
            paths.append((f"filesystem/{os.path.relpath(full,tdir)}",full))
    for attempt in range(7):
        fh=[]
        try:
            files=[]
            for rel,full in paths:
                f=open(full,"rb"); fh.append(f)
                files.append(("files",(rel,f,"application/octet-stream")))
            r=requests.post(f"{API}/snapshots/task/{tid}/update",headers=H,files=files,timeout=240)
            if r.status_code==201: return "OK",""
            if r.status_code==429: time.sleep(13*(attempt+1)); continue
            return f"ERR{r.status_code}", r.text[:120]
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
    print(f"uploading snapshots: {len(todo)} to do, {len(done)} done, {workers} workers",flush=True)
    cnt={};t0=time.time();n=0
    with cf.ThreadPoolExecutor(workers) as ex:
        futs={ex.submit(upload_one,name,tid):name for name,tid in todo}
        for fut in cf.as_completed(futs):
            name=futs[fut]; kind,detail=fut.result(); cnt[kind]=cnt.get(kind,0)+1; n+=1
            if kind=="OK":
                with lock: open(UPDONE,"a").write(name+"\n")
            else: print(f"  [{kind}] {name}: {detail}",flush=True)
            if n%50==0 or n==len(todo):
                print(f"  [{n}/{len(todo)}] {cnt} ({n/(time.time()-t0)*60:.0f}/min)",flush=True)
    print("UPLOAD DONE",cnt,flush=True)


if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--import",dest="imp",action="store_true")
    ap.add_argument("--upload",action="store_true")
    ap.add_argument("--workers",type=int,default=12)
    a=ap.parse_args()
    if a.imp: do_import()
    if a.upload: do_upload(a.workers)
