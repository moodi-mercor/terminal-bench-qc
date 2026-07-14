#!/usr/bin/env python3
"""Strengthen weak verifiers found by mutation testing, then prove the fix on Modal.

Input: mutation_results.jsonl (tasks with survived>0) + the mutant scripts under <mutdir>.
For each weak task, the surviving mutants are wrong solutions the verifier WRONGLY accepted.

--generate : LLM adds assertion(s) to tests/test_outputs.py so each surviving mutant now
             FAILS, while a correct solution still passes. Writes candidate to <cand>/<task>.py
--verify   : Modal builds each task with the candidate verifier and checks BOTH:
               oracle (solution/solve.sh) still scores reward=1   (didn't over-tighten), AND
               every previously-surviving mutant now scores reward=0   (now caught).
             Only candidates passing BOTH are applied to the delivery.

Usage:
  python strengthen_verifiers.py <repo> --results R.jsonl --mutdir M --generate --cand C [--workers 150]
  python strengthen_verifiers.py <repo> --results R.jsonl --mutdir M --verify --cand C --out V.txt [--workers 200]
"""
import argparse, concurrent.futures as cf, json, os, re, sys, threading, time
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path[:0]=[HERE]
lock=threading.Lock()

GEN_SYS=(
 "You STRENGTHEN a Terminal-Bench pytest verifier. You are given instruction.md, the current "
 "tests/test_outputs.py, and one or more MUTANT solutions that WRONGLY PASS the verifier — each "
 "mutant is a plausible-but-wrong solution that violates a stated requirement the verifier failed "
 "to check. Add the MINIMUM assertions so every mutant now FAILS, while a CORRECT solution still "
 "passes.\n"
 "HARD REQUIREMENTS:\n"
 "1. Keep every existing test_ function name; you may add assertions inside them or add new "
 "test_ functions. Do NOT delete or weaken existing checks.\n"
 "2. Each added assertion must target the specific violated requirement (given per mutant), and "
 "must check the PRODUCED ARTIFACT/OUTPUT (state, files, records), independently derived — not the "
 "method the solution used. Recompute expected values from the real inputs; never trust agent output.\n"
 "3. A correct reference solution MUST still pass. Do not assert on incidental/implementation "
 "details a valid alternative solution could legitimately differ on.\n"
 "4. Read fixtures/inputs from their existing paths. No network, no pip/apt install, no new "
 "subprocess to a /tests/.truth script, no reference to files that don't exist.\n"
 "5. You MUST reproduce every existing test_ function verbatim and ADD to them; emitting fewer "
 "test_ functions than the original is a failure. Output must be valid, compilable Python.\n"
 "Output ONLY the complete new tests/test_outputs.py inside one ```python fenced block.")

RETRY_NOTE=(
 "\n\n=== YOUR PREVIOUS ATTEMPT FAILED ===\n{why}\n"
 "Fix it this time: if a mutant still passed, your assertion did not actually distinguish that "
 "mutant's WRONG output from a correct solution's output — inspect what that mutant produces vs "
 "the contract and assert on the concrete difference (recompute the correct value from inputs). "
 "If the oracle broke, you over-tightened — you asserted something a correct solution does not "
 "satisfy; relax to only what the contract requires. Keep ALL original test_ functions verbatim "
 "and emit valid Python. Output ONLY the full test_outputs.py in one ```python block.")

def _survivor_indices(meta, survived_reqs):
    sr=set(survived_reqs or [])
    return [m["i"] for m in meta if m.get("requirement_violated","") in sr] or [m["i"] for m in meta]

def load_weak(results):
    out=[]
    for l in open(results):
        r=json.loads(l)
        if r.get("survived",0)>0: out.append(r)
    return out

# ---------------------------------------------------------------- generate --
def gen_one(key, model, tdir, task, mutdir):
    import fix_leak_api as fl
    instr=fl.readfile(os.path.join(tdir,"instruction.md")) or ""
    to=fl.readfile(os.path.join(tdir,"tests","test_outputs.py")) or ""
    if not to: return task,None,"no-verifier"
    od=os.path.join(mutdir,task)
    meta=json.load(open(os.path.join(od,"mutants.json"))) if os.path.exists(os.path.join(od,"mutants.json")) else []
    idxs=gen_one._idx.get(task) or [m["i"] for m in meta]
    parts=[f"===== instruction.md =====\n{instr[:6000]}",
           f"===== tests/test_outputs.py (CURRENT — too weak) =====\n{to[:16000]}"]
    for m in meta:
        if m["i"] not in idxs: continue
        sh=fl.readfile(os.path.join(od,f"mut_{m['i']}.sh")) or ""
        if sh.strip():
            parts.append(f"===== MUTANT that wrongly passes — violates: {m.get('requirement_violated','')} =====\n{sh[:5000]}")
    base="\n\n".join(parts)
    fb=gen_one._feedback.get(task,"")
    orig=set(re.findall(r'^def\s+(test_\w+)',to,re.M))
    last="unknown"
    for att in range(2):
        user=base if (att==0 and not fb) else base+RETRY_NOTE.format(why=(fb if att==0 else last))
        resp=fl.call(key,model,GEN_SYS,user)
        if "_err" in resp: last=resp["_err"][:60]; continue
        txt="".join(b.get("text","") for b in resp.get("content",[]) if b.get("type")=="text")
        m=re.search(r'```(?:python)?\s*\n(.*?)```',txt,re.S)
        code=(m.group(1) if m else txt).strip()+"\n"
        try: compile(code,"t","exec")
        except Exception as e: last=f"compile:{str(e)[:40]}"; continue
        nc="\n".join(l for l in code.split('\n') if not l.lstrip().startswith('#'))
        if re.search(r'\b(pip|apt-get|npm)\s+install\b',nc) or re.search(r'\.truth/[^\s"\']*\.py',nc):
            last="added-install-or-delegation"; continue
        new=set(re.findall(r'^def\s+(test_\w+)',code,re.M))
        if orig and not orig.issubset(new): last=f"dropped-tests(had {len(orig)} got {len(new)})"; continue
        return task,code,""
    return task,None,last
