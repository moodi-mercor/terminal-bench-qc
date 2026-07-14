#!/usr/bin/env python3
"""Remove genuinely-dead .truth scaffolding from test_outputs.py (conservative, gpt-5.6-sol).

For each candidate task, ask the model to strip ONLY dead .truth residue — an unused constant
(_TRUTH_DIR = '/tests/.truth'), an uncalled helper, or a purely-historical comment. If the
path/constant is actually read anywhere (directly or via a variable/param), the model must
LEAVE IT and return the file unchanged. Gate: compile + all test_ functions preserved + no
live read removed. Changed files are written to <cand>/<task>.py for Modal re-verify before apply.
"""
import argparse, concurrent.futures as cf, os, re, sys, threading
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path[:0]=[HERE]
import fix_leak_api as fl
lock=threading.Lock()

SYS=(
 "You clean DEAD leftover scaffolding from a Terminal-Bench pytest verifier (tests/test_outputs.py). "
 "Remove ONLY genuinely-dead '.truth' residue:\n"
 "  - a module-level constant like `_TRUTH_DIR = '/tests/.truth'` that is NEVER used;\n"
 "  - a helper function that references /tests/.truth and is NEVER called;\n"
 "  - a comment/docstring line that merely mentions /tests/.truth for historical reasons.\n"
 "STRICT RULES:\n"
 "1. If the constant or any /tests/.truth path IS actually read or reached anywhere — directly, or "
 "indirectly through a variable, parameter, os.path.join, open(), listdir, glob, subprocess, cmp, "
 "etc. — it is LIVE. Do NOT remove it. If nothing is safely removable, return the file UNCHANGED.\n"
 "2. Never remove, rename, or weaken any test_ function or any assertion. Keep all test_ names.\n"
 "3. Do not add anything. No installs, no new imports, no new .truth references.\n"
 "Output ONLY the complete test_outputs.py inside one ```python fenced block (unchanged if nothing "
 "is safely dead-removable)." )

def clean_one(key, model, tdir, task, outdir):
    to=os.path.join(tdir,"tests","test_outputs.py")
    src=fl.readfile(to) or ""
    if not src: return task,"no-file"
    resp=fl.call(key, model, SYS, f"===== tests/test_outputs.py =====\n{src[:20000]}")
    if "_err" in resp: return task,"err:"+resp["_err"][:50]
    txt="".join(b.get("text","") for b in resp.get("content",[]) if b.get("type")=="text")
    m=re.search(r'```(?:python)?\s*\n(.*?)```', txt, re.S)
    code=(m.group(1) if m else txt).strip()+"\n"
    if code.strip()==src.strip(): return task,"unchanged"
    try: compile(code,to,'exec')
    except Exception as e: return task,f"compile:{str(e)[:40]}"
    orig=set(re.findall(r'^def\s+(test_\w+)',src,re.M)); new=set(re.findall(r'^def\s+(test_\w+)',code,re.M))
    if orig and not orig.issubset(new): return task,"dropped-tests"
    # must not have removed a LIVE read: any non-comment .truth read line present before must remain
    def reads(s): return set(l.strip() for l in s.split('\n')
                             if '.truth' in l and not l.strip().startswith('#')
                             and re.search(r'(open\(|listdir|glob|cmp|subprocess|Popen|run\(|read_bytes|join\()',l))
    if not reads(src).issubset(reads(code)): return task,"removed-live-read"
    os.makedirs(outdir,exist_ok=True)
    open(os.path.join(outdir,task+".py"),"w").write(code)
    return task,"cleaned"

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("repo"); ap.add_argument("--list",required=True)
    ap.add_argument("--cand",default="_local/qc_out_delivery/cleantruth_cand")
    ap.add_argument("--model",default="gpt-5.6-sol"); ap.add_argument("--workers",type=int,default=100)
    a=ap.parse_args()
    key=fl.load_key(); base=os.path.join(os.path.abspath(a.repo),"tasks")
    tasks=[t for t in open(a.list).read().split() if os.path.isdir(os.path.join(base,t))]
    print(f"[clean] {len(tasks)} candidates x {a.model} ({a.workers}w)",flush=True)
    res={};n=0
    with cf.ThreadPoolExecutor(a.workers) as ex:
        futs={ex.submit(clean_one,key,a.model,os.path.join(base,t),t,a.cand):t for t in tasks}
        for f in cf.as_completed(futs):
            t,st=f.result(); res[st]=res.get(st,0)+1; n+=1
            with lock: open(a.cand+"_verdicts.tsv","a").write(f"{t}\t{st}\n")
    print(f"[clean] DONE {res}",flush=True)

if __name__=="__main__":
    main()
