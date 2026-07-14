#!/usr/bin/env python3
"""General per-task QC fixer via the Claude API (worker pool), billed to ANTHROPIC_API_KEY
(NOT the Claude Code session). Handles the OTS-GDM failure buckets:

  leak       — verifier's expected answer readable by the agent (often build-time generated)
  gameable   — no-op passes the verifier (verifier vacuous / not measuring work)
  oracle     — reference solve.sh runs but the verifier fails (solve/verifier mismatch)
  solvecrash — solve.sh exits non-zero

Per task (one Opus call): feed instruction.md, task.toml, environment/Dockerfile + build
scripts, tests/test.sh, tests/test_outputs.py, solution/solve.sh, plus the observed failure
reason. Opus returns a JSON patch (full new file contents + deletes). We apply + AST-validate
changed .py. Fix is CONFIRMED separately on Modal (oracle gate) after the pool — never trust
the model's self-report.

Usage:
  python fix_task_api.py <tasks_dir> <failures.json> [--workers 6] [--out results.txt]
  failures.json: {task_name: {"type": "leak|gameable|oracle|solvecrash", "detail": "..."}}
"""
import argparse, ast, concurrent.futures as cf, json, os, threading, time
import urllib.request, urllib.error

API_URL="https://api.anthropic.com/v1/messages"; VER="2023-06-01"; MODEL="claude-opus-4-8"
ROOT="/Users/mahmoodmapara/Desktop/terminal-bench-qc"; lock=threading.Lock()
MAXB=20000
CTX=["instruction.md","task.toml","tests/test.sh","tests/test_outputs.py",
     "solution/solve.sh","environment/Dockerfile","environment/setup.sh",
     "environment/setup_commands.sh","environment/__swegen_setup_commands.sh"]

RULES={
 "leak":"The verifier's expected answer is reachable by the agent (a truth/expected file on an agent-visible path, often WRITTEN AT BUILD TIME by an environment/ script or the Dockerfile). Fix: stop exposing the answer to the agent — move its generation/placement to grade time (into tests/ or produced by tests/test.sh) or under tests/.truth/, OR have the verifier recompute it independently. NEVER weaken an assertion. Keep the task solvable and the oracle passing.",
 "gameable":"A no-op (agent does nothing) PASSES the verifier — it is vacuous. Fix: strengthen tests/test_outputs.py so it fails on the untouched seed state and only passes after the intended work (assert on the mutated/produced state, not on something already true at start). Keep the reference solution passing.",
 "oracle":"The reference solution/solve.sh RUNS but the verifier then FAILS — solve.sh does not produce what the verifier asserts, or the verifier asserts something solve.sh doesn't do. Fix the mismatch: correct solve.sh so it produces exactly what tests/test_outputs.py checks (preferred), or correct an over-strict/incorrect assertion. Do NOT gut the test. After the fix, no-op must still fail and oracle must pass.",
 "solvecrash":"solve.sh exits non-zero (crashes). Fix solve.sh (and any missing build/setup dep) so it runs to completion and the verifier passes. Keep no-op failing.",
}

def load_key():
    for v in ("ANTHROPIC_API_KEY","CLAUDE_API_KEY"):
        if os.environ.get(v): return os.environ[v]
    for line in open(f"{ROOT}/.env"):
        if line.startswith("ANTHROPIC_API_KEY="): return line.split("=",1)[1].strip().strip('"').strip("'")
    raise SystemExit("no ANTHROPIC_API_KEY")

def gather(tdir):
    parts=[]
    # standard context files
    for rel in CTX:
        p=os.path.join(tdir,rel)
        if os.path.isfile(p):
            b=open(p,"rb").read()
            if len(b)<=MAXB:
                parts.append(f"=== {rel} ===\n"+b.decode("utf-8","replace"))
    # other build scripts under environment/ (py/sh)
    envd=os.path.join(tdir,"environment")
    for dp,_,fns in os.walk(envd):
        for fn in fns:
            rel=os.path.relpath(os.path.join(dp,fn),tdir)
            if rel in [c for c in CTX]: continue
            if fn.endswith((".py",".sh")) and not fn.startswith("__pycache__"):
                p=os.path.join(dp,fn); b=open(p,"rb").read()
                if len(b)<=MAXB: parts.append(f"=== {rel} ===\n"+b.decode("utf-8","replace"))
    return "\n\n".join(parts)[:120000]

