#!/usr/bin/env python3
"""Import net-new client terminal-bench deliveries into the Canonical world + tag
existing ones with delivery provenance (who / which repo / path).

Reads scratchpad/client_del/{netnew.json,existing.json}. Provenance carried per task.
  --import : create records for net-new (unique names), map name->task_id
  --upload : push each net-new task filesystem from its clone dir (scrubbed)
  --label  : set provenance + difficulty/domain custom_fields on net-new
  --tag    : set provenance custom_fields on EXISTING canonical task_ids
All authorized with RLS_KEY.
"""
import argparse, concurrent.futures as cf, json, os, re, threading, time, hashlib
import urllib.request, urllib.error, requests

API="https://api.studio.mercor.com"
CAMP="camp_4e196b1414a1499db54b43233104b0a7";COMP="comp_2fa4115109d741cd94a3c409ed89e61f";ACCT="acct_85b680d4c5ba49a29f19c173672aebea"
WORLD="world_2c7cdb23737845ad83a9acfa1aa8c25b"
ROOT="/Users/mahmoodmapara/Desktop/terminal-bench-qc"
SP="/private/tmp/claude-501/-Users-mahmoodmapara-Desktop-terminal-bench-qc/1f204211-5ba8-469d-bfaa-7ae458192941/scratchpad/client_del"
IDMAP=f"{SP}/cd_taskids.json"; UPDONE=f"{SP}/cd_uploaded.txt"
K=[l.split("=",1)[1].strip().strip('"').strip("'") for l in open(f"{ROOT}/.env") if l.startswith("RLS_KEY=")][0]
H={"Authorization":f"Bearer {K}","X-Campaign-Id":CAMP,"X-Company-Id":COMP,"X-Account-Id":ACCT,"User-Agent":"curl/8.7.1"}
HJ={**H,"Content-Type":"application/json"}
lock=threading.Lock()
SKIP_DIRS={".git","__pycache__"}
SKIP_FILES={".pull_done",".DS_Store",".lint-allowlist.txt","task_qc_review.md"}
SKIP_SUFFIX=(".orig",".refactored",".bak",".realbak",".pyc")

def prov_fields(prov):
    clients=sorted({p["client"] for p in prov})
    repos=sorted({p["repo"] for p in prov})
    paths=sorted({p["repo"]+"/"+p["path"] for p in prov})
    return {"delivered":"yes","delivered_to":",".join(clients),
            "delivered_repo":",".join(repos),"delivered_path":" ; ".join(paths[:8]),
            "source":"client-delivery-import","harness":"terminal-bench"}

def uniq_names(netnew):
    seen={}; names={}
    for i,it in enumerate(netnew):
        base=it["name"] or f"task-{i}"
        n=base
        if n in seen:
            n=f"{base}-{it['hash'][:6]}"
        while n in seen:
            n=f"{base}-{hashlib.sha256((n).encode()).hexdigest()[:4]}"
        seen[n]=1; names[i]=n
    return names

def do_import():
    netnew=json.load(open(f"{SP}/netnew.json"))
    names=uniq_names(netnew)
    idmap=json.load(open(IDMAP)) if os.path.exists(IDMAP) else {}
    # map key by hash (stable) -> {name,task_id}
    todo=[(i,names[i],netnew[i]) for i in range(len(netnew)) if netnew[i]["hash"] not in idmap]
    print(f"importing {len(todo)} records ({len(idmap)} done)",flush=True)
    B=2000
    for s in range(0,len(todo),B):
        chunk=todo[s:s+B]
        body={"tasks":[{"task_name":nm,"notes":"client-delivery "+it["prov"][0]["client"],
                        "custom_fields":{}} for _,nm,it in chunk]}
        req=urllib.request.Request(f"{API}/worlds/{WORLD}/import-tasks",data=json.dumps(body).encode(),method="POST",headers=HJ)
        for attempt in range(6):
            try:
                with urllib.request.urlopen(req,timeout=300) as r: resp=json.loads(r.read()); break
            except urllib.error.HTTPError as e:
                if e.code==429 and attempt<5: time.sleep(15*(attempt+1)); continue
                raise SystemExit(f"import failed {e.code}: {e.read()[:300].decode(errors='replace')}")
        n2id={res["task_name"]:res["task_id"] for res in resp["results"]}
        for _,nm,it in chunk: idmap[it["hash"]]={"name":nm,"task_id":n2id.get(nm)}
        json.dump(idmap,open(IDMAP,"w"))
        print(f"  imported {s+len(chunk)}/{len(todo)}",flush=True); time.sleep(13)
    print(f"records done: {len(idmap)}",flush=True)

def collect_files(d):
    out=[]
    for dp,dns,fns in os.walk(d):
        dns[:]=[x for x in dns if x not in SKIP_DIRS]
        for fn in fns:
            if fn in SKIP_FILES or fn.endswith(SKIP_SUFFIX): continue
            out.append((f"filesystem/{os.path.relpath(os.path.join(dp,fn),d)}",os.path.join(dp,fn)))
    return out

