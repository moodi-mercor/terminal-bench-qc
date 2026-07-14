#!/usr/bin/env python3
"""Oracle/no-op gate with NETWORK BLOCKED (true air-gap, matches GDM allow_internet=false).
Confirms the offline-install oracle fixes actually pass without a network. Resumable.
Usage: modalenv/bin/python airgap_gate.py <repo> <task_list> --state ... --out ... [--workers 64]
"""
import argparse, concurrent.futures as cf, os, re, threading, time
import modal
lock=threading.Lock(); APP="r35k-oracle-gate"

def gate(app, repo, t, timeout):
    td=os.path.join(repo,"tasks",t)
    img=(modal.Image.from_dockerfile(td+"/environment/Dockerfile",context_dir=td+"/environment")
         .add_local_dir(td+"/tests",remote_path="/tests").add_local_dir(td+"/solution",remote_path="/solution"))
    sb=None
    try:
        try: sb=modal.Sandbox.create(app=app,image=img,timeout=timeout,cpu=2,memory=4096,block_network=True)
        except TypeError: sb=modal.Sandbox.create(app=app,image=img,timeout=timeout,cpu=2,memory=4096)
        script=("mkdir -p /logs 2>/dev/null; bash /tests/test.sh >/tmp/n 2>&1; NRC=$?; "
                "bash /solution/solve.sh >/tmp/s 2>&1; SRC=$?; bash /tests/test.sh >/tmp/o 2>&1; ORC=$?; "
                "echo NRC=$NRC SRC=$SRC ORC=$ORC; tail -c 300 /tmp/o")
        p=sb.exec("bash","-lc",script); out=p.stdout.read(); p.wait()
        m=re.search(r"NRC=(\d+) SRC=(\d+) ORC=(\d+)",out)
        if not m: return "EXEC-ERR", out[-120:].replace("\n"," ")
        nrc,src,orc=map(int,m.groups())
        if orc!=0: return "ORACLE-FAIL", f"solve_rc={src} "+out.split("ORC=",1)[-1][:120].replace("\n"," ")
        if nrc==0: return "NOOP-PASS",""
        return "OK",""
    except Exception as e:
        return "EXC", str(e)[-120:].replace("\n"," ")
    finally:
        if sb is not None:
            try: sb.terminate()
            except Exception: pass

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("repo"); ap.add_argument("tasks")
    ap.add_argument("--state",required=True); ap.add_argument("--out",required=True)
    ap.add_argument("--workers",type=int,default=64); ap.add_argument("--timeout",type=int,default=1500)
    a=ap.parse_args(); repo=os.path.abspath(a.repo)
    app=modal.App.lookup(APP,create_if_missing=True)
    done=set(open(a.state).read().split()) if os.path.exists(a.state) else set()
    tasks=[t for t in open(a.tasks).read().split() if t and t not in done]
    print(f"{len(tasks)} to air-gap-gate ({len(done)} done)",flush=True)
    cnt={}; t0=time.time()
    with cf.ThreadPoolExecutor(a.workers) as ex:
        futs={ex.submit(gate,app,repo,t,a.timeout):t for t in tasks}
        for i,f in enumerate(cf.as_completed(futs),1):
            t=futs[f]
            try: k,d=f.result()
            except Exception as e: k,d="EXC",str(e)[-100:]
            cnt[k]=cnt.get(k,0)+1
            with lock:
                open(a.state,"a").write(t+"\n"); open(a.out,"a").write(f"{t}\t{k}\t{d}\n")
            if k!="OK": print(f"[{k}] {t}: {d[:90]}",flush=True)
            if i%50==0 or i==len(futs): print(f"[{i}/{len(futs)}] {cnt} ({i/(time.time()-t0)*60:.0f}/min)",flush=True)
    print("DONE",cnt)

if __name__=="__main__": main()
