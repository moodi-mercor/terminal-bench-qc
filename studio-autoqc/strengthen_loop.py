#!/usr/bin/env python3
"""Iterative retry harness for the stubborn weak verifiers.

Loops generate->verify per round over the still-weak tasks. Each round feeds every task the
SPECIFIC recorded reason its last attempt failed (which mutant index still passed + what that
mutant violates, or that the oracle broke = over-tightened), so the model corrects instead of
re-trying blind. Alternates models across rounds for diversity. A candidate is applied only if
Modal proves it (oracle still reward=1 AND every surviving mutant now reward=0). Stops when a
round yields no new fixes or the pool empties.

Usage:
  python strengthen_loop.py <repo> --results dedup.jsonl --mutdir M --remaining LIST \
     --delivery DIR --out loop_results.txt [--rounds 4] [--workers 200]
"""
import argparse, concurrent.futures as cf, json, os, re, shutil, sys, threading, time
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path[:0]=[HERE]
import fix_leak_api as fl
from strengthen_verifiers import gen_one, verify_one, _survivor_indices
import modal
lock=threading.Lock()

MODELS=["claude-opus-4-8","gpt-5.6","claude-opus-4-8","gpt-5.6-sol"]  # cycled per round (diversity)

def build_note(task, mutdir, prior_verdict, prior_detail, round_no):
    """Rich, specific feedback from the last failure."""
    od=os.path.join(mutdir,task)
    meta=json.load(open(os.path.join(od,"mutants.json"))) if os.path.exists(os.path.join(od,"mutants.json")) else []
    if prior_verdict=="ORACLE-BROKE":
        return (f"Round {round_no}: your last attempt BROKE THE ORACLE ({prior_detail}). You over-tightened "
                "— you added an assertion a CORRECT reference solution does not satisfy. Relax: assert ONLY "
                "what the written contract strictly requires; recompute expected values from the real inputs.")
    if prior_verdict=="MUTANT-SURVIVES":
        idxs=re.findall(r"\d+", prior_detail)
        reqs=[m.get("requirement_violated","") for m in meta if str(m.get("i")) in idxs]
        r="; ".join(reqs[:4]) or "the recorded requirement"
        bodies=[]
        for m in meta:
            if str(m.get("i")) in idxs:
                sh=fl.readfile(os.path.join(od,f"mut_{m['i']}.sh")) or ""
                if sh.strip(): bodies.append(f"[mutant {m['i']} violates: {m.get('requirement_violated','')}]\n{sh[:2500]}")
        extra=("\n\nThe exact mutant(s) that STILL PASSED:\n"+"\n".join(bodies)) if bodies else ""
        return (f"Round {round_no}: mutant(s) {idxs} STILL PASSED your last verifier — your assertion did NOT "
                f"distinguish this wrong solution from a correct one. It violates: {r}. Inspect what that mutant "
                f"produces vs the contract and assert on the CONCRETE observable difference in the produced "
                f"artifact/state.{extra}")
    return ""  # no prior record -> fresh attempt

def apply_fixed(cand, task, delivery):
    src=os.path.join(cand,task+".py"); dst=os.path.join(delivery,task,"tests","test_outputs.py")
    if os.path.exists(src) and os.path.isdir(os.path.dirname(dst)): shutil.copy(src,dst); return True
    return False

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("repo"); ap.add_argument("--results",required=True); ap.add_argument("--mutdir",required=True)
    ap.add_argument("--remaining",required=True); ap.add_argument("--delivery",required=True)
    ap.add_argument("--notes",default="_local/qc_out_delivery/retry_notes.json")
    ap.add_argument("--rounds",type=int,default=4); ap.add_argument("--workers",type=int,default=200)
    ap.add_argument("--timeout",type=int,default=1200)
    ap.add_argument("--out",default="_local/qc_out_delivery/strengthen_loop.txt")
    ap.add_argument("--canddir",default="_local/qc_out_delivery/loop_cand")
    a=ap.parse_args()
    repo=os.path.abspath(a.repo); key=fl.load_key()
    app=modal.App.lookup("refl-reward-gate-v2",create_if_missing=True)
    rows=[json.loads(l) for l in open(a.results)]
    surv={}  # task -> survivor indices
    for r in rows:
        od=os.path.join(a.mutdir,r["task"]); mj=os.path.join(od,"mutants.json")
        meta=json.load(open(mj)) if os.path.exists(mj) else []
        surv[r["task"]]=_survivor_indices(meta,r.get("survived_reqs"))
    remaining=[t for t in open(a.remaining).read().split() if os.path.isdir(os.path.join(repo,"tasks",t))]
    notes_prior=json.load(open(a.notes)) if os.path.exists(a.notes) else {}
    # seed feedback from the recorded last failure
    feedback={t: build_note(t,a.mutdir, notes_prior.get(t,{}).get("verdict",""),
                            notes_prior.get(t,{}).get("detail",""), 0) for t in remaining}
    total_fixed=0
    print(f"[loop] {len(remaining)} weak, {a.rounds} rounds, workers {a.workers}",flush=True)
    for rnd in range(1,a.rounds+1):
        if not remaining: break
        model=MODELS[(rnd-1)%len(MODELS)]
        cand=f"{a.canddir}_r{rnd}"; shutil.rmtree(cand,ignore_errors=True); os.makedirs(cand,exist_ok=True)
        gen_one._idx={t:surv[t] for t in remaining}; gen_one._feedback=dict(feedback)
        # ---- generate ----
        made=[]
        with cf.ThreadPoolExecutor(a.workers) as ex:
            futs={ex.submit(gen_one,key,model,os.path.join(repo,"tasks",t),t,a.mutdir):t for t in remaining}
            for f in cf.as_completed(futs):
                t,code,err=f.result()
                if code: open(os.path.join(cand,t+".py"),"w").write(code); made.append(t)
        print(f"[loop r{rnd}/{model}] candidates {len(made)}/{len(remaining)}",flush=True)
        # ---- verify + apply ----
        newfixed=[]
        def vf(t): return verify_one(app,repo,t,a.mutdir,cand,surv[t],a.timeout)
        with cf.ThreadPoolExecutor(a.workers) as ex:
            futs={ex.submit(vf,t):t for t in made}
            for f in cf.as_completed(futs):
                t,verdict,detail=f.result()
                with lock: open(a.out,"a").write(f"r{rnd}\t{model}\t{t}\t{verdict}\t{detail}\n")
                if verdict=="FIXED":
                    if apply_fixed(cand,t,a.delivery): newfixed.append(t)
                else:
                    feedback[t]=build_note(t,a.mutdir,verdict,detail,rnd)  # refine note for next round
        remaining=[t for t in remaining if t not in newfixed]
        total_fixed+=len(newfixed)
        print(f"[loop r{rnd}/{model}] FIXED +{len(newfixed)}  (total +{total_fixed}, remaining {len(remaining)})",flush=True)
        if not newfixed:
            print(f"[loop] round {rnd} yielded 0 new fixes — stopping.",flush=True); break
    print(f"[loop] DONE total_new_fixed={total_fixed} remaining_weak={len(remaining)}",flush=True)
    open(a.remaining+".after_loop","w").write("\n".join(remaining))

if __name__=="__main__":
    main()