gen_one._idx={}
gen_one._feedback={}

def do_generate(repo, weak, mutdir, cand, model, workers, prior="", ledger=""):
    import fix_leak_api as fl
    key=fl.load_key(); os.makedirs(cand,exist_ok=True)
    # precompute survivor indices per task
    for r in weak:
        od=os.path.join(mutdir,r["task"]); mj=os.path.join(od,"mutants.json")
        meta=json.load(open(mj)) if os.path.exists(mj) else []
        gen_one._idx[r["task"]]=_survivor_indices(meta,r.get("survived_reqs"))
    # second-pass: load prior verify verdicts -> skip FIXED, feed failures back
    skip=set()
    if prior and os.path.exists(prior):
        for l in open(prior):
            p=l.rstrip("\n").split("\t")
            if len(p)<2: continue
            t,v=p[0],p[1]; det=p[2] if len(p)>2 else ""
            if v=="FIXED": skip.add(t)
            elif v=="MUTANT-SURVIVES": gen_one._feedback[t]=f"A mutant STILL PASSED your strengthened verifier ({det})."
            elif v=="ORACLE-BROKE": gen_one._feedback[t]=f"You BROKE the oracle ({det}) — a correct solution no longer passes."
    # focused per-task guidance from the failure ledger (overrides the generic one-liner)
    if ledger and os.path.exists(ledger):
        led=json.load(open(ledger))
        for t,rec in led.items():
            gen_one._feedback[t]=rec.get("next_hint","")
    tasks=[r["task"] for r in weak if r["task"] not in skip and os.path.isdir(os.path.join(repo,"tasks",r["task"]))]
    print(f"[gen] {len(tasks)} weak verifiers x {model} ({workers}w){' +ledger' if ledger else ''}",flush=True)
    ok=0;fail={};n=0
    genfail=cand.rstrip("/")+"_genfail.tsv"   # per-task generate-failure log (task \t reason) for next ledger
    open(genfail,"w").close()
    with cf.ThreadPoolExecutor(workers) as ex:
        futs={ex.submit(gen_one,key,model,os.path.join(repo,"tasks",t),t,mutdir):t for t in tasks}
        for f in cf.as_completed(futs):
            t,code,err=f.result();n+=1
            if code: open(os.path.join(cand,t+".py"),"w").write(code); ok+=1
            else:
                fail[err.split(':')[0]]=fail.get(err.split(':')[0],0)+1
                with lock: open(genfail,"a").write(f"{t}\t{err}\n")
            if n%25==0 or n==len(tasks): print(f"  [{n}/{len(tasks)}] cand={ok} fail={fail}",flush=True)
    print(f"[gen] DONE candidates={ok} fail={fail}",flush=True)

