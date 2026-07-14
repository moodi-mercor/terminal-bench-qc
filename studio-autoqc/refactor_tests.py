#!/usr/bin/env python3
"""PR #9-style native-Python refactor of Reflection test_outputs.py (client feedback).

Removes the anti-patterns the spec forbids (§159 encoded commands, §176 unnecessary bash,
§239 obfuscation): `_run(base64.b64decode(...))` and `_run('python3 -c "..."')` where the
work can be done natively in-process. KEEPS subprocess only for invoking the agent's own
deliverable CLI or genuine process-level behavior. Behavior-preserving: same test function
names, order, docstrings, and pass/fail intent; `.truth/` untouched.

Per task: inline test_outputs.py + decoded base64 blobs -> Opus refactors -> AST validate
(valid Python + identical set/order of top-level `def test_*`) -> write, keep .orig backup.
Resumable (skips tasks with a .refactored marker). Billed to the Claude API key.

Usage: python refactor_tests.py <tasks-dir> <task-list> [--workers 5] [--model claude-opus-4-8]
"""
import argparse, ast, base64, concurrent.futures as cf, json, os, re, sys, time
import urllib.request, urllib.error

API_URL="https://api.anthropic.com/v1/messages"; VER="2023-06-01"; MODEL="claude-opus-4-8"
HERE=os.path.dirname(os.path.abspath(__file__)); lock=__import__("threading").Lock()

def load_key():
    for v in ("ANTHROPIC_API_KEY","CLAUDE_API_KEY","ANT_KEY"):
        if os.environ.get(v): return os.environ[v].strip()
    here=HERE
    for _ in range(6):
        p=os.path.join(here,".env")
        if os.path.isfile(p):
            for ln in open(p):
                for v in ("ANTHROPIC_API_KEY","CLAUDE_API_KEY","ANT_KEY"):
                    if ln.strip().startswith(v+"="): return ln.split("=",1)[1].strip().strip('"').strip("'")
        here=os.path.dirname(here)
    sys.exit("no Claude API key")

SYS=("You refactor Terminal-Bench (Reflection Harbor) verifier files to native Python per the "
 "client's test-structuring feedback. Rules: (1) Replace `_run(base64.b64decode(...).decode())` "
 "and `_run('python3 -c \"...\"')` calls with the SAME logic written as native in-process Python "
 "in the test body. (2) KEEP subprocess ONLY when it invokes the agent's own deliverable CLI/binary "
 "(e.g. a tool the task builds), a daemon, PTY, or other genuine process-level behavior. (3) Behavior "
 "MUST be preserved: identical top-level test function names, identical order, identical docstrings, "
 "and identical pass/fail outcome for every input. Translate a python -c that ends in print('X')+ "
 "substring-assert into a direct native assert of the same condition. (4) Never read, import from, or "
 "modify anything under tests/.truth except exactly as the original did. (5) Do not weaken assertions, "
 "change thresholds, add try/except that swallows failures, or introduce new imports of agent-writable "
 "modules that create a leak. Output ONLY the complete refactored file content, no fences, no prose.")

def decode_blobs(src):
    out=[]
    for m in re.findall(r"b64decode\(\s*'([A-Za-z0-9+/=]+)'\s*\)",src):
        try: out.append((m[:24]+"...", base64.b64decode(m).decode(errors="replace")))
        except Exception: pass
    return out

def call(key,model,system,user,retries=5):
    body=json.dumps({"model":model,"max_tokens":32000,"system":system,
        "thinking":{"type":"adaptive"},"output_config":{"effort":"high"},
        "messages":[{"role":"user","content":user}]}).encode()
    req=urllib.request.Request(API_URL,data=body,method="POST",headers={
        "x-api-key":key,"anthropic-version":VER,"content-type":"application/json"})
    for a in range(retries):
        try:
            with urllib.request.urlopen(req,timeout=600) as r: return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429,500,503,529) and a<retries-1: time.sleep(3*(a+1)**2); continue
            return {"_err":f"{e.code}:{e.read()[:200].decode(errors='replace')}"}
        except Exception as e:
            if a<retries-1: time.sleep(3*(a+1)); continue
            return {"_err":str(e)[:200]}

def test_names(src):
    try: tree=ast.parse(src)
    except Exception: return None
    return [n.name for n in tree.body if isinstance(n,ast.FunctionDef) and n.name.startswith("test")]

def refactor_one(key,model,tdir,task):
    fp=os.path.join(tdir,"tests","test_outputs.py")
    if not os.path.isfile(fp): return task,"NO-FILE"
    marker=fp+".refactored"
    if os.path.exists(marker): return task,"SKIP-DONE"
    src=open(fp,errors="replace").read()
    if not (re.search(r"b64decode",src) or (re.search(r"python3?\s+-c",src) and re.search(r"_run\(|subprocess\.",src))):
        return task,"SKIP-CLEAN"
    orig_names=test_names(src)
    if orig_names is None: return task,"ORIG-UNPARSEABLE"
    blobs=decode_blobs(src)
    appendix="" if not blobs else ("\n\n# DECODED base64 commands (for your reference — inline these natively):\n"+
        "\n".join(f"# [{k}] -> {v}" for k,v in blobs))
    user=f"Refactor this verifier to native Python per the rules.\n\n```python\n{src}\n```{appendix}"
    resp=call(key,model,SYS,user)
    if "_err" in resp: return task,f"API-ERR:{resp['_err'][:60]}"
    if resp.get("stop_reason")=="refusal": return task,"REFUSED"
    text="".join(b.get("text","") for b in resp.get("content",[]) if b.get("type")=="text")
    text=re.sub(r"^```(python)?\n","",text.strip()); text=re.sub(r"\n```$","",text)
    new_names=test_names(text)
    if new_names is None: return task,"NEW-UNPARSEABLE"
    if new_names!=orig_names: return task,f"NAMES-CHANGED({len(orig_names)}->{len(new_names)})"
    if re.search(r"b64decode",text): return task,"STILL-B64"
    with lock:
        if not os.path.exists(fp+".orig"): open(fp+".orig","w").write(src)
        open(fp,"w").write(text if text.endswith("\n") else text+"\n")
        open(marker,"w").write("1")
    return task,"OK"

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("tasks_dir"); ap.add_argument("task_list")
    ap.add_argument("--workers",type=int,default=5); ap.add_argument("--model",default=MODEL)
    ap.add_argument("--out",default="_local/qc_out_eval_pool/refactor_results.txt")
    a=ap.parse_args(); key=load_key()
    tasks=[t for t in open(a.task_list).read().split() if t]
    print(f"[refactor] {len(tasks)} tasks x {a.model} ({a.workers} workers). Billed to Claude API key.",flush=True)
    counts={}; done=0
    with cf.ThreadPoolExecutor(a.workers) as ex:
        futs={ex.submit(refactor_one,key,a.model,os.path.join(a.tasks_dir,t),t):t for t in tasks}
        for f in cf.as_completed(futs):
            t,kind=f.result(); counts[kind]=counts.get(kind,0)+1; done+=1
            with lock: open(a.out,"a").write(f"{t}\t{kind}\n")
            if kind not in ("OK","SKIP-DONE","SKIP-CLEAN"): print(f"  [{kind}] {t}",flush=True)
            if done%25==0 or done==len(tasks): print(f"  [{done}/{len(tasks)}] {counts}",flush=True)
    print("DONE",counts)

if __name__=="__main__": main()
