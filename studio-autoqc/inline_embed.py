#!/usr/bin/env python3
"""Round-4 inliner: EMBED the .truth verifier source VERBATIM (no logic rewrite) and run it
in-process via a shim, then rewire the subprocess/import call sites. This kills the
"dropped global / semantic drift" failure class from the reproduce-the-logic approach.

The model is told: paste each .truth script's source EXACTLY as a raw string constant, add
the provided _run_embedded() shim, and replace every `subprocess.run(... .truth/X.py ARGS)`
(or import from .truth) with a call that execs the embedded source with those args. Preserve
exact stdout/returncode semantics the asserts depend on.
"""
import argparse, concurrent.futures as cf, glob, os, re, sys, threading
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path[:0]=[HERE]
import fix_leak_api as fl
import inline_truth_verifier as base

SHIM = '''# --- embedded verifier runner (runs a verifier's source in-process, capturing stdout/rc) ---
def _run_embedded(_src, _argv):
    import sys as _sys, io as _io, contextlib as _cl
    _buf = _io.StringIO(); _rc = 0
    _old = _sys.argv; _sys.argv = ["verifier"] + [str(_a) for _a in _argv]
    try:
        with _cl.redirect_stdout(_buf), _cl.redirect_stderr(_buf):
            try:
                exec(compile(_src, "<embedded-verifier>", "exec"), {"__name__": "__main__"})
            except SystemExit as _e:
                _rc = _e.code if isinstance(_e.code, int) else (1 if _e.code else 0)
    finally:
        _sys.argv = _old
    class _R: pass
    _r = _R(); _r.stdout = _buf.getvalue(); _r.stderr = ""; _r.returncode = _rc
    return _r
'''

SYS=(
 "You make a Terminal-Bench pytest verifier SELF-CONTAINED by EMBEDDING the helper verifier "
 "script(s) it currently calls under /tests/.truth, WITHOUT rewriting their logic.\n"
 "You are given tests/test_outputs.py (which shells out to or imports one or more /tests/.truth "
 "scripts) and the full source of each such script.\n"
 "PRODUCE a single test_outputs.py that:\n"
 "1. Includes this EXACT shim verbatim (do not modify it):\n" + SHIM + "\n"
 "2. Embeds each /tests/.truth script's source EXACTLY as given, as a raw triple-quoted string "
 "constant (e.g. _TRUTH_SRC_1 = r'''<paste the ENTIRE script source unchanged>'''). Do NOT edit, "
 "summarize, or re-derive the script's logic, globals, or thresholds. Copy it byte-for-byte. If a "
 "triple-single-quote appears in the source, use a triple-double-quote wrapper (or vice versa).\n"
 "3. Replaces every call that ran a .truth script (e.g. subprocess.run('python3 "
 "/tests/.truth/verify.py compile'), or via a _run() helper) with `_run_embedded(_TRUTH_SRC_k, "
 "[<the args after the script path>])`. The returned object has .stdout/.stderr/.returncode, so "
 "existing assertions on result.stdout / result.returncode keep working unchanged.\n"
 "4. If test_outputs.py IMPORTED a module from /tests/.truth (sys.path.insert + import X), instead "
 "paste that module's source verbatim into test_outputs.py so the symbols are defined locally.\n"
 "5. Keeps EVERY original test_ function name and all other logic identical. Keep reading real "
 "data fixtures from their original paths. No remaining reference to a .truth SCRIPT path, no "
 "subprocess to .truth, no import from .truth. No pip/apt/uv install. Valid importable Python.\n"
 "Output ONLY the complete test_outputs.py in one ```python fenced block."
)

lock=threading.Lock()

def embed_one(key, model, tdir, task, outdir, attempts=2):
    to=os.path.join(tdir,"tests","test_outputs.py")
    src=base.read(to)
    truth=sorted(glob.glob(os.path.join(tdir,"tests",".truth","**","*.py"),recursive=True))
    if not truth: return task, None, "no-truth-py"
    parts=[f"===== tests/test_outputs.py (CURRENT) =====\n{src}"]
    for tp in truth:
        parts.append(f"===== {os.path.relpath(tp,tdir)} (embed this VERBATIM) =====\n{base.read(tp)[:80000]}")
    base_msg="\n\n".join(parts); last="unknown"
    for att in range(attempts):
        user=base_msg if att==0 else base_msg+base.RETRY_NOTE.format(why=last)
        resp=fl.call(key, model, SYS, user)
        if "_err" in resp: last=resp["_err"][:60]; continue
        txt="".join(b.get("text","") for b in resp.get("content",[]) if b.get("type")=="text")
        m=re.search(r'```(?:python)?\s*\n(.*?)```', txt, re.S)
        code=m.group(1) if m else txt
        clean, why=base._validate(code, src, to)
        if clean:
            os.makedirs(outdir,exist_ok=True)
            open(os.path.join(outdir,task+".py"),'w',encoding='utf-8').write(clean)
            return task,"ok",""
        last=why
    return task,None,last

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("tasks_dir"); ap.add_argument("--list", required=True)
    ap.add_argument("--workers", type=int, default=200)
    ap.add_argument("--model", default="claude-opus-4-8")
    ap.add_argument("--outdir", default="_local/qc_out_delivery/inline_cand4")
    a=ap.parse_args()
    key=fl.load_key(); base_dir=a.tasks_dir
    tasks=[t for t in open(a.list).read().split() if os.path.isdir(f"{base_dir}/{t}")]
    print(f"[embed] {len(tasks)} tasks x {a.model} ({a.workers}w)", flush=True)
    ok=0; fail={}; n=0
    with cf.ThreadPoolExecutor(a.workers) as ex:
        futs={ex.submit(embed_one,key,a.model,f"{base_dir}/{t}",t,a.outdir):t for t in tasks}
        for f in cf.as_completed(futs):
            t,st,why=f.result(); n+=1
            if st=="ok": ok+=1
            else: fail[str(why).split(':')[0]]=fail.get(str(why).split(':')[0],0)+1
            if n%20==0 or n==len(tasks): print(f"  [{n}/{len(tasks)}] ok={ok} {fail}", flush=True)
    print(f"DONE embedded-candidates={ok} fail={fail} -> {a.outdir}", flush=True)

if __name__=="__main__": main()
