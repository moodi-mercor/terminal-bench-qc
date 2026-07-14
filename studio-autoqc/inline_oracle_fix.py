#!/usr/bin/env python3
"""Feedback re-inline for tasks whose inlined verifier passed structural gates but FAILED
the Modal oracle (reward!=1). Feed the original test_outputs.py + the .truth verifier(s) +
the failed candidate + the oracle error, and ask for a corrected self-contained version.
"""
import argparse, concurrent.futures as cf, glob, json, os, re, sys, threading
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path[:0]=[HERE]
import fix_leak_api as fl
import inline_truth_verifier as base
lock=threading.Lock()

SYS=(base.SYS+"\n\nThis is a CORRECTION task. A prior inlined version was produced but the "
 "REFERENCE solution (oracle) then FAILED the verifier (reward=0, should be 1). That means the "
 "inlining introduced a bug: a dropped global/constant, a scoping error, a missing setup step, "
 "a changed threshold, or a side effect the original .truth verifier performed that you omitted. "
 "Study the ORIGINAL verifier scripts and the ERROR, and emit a corrected, fully self-contained "
 "test_outputs.py that the reference solution passes. Preserve exact semantics/thresholds and all "
 "test_ names. Output ONLY the complete file in one ```python block.")

def fix_one(key, model, tdir, task, cand_dir, err, outdir):
    to=os.path.join(tdir,"tests","test_outputs.py")
    src=base.read(to)
    truth=sorted(glob.glob(os.path.join(tdir,"tests",".truth","**","*.py"),recursive=True))
    cand=base.read(os.path.join(cand_dir,task+".py"))
    parts=[f"===== tests/test_outputs.py (ORIGINAL, delegating) =====\n{src}"]
    for tp in truth:
        parts.append(f"===== {os.path.relpath(tp,tdir)} (verifier) =====\n{base.read(tp)[:60000]}")
    parts.append(f"===== FAILED inlined candidate =====\n{cand}")
    parts.append(f"===== ORACLE ERROR (reference solution failed this) =====\n{err}")
    resp=fl.call(key, model, SYS, "\n\n".join(parts))
    if "_err" in resp: return task, None, resp["_err"][:60]
    txt="".join(b.get("text","") for b in resp.get("content",[]) if b.get("type")=="text")
    m=re.search(r'```(?:python)?\s*\n(.*?)```', txt, re.S)
    code=m.group(1) if m else txt
    clean, why=base._validate(code, src, to)
    if not clean: return task, None, why
    os.makedirs(outdir,exist_ok=True)
    open(os.path.join(outdir,task+".py"),'w',encoding='utf-8').write(clean)
    return task, "ok", ""

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("tasks_dir"); ap.add_argument("results")   # Modal results.txt
    ap.add_argument("--cand-dir", default="_local/qc_out_delivery/inline_cand")
    ap.add_argument("--outdir", default="_local/qc_out_delivery/inline_cand2")
    ap.add_argument("--workers", type=int, default=18)
    ap.add_argument("--model", default="claude-opus-4-8")
    a=ap.parse_args()
    key=fl.load_key(); base_dir=a.tasks_dir
    errs={}
    for l in open(a.results):
        p=l.rstrip("\n").split("\t")
        if len(p)>=3 and p[1]=="ORACLE-FAIL": errs[p[0]]=p[2]
    # only those that still have a candidate to correct and still delegate
    tasks=[t for t in errs if os.path.isfile(os.path.join(a.cand_dir,t+".py"))
           and os.path.isdir(os.path.join(base_dir,t))]
    print(f"[oracle-fix] {len(tasks)} tasks x {a.model} ({a.workers}w)", flush=True)
    ok=0; fail={}; n=0
    with cf.ThreadPoolExecutor(a.workers) as ex:
        futs={ex.submit(fix_one,key,a.model,f"{base_dir}/{t}",t,a.cand_dir,errs[t],a.outdir):t for t in tasks}
        for f in cf.as_completed(futs):
            t,st,why=f.result(); n+=1
            if st=="ok": ok+=1
            else: fail[str(why).split(':')[0]]=fail.get(str(why).split(':')[0],0)+1
    print(f"DONE corrected-candidates={ok} fail={fail} -> {a.outdir}", flush=True)

if __name__=="__main__":
    main()
