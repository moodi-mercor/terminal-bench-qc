#!/usr/bin/env python3
"""Re-upload client-del snapshots from the absolute qc_tasks tree (fixes empty snapshots)."""
import concurrent.futures as cf, json, os, threading, time, requests
API="https://api.studio.mercor.com"
CAMP="camp_4e196b1414a1499db54b43233104b0a7";COMP="comp_2fa4115109d741cd94a3c409ed89e61f";ACCT="acct_85b680d4c5ba49a29f19c173672aebea"
K=[l.split("=",1)[1].strip().strip('"').strip("'") for l in open(".env") if l.startswith("RLS_KEY=")][0]
H={"Authorization":f"Bearer {K}","X-Campaign-Id":CAMP,"X-Company-Id":COMP,"X-Account-Id":ACCT,"User-Agent":"curl/8.7.1"}
CD="/private/tmp/claude-501/-Users-mahmoodmapara-Desktop-terminal-bench-qc/1f204211-5ba8-469d-bfaa-7ae458192941/scratchpad/client_del"
QCT=f"{CD}/qc_tasks"; DONE=f"{CD}/cd_reupload_done.txt"
namemap=json.load(open(f"{CD}/qc_namemap.json"))
lock=threading.Lock()
def up(name,tid):
    d=os.path.join(QCT,name); paths=[]
    for dp,dns,fns in os.walk(d):
        dns[:]=[x for x in dns if x not in (".git","__pycache__")]
        for fn in fns:
            if fn.endswith(".pyc"): continue
            paths.append((f"filesystem/{os.path.relpath(os.path.join(dp,fn),d)}",os.path.join(dp,fn)))
    if not paths: return "NOFILES"
    for att in range(7):
        fh=[]
        try:
            files=[]
            for rel,full in paths:
                f=open(full,"rb"); fh.append(f); files.append(("files",(rel,f,"application/octet-stream")))
            r=requests.post(f"{API}/snapshots/task/{tid}/update",headers=H,files=files,timeout=300)
            if r.status_code==201:
                j=r.json(); return "OK" if j.get("snapshot_id") else "EMPTY:"+str(j.get("total_files"))
            if r.status_code==429: time.sleep(13*(att+1)); continue
            return f"ERR{r.status_code}"
        except Exception as e:
            if att<6: time.sleep(5*(att+1)); continue
            return "EXC:"+str(e)[:60]
        finally:
            for f in fh:
                try: f.close()
                except: pass
    return "ERR429"
def main():
    done=set(open(DONE).read().split()) if os.path.exists(DONE) else set()
    todo=[(n,v["task_id"]) for n,v in namemap.items() if v["task_id"] and n not in done]
    print(f"re-uploading {len(todo)} ({len(done)} done)",flush=True)
    cnt={};t0=time.time();n=0
    with cf.ThreadPoolExecutor(12) as ex:
        futs={ex.submit(up,nm,tid):nm for nm,tid in todo}
        for fut in cf.as_completed(futs):
            nm=futs[fut]; k=fut.result(); kind=k.split(":")[0]; cnt[kind]=cnt.get(kind,0)+1; n+=1
            if kind=="OK":
                with lock: open(DONE,"a").write(nm+"\n")
            else: print(f"  [{k}] {nm}",flush=True)
            if n%200==0 or n==len(todo): print(f"  [{n}/{len(todo)}] {cnt} ({n/(time.time()-t0)*60:.0f}/min)",flush=True)
    print("REUPLOAD DONE",cnt,flush=True)
if __name__=="__main__": main()
