#!/usr/bin/env python3
"""Shorten verbose instruction.md to <=1500 tokens, preserving EVERY technical requirement,
path, schema, and rule. Grading is unaffected (oracle uses solve.sh/test.sh, not instruction.md),
so this only trims prose. gpt-5.6-sol path via strengthen_verifier.llm_call."""
import os,sys,concurrent.futures as cf,threading
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path[:0]=[HERE]
import strengthen_verifier as sv
sys.path.insert(0,"skills/static-semantic-qc/scripts")
try: from check_instructions import _est_tokens
except: _est_tokens=lambda s: len(s)//4
lock=threading.Lock()
SYS=("You compress a Terminal-Bench task instruction to be concise (UNDER 1500 tokens) WITHOUT losing "
 "any technical content. Keep EVERY: file/dir path, filename, CLI flag, schema field, numeric constant, "
 "tolerance, ordering rule, edge-case rule, output-format spec, and success criterion — verbatim where it "
 "matters. Remove only redundancy, filler, restated context, and over-explanation. Do NOT add new "
 "requirements or change any behavior. Output ONLY the rewritten instruction.md text (markdown), no fences.")
def one(model,td,task):
    p=f"{td}/instruction.md"
    instr=open(p,encoding='utf-8',errors='replace').read()
    if _est_tokens(instr)<=1500: return task,"already-short"
    for att in range(3):
        note="" if att==0 else f"\n\nYour previous version was STILL {_est_tokens(prev)} tokens (>1500). Cut further — remove more prose/redundancy, keep all technical specs."
        resp=sv.llm_call(None,model,SYS,f"Rewrite this to under 1500 tokens:\n\n{instr}"+note)
        if "_err" in resp: return task,"err:"+resp["_err"][:50]
        txt="".join(b.get("text","") for b in resp.get("content",[]) if b.get("type")=="text").strip()
        import re; m=re.search(r'```(?:markdown)?\s*\n(.*?)```',txt,re.S)
        if m: txt=m.group(1).strip()
        prev=txt
        if txt and _est_tokens(txt)<=1500:
            open(p,'w').write(txt+"\n"); return task,f"ok:{_est_tokens(txt)}tok"
    return task,f"still-long:{_est_tokens(prev)}"
def main():
    W="_local/refl_eval_pool/repl74_pull/tasks"; model="gpt-5.6-sol"
    tasks=[t for t in os.listdir(W) if os.path.isdir(f"{W}/{t}")]
    print(f"shortening {len(tasks)} instructions x {model}",flush=True)
    ok=0;n=0
    with cf.ThreadPoolExecutor(min(120,len(tasks))) as ex:
        for f in cf.as_completed([ex.submit(one,model,f"{W}/{t}",t) for t in tasks]):
            t,st=f.result();n+=1
            if st.startswith("ok") or st=="already-short": ok+=1
            if n%25==0 or n==len(tasks): print(f"  [{n}/{len(tasks)}] ok={ok}",flush=True)
    print(f"DONE shortened/ok={ok}",flush=True)
if __name__=="__main__": main()
