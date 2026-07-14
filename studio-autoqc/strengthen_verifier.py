#!/usr/bin/env python3
"""Strengthen weak verifiers found by mutation testing. For each task where a mutant
survived, ask the model to tighten tests/test_outputs.py so the surviving wrong solution
now FAILS, while the reference oracle still PASSES. Candidate written to outdir; a separate
Modal step verifies oracle=1 AND mutant killed AND no-op=0 before applying."""
import argparse,concurrent.futures as cf,json,os,re,sys,threading,glob
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path[:0]=[HERE]
import fix_leak_api as fl
lock=threading.Lock()

SYS=(
 "You strengthen a Terminal-Bench pytest verifier. Mutation testing found a WRONG solution "
 "(a 'mutant') that the current verifier wrongly ACCEPTS. You are given: instruction.md, the "
 "reference solution/solve.sh (correct oracle), the current tests/test_outputs.py, and the "
 "surviving mutant script(s) with the requirement each violates. Rewrite test_outputs.py so it:\n"
 "1. REJECTS each surviving mutant (add/tighten assertions that check the specific behavior the "
 "mutant violates — the exact output/semantics, not how it's built).\n"
 "2. Still PASSES the reference oracle unchanged (do not assert anything the correct solution "
 "doesn't satisfy). Preserve numeric tolerances that the oracle needs.\n"
 "3. Keeps the SAME test function names (add new test_ functions if needed) and stays self-"
 "contained: no /tests/.truth references, no subprocess to external verifiers, no pip/apt/npm "
 "install, no network. Valid Python importable by pytest.\n"
 "Base the new assertions on the CONTRACT in instruction.md and what the oracle actually produces. "
 "Output ONLY the complete new test_outputs.py in one ```python fenced block.")

def read(p):
    try: return open(p,encoding='utf-8',errors='replace').read()
    except: return ""

_OAI=None
def _oai():
    global _OAI
    if _OAI is None:
        import openai
        k=os.environ.get("OPENAI_API_KEY")
        if not k:
            for ln in open("/Users/mahmoodmapara/Desktop/code-qa-evals/.env"):
                if ln.startswith("OPENAI_API_KEY="): k=ln.split("=",1)[1].strip().strip('"')
        _OAI=openai.OpenAI(api_key=k)
    return _OAI

def llm_call(key, model, system, user):
    """Dispatch by model prefix: gpt-* -> OpenAI (reasoning_effort=high); else Anthropic via fl.call."""
    if model.startswith("gpt"):
        try:
            r=_oai().chat.completions.create(
                model=model, reasoning_effort="high", max_completion_tokens=16000,
                messages=[{"role":"system","content":system},{"role":"user","content":user}])
            return {"content":[{"type":"text","text":r.choices[0].message.content or ""}]}
        except Exception as e:
            return {"_err":str(e)[:180]}
    return fl.call(key, model, system, user)