def upload_one(d,tid):
    paths=collect_files(d)
    for attempt in range(7):
        fh=[]
        try:
            files=[]
            for rel,full in paths:
                f=open(full,"rb"); fh.append(f); files.append(("files",(rel,f,"application/octet-stream")))
            r=requests.post(f"{API}/snapshots/task/{tid}/update",headers=H,files=files,timeout=300)
            if r.status_code==201: return "OK"
            if r.status_code==429: time.sleep(13*(attempt+1)); continue
            return f"ERR{r.status_code}:{r.text[:80]}"
        except Exception as e:
            if attempt<6: time.sleep(5*(attempt+1)); continue
            return "EXC:"+str(e)[:80]
        finally:
            for f in fh:
                try: f.close()
                except: pass
    return "ERR429"

def do_upload(workers):
    netnew=json.load(open(f"{SP}/netnew.json")); idmap=json.load(open(IDMAP))
    byhash={it["hash"]:it for it in netnew}
    done=set(open(UPDONE).read().split()) if os.path.exists(UPDONE) else set()
    todo=[(h,v["task_id"],byhash[h]["rep_dir"]) for h,v in idmap.items() if v["task_id"] and h not in done]
    print(f"uploading {len(todo)}, {len(done)} done, {workers} workers",flush=True)
    cnt={};t0=time.time();n=0
    with cf.ThreadPoolExecutor(workers) as ex:
        futs={ex.submit(upload_one,d,tid):h for h,tid,d in todo}
        for fut in cf.as_completed(futs):
            h=futs[fut]; k=fut.result(); kind=k.split(":")[0]; cnt[kind]=cnt.get(kind,0)+1; n+=1
            if kind=="OK":
                with lock: open(UPDONE,"a").write(h+"\n")
            else: print(f"  [{k}] {h}",flush=True)
            if n%200==0 or n==len(todo): print(f"  [{n}/{len(todo)}] {cnt} ({n/(time.time()-t0)*60:.0f}/min)",flush=True)
    print("UPLOAD DONE",cnt,flush=True)

def toml_meta(d):
    p=os.path.join(d,"task.toml"); m={}
    if not os.path.exists(p): return m
    inmeta=False
    for line in open(p,encoding="utf-8",errors="replace"):
        s=line.strip()
        if s.startswith("["): inmeta=s=="[metadata]"; continue
        if inmeta and "=" in s:
            k,v=s.split("=",1); m[k.strip()]=v.strip().strip('"')
    return m

def bulk(rows):
    for i in range(0,len(rows),500):
        for attempt in range(4):
            r=requests.post(f"{API}/tasks/bulk-update",headers=HJ,json={"updates":rows[i:i+500]},timeout=300)
            if r.status_code==200 and all(x.get("success") for x in r.json().get("results",[])): break
            if r.status_code==429: time.sleep(15*(attempt+1)); continue
            print(f"  PARTIAL {r.status_code} {r.text[:100]}",flush=True); break
        print(f"  [{min(i+500,len(rows))}/{len(rows)}]",flush=True); time.sleep(2)

def do_label():
    netnew=json.load(open(f"{SP}/netnew.json")); idmap=json.load(open(IDMAP))
    byhash={it["hash"]:it for it in netnew}
    rows=[]
    for h,v in idmap.items():
        if not v["task_id"]: continue
        it=byhash[h]; m=toml_meta(it["rep_dir"]); f=prov_fields(it["prov"])
        f["difficulty"]=m.get("difficulty") or ""
        f["domain"]=m.get("category") or ""
        f["subcategory"]=m.get("subcategory") or ""
        upl=[{"column_key":f"custom_fields->>'{k}'","value":val} for k,val in f.items() if val]
        rows.append({"task_id":v["task_id"],"updates":upl})
    print(f"labeling {len(rows)} net-new",flush=True); bulk(rows)
    print("LABEL DONE",flush=True)

def do_tag():
    existing=json.load(open(f"{SP}/existing.json"))
    rows=[]
    for e in existing:
        f=prov_fields(e["prov"])
        upl=[{"column_key":f"custom_fields->>'{k}'","value":v} for k,v in f.items() if v]
        rows.append({"task_id":e["task_id"],"updates":upl})
    print(f"tagging {len(rows)} existing with provenance",flush=True); bulk(rows)
    print("TAG DONE",flush=True)

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    for f in ["import","upload","label","tag"]: ap.add_argument("--"+f,dest=f.replace("import","imp"),action="store_true")
    ap.add_argument("--workers",type=int,default=12)
    a=ap.parse_args()
    if a.imp: do_import()
    if a.upload: do_upload(a.workers)
    if a.label: do_label()
    if a.tag: do_tag()
