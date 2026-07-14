#!/usr/bin/env python3
"""Verify strengthened verifiers on Modal with FRESH state per run:
  fresh sandbox: oracle (solve.sh) -> reward must be 1
  fresh sandbox per SURVIVING mutant -> reward must be 0
Accept (OK) only if oracle passes and every surviving mutant is now killed."""
import argparse,concurrent.futures as cf,json,os,re,threading,time,glob
import modal
lock=threading.Lock(); APP="refl-strengthen-verify"

def _reward1(o):
    try: return abs(float(re.sub(r'[^0-9.]','',o.strip().split()[-1] if o.strip() else 'x'))-1.0)<1e-9
    except: return False

def _run(img, app, timeout, setup):
    sb=modal.Sandbox.create(app=app,image=img,timeout=timeout,cpu=2,memory=4096)
    try:
        script=("cp /cand_test_outputs.py /tests/test_outputs.py; "
                "rm -f /logs/verifier/reward.txt /logs/tests/reward.txt 2>/dev/null; "
                "mkdir -p /logs/tests /logs/verifier /solution; "+setup+
                "bash /tests/test.sh >/tmp/t.log 2>&1; "
                "echo R=$(cat /logs/verifier/reward.txt /logs/tests/reward.txt 2>/dev/null | head -n1 | tr -dc '0-9.')")
        p=sb.exec("bash","-lc",script); o=p.stdout.read(); p.wait()
        m=re.search(r'R=([\d.]*)',o); return m.group(1) if m else ""
    finally:
        try: sb.terminate()
        except: pass

def surviving_muts(task, weak, mutdir):
    meta=[]
    mp=f"{mutdir}/{task}/mutants.json"
    if os.path.exists(mp):
        try: meta=json.load(open(mp))
        except: meta=[]
    sr=set(weak.get(task,{}).get("survived_reqs") or [])
    out=[]
    for m in meta:
        req=m.get("requirement_violated","")
        if not sr or any(req.startswith(x[:40]) or x.startswith(req[:40]) for x in sr):
            f=f"{mutdir}/{task}/mut_{m['i']}.sh"
            if os.path.exists(f): out.append(f)
    if not out: out=sorted(glob.glob(f"{mutdir}/{task}/mut_*.sh"))
    return out

def verify_one(app, D, task, candpath, weak, mutdir, timeout):
    td=os.path.join(D,task)
    try:
        img=(modal.Image.from_dockerfile(f"{td}/environment/Dockerfile", context_dir=f"{td}/environment")
             .add_local_dir(f"{td}/tests", remote_path="/tests")
             .add_local_dir(f"{td}/solution", remote_path="/solution")
             .add_local_file(candpath, remote_path="/cand_test_outputs.py")
             .add_local_dir(f"{mutdir}/{task}", remote_path="/muts"))
        orc=_run(img,app,timeout,"bash /solution/solve.sh >/tmp/s.log 2>&1; ")
        if not _reward1(orc): return task,"FAIL",f"oracle-broke r={orc}"
        for m in surviving_muts(task,weak,mutdir):
            i=os.path.basename(m)
            mr=_run(img,app,timeout,f"cp /muts/{i} /solution/solve.sh; bash /solution/solve.sh >/tmp/m.log 2>&1; ")
            if _reward1(mr): return task,"FAIL",f"{i} still survives"
        return task,"OK",""
    except Exception as e:
        return task,"EXC",str(e)[-100:]

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("delivery"); ap.add_argument("--cand", default="_local/qc_out_delivery/strengthen_cand")
    ap.add_argument("--weak", default="_local/qc_out_delivery/weak_verifiers.json")
    ap.add_argument("--mutdir", default="_local/qc_out_delivery/mut")
    ap.add_argument("--out", default="_local/qc_out_delivery/strengthen_verify.txt")
    ap.add_argument("--state", default="_local/qc_out_delivery/strengthen_verify_state.txt")
    ap.add_argument("--workers", type=int, default=120); ap.add_argument("--timeout", type=int, default=1200)
    a=ap.parse_args()
    app=modal.App.lookup(APP, create_if_missing=True); weak=json.load(open(a.weak))
    done=set(open(a.state).read().split()) if os.path.exists(a.state) else set()
    cands=[f[:-3] for f in os.listdir(a.cand) if f.endswith('.py') and f[:-3] not in done]
    print(f"[verify-strengthen] {len(cands)} candidates ({a.workers}w)",flush=True)
    cnt={};n=0;t0=time.time()
    with cf.ThreadPoolExecutor(a.workers) as ex:
        futs={ex.submit(verify_one,app,a.delivery,t,f"{a.cand}/{t}.py",weak,a.mutdir,a.timeout):t for t in cands}
        for f in cf.as_completed(futs):
            t,v,d=f.result();n+=1;cnt[v]=cnt.get(v,0)+1
            with lock:
                open(a.state,"a").write(t+"\n"); open(a.out,"a").write(f"{t}\t{v}\t{d}\n")
            if v!="OK": print(f"[{v}] {t}: {d[:70]}",flush=True)
            if n%25==0 or n==len(cands): print(f"[{n}/{len(cands)}] {cnt} ({n/(time.time()-t0)*60:.0f}/min)",flush=True)
    print("DONE",cnt,flush=True)
if __name__=="__main__": main()
