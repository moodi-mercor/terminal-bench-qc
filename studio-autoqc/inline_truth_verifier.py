#!/usr/bin/env python3
"""Inline a delegated /tests/.truth verifier into a self-contained test_outputs.py.

Reflection rule: test_outputs.py must contain ALL testing code, not call out to other
scripts. Many tasks shell out to `python3 /tests/.truth/verify.py <mode>` or import a
module from /tests/.truth. This merges that verifier's logic into test_outputs.py so the
file is self-contained, then (apply step) removes the now-unused .truth verifier.

Per task, feed test_outputs.py + every *.py under tests/.truth. Ask the model to emit a
SINGLE self-contained test_outputs.py that reproduces the SAME pass/fail behaviour with the
same pytest function names, inlining the reference logic and keeping all paths/fixtures.

Output: writes candidate to _local/qc_out_delivery/inline_cand/<task>.py (never overwrites
the delivery here — the apply+verify step does that after Modal confirms oracle=1/no-op=0).
"""
import argparse, concurrent.futures as cf, glob, json, os, re, sys, threading
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path[:0]=[HERE]
import fix_leak_api as fl
lock=threading.Lock()

SYS=(
 "You refactor a Terminal-Bench pytest verifier to be SELF-CONTAINED. You are given the "
 "current tests/test_outputs.py and one or more helper verifier scripts that currently live "
 "under tests/.truth/ (the verifier shells out to them or imports them). Produce a SINGLE "
 "test_outputs.py that inlines all needed reference logic so it no longer references "
 "/tests/.truth at all, while preserving EXACT behaviour.\n"
 "HARD REQUIREMENTS:\n"
 "1. Keep the SAME pytest test function names and the SAME pass/fail semantics. A solution "
 "that passed before must still pass; one that failed must still fail.\n"
 "2. Where a test ran `python3 /tests/.truth/verify.py <mode>` and asserted on its output, "
 "replace that with the equivalent inline computation + assertion (same thresholds, same "
 "comparisons). Preserve numeric tolerances exactly.\n"
 "3. Where the verifier imported a module from /tests/.truth, inline that module's code.\n"
 "4. Keep reading real inputs/fixtures from their original paths (/app/..., data files). Only "
 "the /tests/.truth verifier CODE is being inlined, not data-fixture paths that still exist.\n"
 "5. If MULTIPLE verifier scripts are given, merge ALL of them. If one verifier imports another "
 "verifier module that is ALSO provided (e.g. `import ref_impl`, `from src import ...`, `core`, "
 "`decode`), inline that module's code too. Imports of the AGENT's own package under /app stay.\n"
 "6. Assume EVERY third-party/stdlib module the verifier imports (numpy, pandas, yaml, hmac, "
 "zoneinfo, sqlite3, etc.) is ALREADY installed in the image (the original verifier ran there). "
 "NEVER add pip/apt/npm install, uv, or any network call. Keep the imports; do not install.\n"
 "7. No remaining reference to a '.truth' verifier SCRIPT (no `python3 /tests/.truth/*.py`, no "
 "import from .truth, no sys.path into .truth). Must be valid Python importable by pytest.\n"
 "Output ONLY the complete new test_outputs.py inside a single ```python fenced block."
)

RETRY_NOTE=(
 "\n\nYour previous attempt was REJECTED because: {why}. Fix it: inline ALL verifier logic so "
 "there is NO subprocess/import to any /tests/.truth script, do NOT add any install command, "
 "keep every original test_ function name, and emit valid compilable Python. Output ONLY the "
 "complete test_outputs.py in one ```python block."
)

def read(p):
    try: return open(p,encoding='utf-8',errors='replace').read()
    except: return ""

def _strip_dead_truth(code):
    """Remove vestigial _TRUTH_DIR/_truth_file boilerplate the model tends to regenerate,
    when the helper is never called and _TRUTH_DIR is unused."""
    import ast as _ast
    try: tree=_ast.parse(code)
    except: return code
    lines=code.split('\n'); remove=set()
    for n in tree.body:
        if isinstance(n,_ast.FunctionDef) and not n.name.startswith('test_'):
            seg=_ast.get_source_segment(code,n) or ""
            if '.truth' in seg and len(re.findall(r'\b'+re.escape(n.name)+r'\b',code))==1:
                for ln in range(n.lineno-1,n.end_lineno): remove.add(ln)
    for n in tree.body:
        if isinstance(n,_ast.Assign):
            seg=_ast.get_source_segment(code,n) or ""
            if '.truth' in seg and 'open(' not in seg and 'b64decode' not in seg:
                names=[t.id for t in n.targets if isinstance(t,_ast.Name)]
                if names and all(len(re.findall(r'\b'+re.escape(x)+r'\b',code))<=2 for x in names):
                    for ln in range(n.lineno-1,n.end_lineno): remove.add(ln)
    for i,l in enumerate(lines):
        if l.strip().startswith('#') and '.truth' in l: remove.add(i)
    return re.sub(r'\n{4,}','\n\n\n','\n'.join(l for i,l in enumerate(lines) if i not in remove))

