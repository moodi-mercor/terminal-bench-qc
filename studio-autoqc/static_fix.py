#!/usr/bin/env python3
"""Fix static-QC findings (subcategory/internet/placeholder/path/dangling-truth) with gpt-5.6-sol.
Per task, feed the finding + files + tree; model returns a JSON patch. Tests/env changes are
Modal-gated separately. Usage: python static_fix.py <base> <fix18.json> --outdir OUT --workers N"""
import os,sys,re,json,glob,time,random,shutil,ast,concurrent.futures as cf
sys.path.insert(0,"studio-autoqc")
from fix_leak_api import load_key
from strengthen_verifier import llm_call
K=load_key(); MODEL="gpt-5.6-sol"

GUIDE={
 "align":"Fix the SPECIFIC coherence/metadata issue described in the findings below with a MINIMAL edit. Typical fixes: correct or add a well-formed category/subcategory in task.toml; remove a stale/incorrect task-name or category comment in tests; fix a dangling reference (point to the real existing path or remove the mention); complete cut-off/garbled prose in instruction.md/CONTRACT.md/README so it is coherent. Do NOT change graded behavior, assertions, or the oracle. Keep the task solvable; oracle reward=1, no-op reward=0.",
 "subcategory":"Add a `subcategory = \"...\"` line under [metadata] in task.toml. Pick the SINGLE best-fitting value from the ALLOWED SUBCATEGORIES list provided (must be one of those exact strings). Change NOTHING else.",
 "internet":"The task has allow_internet=false (correct) but instruction.md tells the agent to download/fetch from the network. Reword instruction.md so it does NOT instruct any network fetch — the required inputs are already present in the agent's filesystem (point the agent at the local files/paths that exist in the environment). Do NOT set allow_internet=true. Do NOT change grading. Keep the task solvable offline.",
 "placeholder":"instruction.md contains a leftover authoring placeholder (TODO/FIXME/XXX). Rewrite instruction.md to remove the placeholder marker, filling in the intended content coherently from the surrounding context and the actual task files. Do not add new requirements or change what is graded.",
 "path":"instruction.md references a path that is NOT present in the agent's environment. Fix by EITHER rewording the instruction to reference the correct path that actually exists in the environment, OR (only if the file is genuinely needed and safe) ship it. Prefer rewording to the real existing path. Do not leak answers. Keep oracle passing.",
 "dangling":"A test (tests/test.sh or tests/test_outputs.py) references a truth/helper file or path that is dangling (not shipped / not re-copied). Fix so the verifier is self-contained: inline the referenced logic into test_outputs.py, or ship/re-copy the referenced file from /tests. Never read an agent-writable answer. The oracle must still score reward=1 and a no-op reward=0; do not weaken assertions.",
}
SYS_BASE="""You fix ONE static-QC finding in a terminal-bench (Reflection/Harbor) task with the MINIMAL change.
HARD RULES: never change what a test asserts, expected values, or pass/fail semantics; the oracle
(solution/solve.sh) must still reach reward=1 and a no-op must still fail; do not add network/pip installs;
do not introduce answer leakage. Return ONLY JSON:
{"files":{"<relpath>":"<full new contents>"}, "deletes":["<relpath>"], "note":"<=25 words"}"""

def rd(p,n=16000):
    try: return open(p,encoding="utf-8",errors="replace").read()[:n]
    except: return ""

def ctx(td, info, allowed_subcats):
    grp=info["group"]
    parts=[f"### FINDING GROUP: {grp}\nWHAT TO DO: {GUIDE[grp]}",
           "### findings: "+"; ".join(f"{t}: {d}" for t,d in info.get("findings",[]))]
    if grp=="subcategory":
        parts.append("### ALLOWED SUBCATEGORIES (pick exactly one):\n"+"\n".join(allowed_subcats))
        parts.append(f"### task.toml\n{rd(td+'/task.toml')}")
        parts.append(f"### instruction.md\n{rd(td+'/instruction.md',4000)}")
    else:
        parts.append(f"### instruction.md\n{rd(td+'/instruction.md',6000)}")
        parts.append(f"### task.toml\n{rd(td+'/task.toml',3000)}")
        if grp in ("path","dangling","internet"):
            parts.append(f"### environment/Dockerfile\n{rd(td+'/environment/Dockerfile',4000)}")
            parts.append(f"### tests/test.sh\n{rd(td+'/tests/test.sh',4000)}")
            parts.append(f"### tests/test_outputs.py\n{rd(td+'/tests/test_outputs.py',9000)}")
            parts.append(f"### solution/solve.sh\n{rd(td+'/solution/solve.sh',5000)}")
            tree=[os.path.relpath(f,td) for f in sorted(glob.glob(td+"/**/*",recursive=True)) if os.path.isfile(f)]
            parts.append("### FILE TREE\n"+"\n".join(tree[:80]))
    return "\n\n".join(parts)[:60000]

def fix_one(base, outdir, task, info, subcats):
    td=f"{base}/{task}"
    for att in range(5):
        try:
            r=llm_call(K,MODEL,SYS_BASE,ctx(td,info,subcats.get(task,[])))
            if "_err" in r: raise RuntimeError(r["_err"])
            txt="".join(b.get("text","") for b in r.get("content",[]) if b.get("type")=="text")
            m=re.search(r'\{.*\}',txt,re.S)
            if not m: raise ValueError("no json")
            patch=json.loads(m.group(0))
            od=f"{outdir}/{task}"
            if os.path.exists(od): shutil.rmtree(od)
            shutil.copytree(td,od)
            for rel,c in (patch.get("files") or {}).items():
                fp=os.path.join(od,rel); os.makedirs(os.path.dirname(fp),exist_ok=True); open(fp,"w").write(c)
                if rel.endswith(".py"): ast.parse(c)
            for rel in (patch.get("deletes") or []):
                fp=os.path.join(od,rel)
                if os.path.exists(fp): (shutil.rmtree if os.path.isdir(fp) else os.remove)(fp)
            return task,"ok",patch.get("note","")
        except Exception as e:
            s=str(e)
            if "429" in s or "rate" in s.lower() or "overload" in s.lower(): time.sleep(min(30,2**att)+random.random())
            elif att==4: return task,"fail",s[:80]
            else: time.sleep(2)
    return task,"fail","retries"

def main():
    base=sys.argv[1]; info=json.load(open(sys.argv[2]))
    outdir=sys.argv[sys.argv.index("--outdir")+1]
    workers=int(sys.argv[sys.argv.index("--workers")+1]) if "--workers" in sys.argv else 18
    subcats=json.load(open(sys.argv[sys.argv.index("--subcats")+1])) if "--subcats" in sys.argv else {}
    global MODEL
    if "--model" in sys.argv: MODEL=sys.argv[sys.argv.index("--model")+1]
    os.makedirs(outdir,exist_ok=True)
    print(f"[static-fix] {len(info)} tasks x {workers} workers on {MODEL}",flush=True)
    ok=0; fail={}
    with cf.ThreadPoolExecutor(workers) as ex:
        for t,st,note in ex.map(lambda kv: fix_one(base,outdir,kv[0],kv[1],subcats), info.items()):
            if st=="ok": ok+=1; print(f"  ok {t}: {note}",flush=True)
            else: fail[t]=note; print(f"  FAIL {t}: {note}",flush=True)
    print(f"[static-fix] candidates: {ok} | fail: {fail}",flush=True)
if __name__=="__main__": main()
