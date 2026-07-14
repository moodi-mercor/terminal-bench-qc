#!/usr/bin/env python3
"""For each contract-path FAIL, build the task image on Modal and classify the flagged path:
 INPUT-PRESENT (exists after build), OUTPUT (created by solve.sh), or MISSING (never exists)."""
import os,sys,json,re,concurrent.futures as cf,threading
import modal
D=os.path.abspath("_local/refl_eval_pool/delivery_0708")
fails=json.load(open("_local/qc_out_delivery/cp_fail_paths.json"))
lock=threading.Lock()
app=modal.App.lookup("refl-reward-gate-v2",create_if_missing=False)
def norm(p): return p if p.startswith("/") else "/app/"+p
def probe(task,paths):
    tdir=os.path.join(D,task)
    try:
        img=(modal.Image.from_dockerfile(f"{tdir}/environment/Dockerfile",context_dir=f"{tdir}/environment")
             .add_local_dir(f"{tdir}/tests",remote_path="/tests")
             .add_local_dir(f"{tdir}/solution",remote_path="/solution"))
        sb=modal.Sandbox.create(app=app,image=img,timeout=600,cpu=2,memory=4096)
        checks=" ".join(f'echo "PRE {p} $([ -e {norm(p)} ] && echo YES || echo NO)";' for p in paths)
        post=" ".join(f'echo "POST {p} $([ -e {norm(p)} ] && echo YES || echo NO)";' for p in paths)
        script=f"{checks} bash /solution/solve.sh >/tmp/s.log 2>&1; {post}"
        pr=sb.exec("bash","-lc",script); out=pr.stdout.read(); pr.wait(); sb.terminate()
        res={}
        for p in paths:
            pre=re.search(rf'PRE {re.escape(p)} (YES|NO)',out)
            po=re.search(rf'POST {re.escape(p)} (YES|NO)',out)
            pre=pre.group(1) if pre else '?'; po=po.group(1) if po else '?'
            res[p]="INPUT-PRESENT" if pre=="YES" else ("OUTPUT" if po=="YES" else "MISSING")
        return task,res,""
    except Exception as e:
        return task,{},str(e)[-100:]
results={}
with cf.ThreadPoolExecutor(9) as ex:
    for f in cf.as_completed([ex.submit(probe,t,p) for t,p in fails.items()]):
        t,r,e=f.result(); results[t]=r or {"_err":e}
        print(f"{t}: {r or e}",flush=True)
json.dump(results,open("_local/qc_out_delivery/cp_probe.json","w"),indent=1)