STRIP_INSTALL=os.environ.get("STRIP_INSTALL")=="1"

def _strip_install_lines(code):
    """Remove pip/apt/npm/uv install commands (deps get baked into the Dockerfile instead)."""
    out=[]
    for l in code.split('\n'):
        if re.search(r'\b(pip|pip3|apt-get|apt|npm|conda)\s+install\b', l) or re.search(r'\buv\s+pip\s+install\b', l):
            continue
        out.append(l)
    return '\n'.join(out)

def _validate(code, src, to):
    """Return (clean_code, None) if acceptable, else (None, reason)."""
    code=_strip_dead_truth(code.strip()+"\n")
    if STRIP_INSTALL:
        code=_strip_install_lines(code)
    try: compile(code, to, 'exec')
    except Exception as e: return None, f"compile:{str(e)[:50]}"
    noncomment="\n".join(l for l in code.split('\n') if not l.lstrip().startswith('#'))
    if re.search(r'\.truth/[^\s"\']*\.py', noncomment) or re.search(r'sys\.path[^\n]*\.truth', noncomment) \
       or re.search(r'import[^\n]*\.truth', noncomment):
        return None, "residual-delegation"
    if re.search(r'\b(pip|apt-get|npm)\s+install\b', noncomment) or re.search(r'\buv\s+pip\b', noncomment):
        return None, "added-install"
    orig=set(re.findall(r'^def\s+(test_\w+)', src, re.M))
    new=set(re.findall(r'^def\s+(test_\w+)', code, re.M))
    if orig and not orig.issubset(new):
        return None, f"missing-tests:{sorted(orig-new)[:3]}"
    return code, None

def inline_one(key, model, tdir, task, outdir, attempts=2):
    to=os.path.join(tdir,"tests","test_outputs.py")
    src=read(to)
    truth=sorted(glob.glob(os.path.join(tdir,"tests",".truth","**","*.py"),recursive=True))
    if not truth:
        return task, None, "no-truth-py"
    parts=[f"===== tests/test_outputs.py (CURRENT) =====\n{src}"]
    for tp in truth:
        rel=os.path.relpath(tp,tdir)
        parts.append(f"===== {rel} (verifier to inline) =====\n{read(tp)[:60000]}")
    base="\n\n".join(parts)
    last="unknown"
    for att in range(attempts):
        user=base if att==0 else base+RETRY_NOTE.format(why=last)
        resp=fl.call(key, model, SYS, user)
        if "_err" in resp: last=resp["_err"][:60]; continue
        txt="".join(b.get("text","") for b in resp.get("content",[]) if b.get("type")=="text")
        m=re.search(r'```(?:python)?\s*\n(.*?)```', txt, re.S)
        code=m.group(1) if m else txt
        clean, why=_validate(code, src, to)
        if clean:
            os.makedirs(outdir,exist_ok=True)
            open(os.path.join(outdir,f"{task}.py"),'w',encoding='utf-8').write(clean)
            return task, "ok", ""
        last=why
    return task, None, last

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("tasks_dir"); ap.add_argument("--list", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=40)
    ap.add_argument("--model", default="claude-opus-4-8")
    ap.add_argument("--outdir", default="_local/qc_out_delivery/inline_cand")
    a=ap.parse_args()
    key=fl.load_key()
    base=a.tasks_dir
    if a.list and os.path.isfile(a.list):
        tasks=[t for t in (open(a.list).read().split()) if os.path.isdir(f"{base}/{t}")]
    else:
        tasks=[]
        for t in sorted(os.listdir(base)):
            to=os.path.join(base,t,"tests","test_outputs.py")
            if not os.path.isfile(to): continue
            s=read(to)
            if '.truth' not in s: continue
            if re.search(r'sys\.path[^\n]*\.truth',s) or re.search(r'\.truth/[^\s"\']*\.py',s) or \
               re.search(r'(subprocess|check_output|Popen|os\.system|run)\([^\n]*\.truth',s):
                # only if a .truth *.py verifier actually exists to inline
                if glob.glob(os.path.join(base,t,"tests",".truth","**","*.py"),recursive=True):
                    tasks.append(t)
    if a.limit: tasks=tasks[:a.limit]
    print(f"[inline] {len(tasks)} delegation tasks x {a.model} ({a.workers}w)", flush=True)
    ok=0; fail={}; n=0
    with cf.ThreadPoolExecutor(a.workers) as ex:
        futs={ex.submit(inline_one,key,a.model,f"{base}/{t}",t,a.outdir):t for t in tasks}
        for f in cf.as_completed(futs):
            t,st,err=f.result(); n+=1
            if st=="ok": ok+=1
            else: fail[err.split(':')[0]]=fail.get(err.split(':')[0],0)+1
            if n%20==0 or n==len(tasks): print(f"  [{n}/{len(tasks)}] ok={ok} fail={fail}", flush=True)
    print(f"DONE inlined-candidates={ok} failures={fail} -> {a.outdir}", flush=True)

if __name__=="__main__":
    main()