SYS=("You are a Terminal-Bench task QC engineer. You fix a task so that: (1) the no-op agent "
     "FAILS the verifier, (2) the reference solution/solve.sh makes the verifier PASS (reward=1), "
     "(3) no expected answer is readable by the agent. Return ONLY minimal, correct file edits.")

def fix_one(key,tdir,task,ftype,detail):
    if not os.path.isdir(os.path.join(tdir,"environment")): return task,"NO-TASK",""
    prompt=(f"This task FAILED QC. Failure type: {ftype}.\nObserved: {detail}\n\n"
            f"Fix rule: {RULES.get(ftype,RULES['oracle'])}\n\n"
            "Return a JSON object ONLY: {\"files\": {\"<relpath>\": \"<full new content>\"}, "
            "\"deletes\": [\"<relpath>\"], \"note\": \"<one line>\"}. Include the FULL new content "
            "of each changed file (relative to the task dir, e.g. tests/test_outputs.py). "
            "Do not include unchanged files.\n\n"+gather(tdir))
    body=json.dumps({"model":MODEL,"max_tokens":16000,"system":SYS,
                     "messages":[{"role":"user","content":prompt}]}).encode()
    for attempt in range(5):
        try:
            req=urllib.request.Request(API_URL,data=body,method="POST",
                headers={"x-api-key":key,"anthropic-version":VER,"content-type":"application/json"})
            with urllib.request.urlopen(req,timeout=180) as r: resp=json.loads(r.read())
            break
        except urllib.error.HTTPError as e:
            if e.code in (429,500,529) and attempt<4: time.sleep(8*(attempt+1)); continue
            return task,"API-ERR",f"{e.code}"
        except Exception as e:
            if attempt<4: time.sleep(5*(attempt+1)); continue
            return task,"EXC",str(e)[:100]
    txt="".join(b.get("text","") for b in resp.get("content",[]))
    i,j=txt.find("{"),txt.rfind("}")
    if i<0 or j<0: return task,"NO-JSON",""
    try: patch=json.loads(txt[i:j+1])
    except Exception: return task,"BAD-JSON",""
    files=patch.get("files",{}) or {}; deletes=patch.get("deletes",[]) or []
    # validate python
    for rel,content in files.items():
        if rel.endswith(".py"):
            try: ast.parse(content)
            except SyntaxError: return task,"PY-SYNTAX",rel
    # apply
    for rel,content in files.items():
        full=os.path.join(tdir,rel); os.makedirs(os.path.dirname(full),exist_ok=True)
        open(full,"w").write(content)
    for rel in deletes:
        full=os.path.join(tdir,rel)
        if os.path.isfile(full): os.remove(full)
    return task,"FIXED",patch.get("note","")[:100]

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("tasks"); ap.add_argument("failures")
    ap.add_argument("--workers",type=int,default=6); ap.add_argument("--out",required=True)
    a=ap.parse_args(); key=load_key()
    fails=json.load(open(a.failures))
    done=set()
    if os.path.exists(a.out):
        for l in open(a.out):
            p=l.split("\t")
            if len(p)>=2 and p[1]=="FIXED": done.add(p[0])
    todo=[(t,d) for t,d in fails.items() if t not in done]
    print(f"fixing {len(todo)} tasks ({len(done)} done), {a.workers} workers",flush=True)
    cnt={};n=0; sf=open(a.out,"a")
    with cf.ThreadPoolExecutor(a.workers) as ex:
        futs={ex.submit(fix_one,key,os.path.join(a.tasks,t),t,d.get("type","oracle"),d.get("detail","")):t for t,d in todo}
        for fut in cf.as_completed(futs):
            t,kind,note=fut.result(); cnt[kind]=cnt.get(kind,0)+1; n+=1
            with lock: sf.write(f"{t}\t{kind}\t{note}\n"); sf.flush()
            if kind!="FIXED" or n%20==0: print(f"  [{n}/{len(todo)}] {t}: {kind} {note}",flush=True)
    print("DONE",cnt,flush=True)

if __name__=="__main__": main()