# ------------------------------------------------------------------ verify --
def verify_one(app, repo, task, mutdir, cand, survivor_idx, timeout):
    import modal
    tdir=os.path.join(repo,"tasks",task); od=os.path.join(mutdir,task)
    candpy=os.path.join(cand,task+".py")
    if not os.path.exists(candpy): return task,"NO-CAND",""
    img=(modal.Image.from_dockerfile(os.path.join(tdir,"environment","Dockerfile"),
                                     context_dir=os.path.join(tdir,"environment"))
         .add_local_dir(os.path.join(tdir,"tests"),remote_path="/tests")
         .add_local_dir(os.path.join(tdir,"solution"),remote_path="/solution")
         .add_local_file(candpy,remote_path="/cand/test_outputs.py")
         .add_local_dir(od,remote_path="/mutants"))
    sb=None
    try:
        sb=modal.Sandbox.create(app=app,image=img,timeout=timeout,cpu=2,memory=4096)
        # install candidate verifier over the shipped one
        setup="cp /cand/test_outputs.py /tests/test_outputs.py; mkdir -p /logs/tests /logs/verifier /solution; "
        def reward():
            return ("R=$(cat /logs/verifier/reward.txt /logs/tests/reward.txt 2>/dev/null | head -n1 | tr -dc '0-9.'); "
                    "rm -f /logs/verifier/reward.txt /logs/tests/reward.txt; ")
        # 1) oracle must still pass
        s1=setup+"bash /solution/solve.sh >/tmp/o.log 2>&1; bash /tests/test.sh >/tmp/t.log 2>&1; "+reward()+"echo \"ORACLE=[$R]\""
        p=sb.exec("bash","-lc",s1); o=p.stdout.read(); p.wait()
        mo=re.search(r"ORACLE=\[([\d.]*)\]",o); orr=mo.group(1) if mo else ""
        if not (orr and abs(float(orr)-1.0)<1e-9):
            return task,"ORACLE-BROKE",f"oracle_reward={orr or 'MISSING'}"
        # 2) each previously-surviving mutant must now die (reward 0)
        still=[]
        for i in survivor_idx:
            if not os.path.exists(os.path.join(od,f"mut_{i}.sh")): continue
            s2=(f"cp /mutants/mut_{i}.sh /solution/solve.sh; chmod +x /solution/solve.sh; "
                "bash /solution/solve.sh >/tmp/m.log 2>&1; bash /tests/test.sh >/tmp/t.log 2>&1; "+reward()+"echo \"MUT=[$R]\"")
            p=sb.exec("bash","-lc",s2); o=p.stdout.read(); p.wait()
            mm=re.search(r"MUT=\[([\d.]*)\]",o); r=mm.group(1) if mm else ""
            if r and abs(float(r)-1.0)<1e-9: still.append(i)   # still passes -> not fixed
        if still: return task,"MUTANT-SURVIVES",f"mutants_still_passing={still}"
        return task,"FIXED",""
    except Exception as e:
        return task,"EXC",str(e)[-100:]
    finally:
        if sb is not None:
            try: sb.terminate()
            except Exception: pass

def do_verify(repo, weak, mutdir, cand, out, workers, timeout, delivery):
    import modal
    app=modal.App.lookup("refl-reward-gate-v2",create_if_missing=True)  # reuse existing app (1000-app cap)
    idx={}
    for r in weak:
        mj=os.path.join(mutdir,r["task"],"mutants.json")
        meta=json.load(open(mj)) if os.path.exists(mj) else []
        idx[r["task"]]=_survivor_indices(meta,r.get("survived_reqs"))
    tasks=[r["task"] for r in weak if os.path.exists(os.path.join(cand,r["task"]+".py"))]
    done=set(l.split('\t')[0] for l in open(out)) if os.path.exists(out) else set()
    tasks=[t for t in tasks if t not in done]
    print(f"[verify] {len(tasks)} candidates on Modal ({workers}w)",flush=True)
    counts={};n=0;t0=time.time()
    with cf.ThreadPoolExecutor(workers) as ex:
        futs={ex.submit(verify_one,app,repo,t,mutdir,cand,idx[t],timeout):t for t in tasks}
        for f in cf.as_completed(futs):
            t,verdict,detail=f.result();n+=1
            counts[verdict]=counts.get(verdict,0)+1
            with lock: open(out,"a").write(f"{t}\t{verdict}\t{detail}\n")
            if verdict=="FIXED" and delivery:   # apply
                dst=os.path.join(delivery,t,"tests","test_outputs.py")
                if os.path.isdir(os.path.dirname(dst)):
                    import shutil; shutil.copy(os.path.join(cand,t+".py"),dst)
            if verdict not in ("FIXED",): print(f"  [{verdict}] {t}: {detail[:80]}",flush=True)
            if n%25==0 or n==len(tasks): print(f"  [{n}/{len(tasks)}] {counts} ({n/(time.time()-t0)*60:.0f}/min)",flush=True)
    print(f"[verify] DONE {counts}",flush=True)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("repo"); ap.add_argument("--results",required=True); ap.add_argument("--mutdir",required=True)
    ap.add_argument("--cand",default="_local/qc_out_delivery/strengthen_cand")
    ap.add_argument("--generate",action="store_true"); ap.add_argument("--verify",action="store_true")
    ap.add_argument("--model",default="claude-opus-4-8"); ap.add_argument("--workers",type=int,default=150)
    ap.add_argument("--timeout",type=int,default=1200); ap.add_argument("--out",default="_local/qc_out_delivery/strengthen_verify.txt")
    ap.add_argument("--delivery",default="")
    ap.add_argument("--prior",default="")   # 2nd pass: prior verify results -> skip FIXED, feed failures back
    ap.add_argument("--ledger",default="")  # per-task failure ledger (JSON) -> focused next_hint feedback
    a=ap.parse_args()
    repo=os.path.abspath(a.repo); weak=load_weak(a.results)
    if a.generate: do_generate(repo,weak,a.mutdir,a.cand,a.model,a.workers,a.prior,a.ledger)
    if a.verify: do_verify(repo,weak,a.mutdir,a.cand,a.out,a.workers,a.timeout,a.delivery)

if __name__=="__main__":
    main()