def strengthen_one(key, model, D, task, weak, mutdir, outdir):
    td=os.path.join(D,task)
    to=read(f"{td}/tests/test_outputs.py"); solve=read(f"{td}/solution/solve.sh"); instr=read(f"{td}/instruction.md")
    if not to or not solve: return task,None,"missing-files"
    # gather surviving mutant scripts by matching requirement text
    meta=[]
    mp=f"{mutdir}/{task}/mutants.json"
    if os.path.exists(mp):
        try: meta=json.load(open(mp))
        except: meta=[]
    survreqs=set(weak.get("survived_reqs") or [])
    muts=[]
    for m in meta:
        req=m.get("requirement_violated","")
        # match on prefix (survived_reqs are truncated to 80)
        if any(req.startswith(sr[:40]) or sr.startswith(req[:40]) for sr in survreqs) or not survreqs:
            f=f"{mutdir}/{task}/mut_{m['i']}.sh"
            if os.path.exists(f): muts.append((req, read(f)))
    if not muts:  # fall back: include all mutants
        for f in glob.glob(f"{mutdir}/{task}/mut_*.sh"): muts.append(("(unlabeled)", read(f)))
    muts=muts[:3]
    parts=[f"===== instruction.md =====\n{instr[:6000]}",
           f"===== solution/solve.sh (ORACLE, must still pass) =====\n{solve[:6000]}",
           f"===== tests/test_outputs.py (CURRENT, too weak) =====\n{to[:12000]}"]
    for req,body in muts:
        parts.append(f"===== SURVIVING MUTANT (violates: {req}) — must now FAIL =====\n{body[:5000]}")
    vn=weak.get("verify_note")
    if vn:
        parts.append("===== CRITICAL — LEARN FROM THE PRIOR FAILED ATTEMPT =====\n"+vn)
    base="\n\n".join(parts)
    orig=set(re.findall(r'^def\s+(test_\w+)',to,re.M))
    # validate a candidate; return (clean_code|None, reason, feedback_for_next_attempt)
    def validate(txt):
        m=re.search(r'```(?:python)?\s*\n(.*?)```',txt,re.S)
        code=(m.group(1) if m else txt).strip()+"\n"
        try: compile(code,to,'exec')
        except Exception as e:
            return None,"compile",(f"Your previous attempt had a Python SYNTAX ERROR: {str(e)[:160]}. "
                                    "Fix it and re-emit the COMPLETE, compilable file.")
        nc="\n".join(l for l in code.split('\n') if not l.lstrip().startswith('#'))
        if re.search(r'\.truth/[^\s"\']*\.py',nc):
            return None,"added-.truth",("Your previous attempt referenced a /tests/.truth/*.py verifier script. "
                                        "That path does not exist at grade time. Inline the needed logic directly; "
                                        "remove ALL references to /tests/.truth.")
        if re.search(r'\b(pip|apt-get|npm)\s+install\b',nc):
            return None,"added-install",("Your previous attempt added a pip/apt/npm install command. Do NOT install "
                                         "anything — every dependency (numpy, pyyaml, etc.) is already present in the image. "
                                         "Remove all install lines and just import what you need.")
        new=set(re.findall(r'^def\s+(test_\w+)',code,re.M))
        missing=orig-new
        if orig and missing:
            return None,"dropped-tests",("Your previous attempt DELETED these required test functions: "
                                         f"{sorted(missing)}. You MUST keep EVERY original test_ function with its exact "
                                         "name and its existing assertions intact — you may only ADD new assertions inside "
                                         "them or ADD entirely new test_ functions. Re-emit the COMPLETE file containing "
                                         f"ALL {len(orig)} original test functions plus your additions.")
        return code,"ok",""
    last="unknown"; reason="api-error"
    for att in range(4):
        user=base if att==0 else (base+"\n\n===== FIX YOUR PREVIOUS ATTEMPT =====\n"+last+
              "\nKeep everything that was already correct; change only what the note above requires. "
              "Output ONLY the complete test_outputs.py in one ```python block.")
        resp=llm_call(key, model, SYS, user)
        if "_err" in resp: last=f"the API call errored ({resp['_err'][:80]}); produce a valid file."; continue
        txt="".join(b.get("text","") for b in resp.get("content",[]) if b.get("type")=="text")
        code,reason,fb=validate(txt)
        if code:
            os.makedirs(outdir,exist_ok=True); open(f"{outdir}/{task}.py","w").write(code)
            return task,"ok",f"attempt{att+1}"
        last=fb
    return task,None,reason

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("delivery"); ap.add_argument("--weak", default="_local/qc_out_delivery/weak_verifiers.json")
    ap.add_argument("--mutdir", default="_local/qc_out_delivery/mut")
    ap.add_argument("--outdir", default="_local/qc_out_delivery/strengthen_cand")
    ap.add_argument("--workers", type=int, default=100); ap.add_argument("--model", default="claude-opus-4-8")
    a=ap.parse_args()
    key=fl.load_key(); weak=json.load(open(a.weak))
    tasks=list(weak)
    print(f"[strengthen] {len(tasks)} weak verifiers x {a.model} ({a.workers}w)",flush=True)
    ok=0;fail={};n=0
    with cf.ThreadPoolExecutor(a.workers) as ex:
        futs={ex.submit(strengthen_one,key,a.model,a.delivery,t,weak[t],a.mutdir,a.outdir):t for t in tasks}
        for f in cf.as_completed(futs):
            t,st,err=f.result();n+=1
            if st=="ok": ok+=1
            else: fail[err.split(':')[0]]=fail.get(err.split(':')[0],0)+1
            if n%25==0 or n==len(tasks): print(f"  [{n}/{len(tasks)}] ok={ok} fail={fail}",flush=True)
    print(f"DONE candidates={ok} fail={fail}",flush=True)
if __name__=="__main__": main()
