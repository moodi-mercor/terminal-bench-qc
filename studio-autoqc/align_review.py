#!/usr/bin/env python3
"""Comprehensive per-task ALIGNMENT/coherence review (semantic). Reads every core file of a task and
checks it is internally consistent and solvable as shipped — especially after a QC fix. gpt-5.6-sol.
Usage: python align_review.py <base> <task_list> --out results.json --workers N"""
import os,sys,re,json,glob,time,random,concurrent.futures as cf
sys.path.insert(0,"studio-autoqc")
from fix_leak_api import load_key
from strengthen_verifier import llm_call
K=load_key(); MODEL="gpt-5.6-sol"

SYS="""You are a meticulous QC reviewer doing a FINAL alignment pass on ONE terminal-bench (Reflection/Harbor)
task that was just edited by an automated fix. The files below are COMPLETE (not truncated). Do NOT report truncation/SyntaxError unless the actual final characters are genuinely malformed. Read ALL the provided files and judge whether the task is
INTERNALLY CONSISTENT and SOLVABLE AS SHIPPED. Check specifically:
 1. instruction.md ↔ environment: every file/path/dir/command the instruction tells the agent to use actually
    exists in the environment (or is created by the agent per the instruction). No references to missing files.
 2. instruction.md ↔ tests: what the instruction asks for is what the verifier (tests/test_outputs.py) checks;
    the required output paths/formats/schemas match. No graded behavior that the instruction never described.
 3. instruction ↔ solution: the reference solution (solution/solve.sh) produces what the instruction asks and
    what the tests check.
 4. metadata sanity: category/subcategory fit the task; allow_internet matches whether the task needs network
    (should be false and the task must be offline-solvable); model_tested present.
 5. no leftover authoring placeholders (TODO/FIXME/XXX), no contradictions, no dangling references, no prose
    that was garbled by an edit.
 6. the task still poses a real problem (not trivially passable, not impossible).
Be concrete. Output ONLY JSON:
{"verdict":"ALIGNED"|"ISSUES","issues":[{"kind":"<short>","detail":"<file:line + what's wrong>"}],"summary":"<=25 words"}
If fully consistent and solvable, return verdict ALIGNED with an empty issues list."""

def rd(p,n=120000):
    try: return open(p,encoding="utf-8",errors="replace").read()[:n]
    except: return ""
def ctx(td):
    parts=[f"### instruction.md\n{rd(td+'/instruction.md',20000)}",
           f"### task.toml\n{rd(td+'/task.toml',3000)}",
           f"### environment/Dockerfile\n{rd(td+'/environment/Dockerfile',12000)}",
           f"### tests/test.sh\n{rd(td+'/tests/test.sh',12000)}",
           f"### tests/test_outputs.py\n{rd(td+'/tests/test_outputs.py',80000)}",
           f"### solution/solve.sh\n{rd(td+'/solution/solve.sh',80000)}"]
    tree=[os.path.relpath(f,td) for f in sorted(glob.glob(td+"/**/*",recursive=True)) if os.path.isfile(f)]
    parts.append("### FULL FILE TREE\n"+"\n".join(tree[:120]))
    # heads of other env files the instruction may reference
    for f in sorted(glob.glob(td+"/environment/**/*",recursive=True)):
        if os.path.isfile(f) and f.endswith(('.md','.txt','.json','.yaml','.yml','.conf')) and os.path.getsize(f)<6000:
            parts.append(f"### {os.path.relpath(f,td)}\n{rd(f,3000)}")
    return "\n\n".join(parts)[:240000]

def review(td):
    for att in range(5):
        try:
            r=llm_call(K,MODEL,SYS,ctx(td))
            if "_err" in r: raise RuntimeError(r["_err"])
            txt="".join(b.get("text","") for b in r.get("content",[]) if b.get("type")=="text")
            m=re.search(r'\{.*\}',txt,re.S)
            return json.loads(m.group(0)) if m else {"verdict":"?","summary":txt[:120]}
        except Exception as e:
            s=str(e)
            if "429" in s or "rate" in s.lower() or "overload" in s.lower(): time.sleep(min(30,2**att)+random.random())
            elif att==4: return {"verdict":"ERR","summary":s[:100]}
            else: time.sleep(2)
    return {"verdict":"ERR"}

def main():
    base=sys.argv[1]; tasks=[l.strip() for l in open(sys.argv[2]) if l.strip()]
    outp=sys.argv[sys.argv.index("--out")+1] if "--out" in sys.argv else "align.json"
    workers=int(sys.argv[sys.argv.index("--workers")+1]) if "--workers" in sys.argv else 18
    print(f"[align] {len(tasks)} tasks x {workers} on {MODEL}",flush=True)
    res={}
    with cf.ThreadPoolExecutor(workers) as ex:
        for t,v in ex.map(lambda t:(t,review(f"{base}/{t}")), tasks):
            res[t]=v; print(f"  {t}: {v.get('verdict')} — {v.get('summary','')[:70]}",flush=True)
            json.dump(res,open(outp,"w"),indent=1)
    from collections import Counter
    print("verdicts:",dict(Counter(v.get("verdict") for v in res.values())),flush=True)
if __name__=="__main__": main()
