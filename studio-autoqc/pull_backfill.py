#!/usr/bin/env python3
"""Concurrently pull candidate backfill task trees from the Reflection RLS world
into a gate-ready <out>/tasks/<task_name> layout. Reuses studio_pull primitives."""
import os,sys,json,concurrent.futures as cf,threading,argparse
HERE=os.path.dirname(os.path.abspath(__file__))
sys.path[:0]=[os.path.join(HERE,"..","skills","static-semantic-qc","scripts")]
import studio_pull as sp
lock=threading.Lock()
WORLD="world_d07785c2757b4a5cb643517cbea8ec98"

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--cand", default="_local/refl_eval_pool/backfill_candidates.json")
    ap.add_argument("--out", default="_local/refl_eval_pool/backfill_pull")
    ap.add_argument("--n", type=int, default=1200)
    ap.add_argument("--workers", type=int, default=50)
    a=ap.parse_args()
    key=sp.load_key() if hasattr(sp,'load_key') else os.environ.get("RLS_KEY")
    # world list (id->task dict) for pull_task
    wl={t["task_id"]:t for t in sp.list_tasks(key, WORLD)}
    cand=json.load(open(a.cand))
    # category-diverse selection: fully include thin cats, sample abundant
    from collections import defaultdict
    bycat=defaultdict(list)
    for c in cand: bycat[c["category"]].append(c["task_id"])
    order=sorted(bycat, key=lambda k: len(bycat[k]))  # thin first
    sel=[]; 
    # round-robin to keep diversity
    import itertools
    pools={k:iter(v) for k,v in bycat.items()}
    while len(sel)<a.n:
        added=False
        for k in order:
            try: sel.append(next(pools[k])); added=True
            except StopIteration: pass
            if len(sel)>=a.n: break
        if not added: break
    sel=[tid for tid in sel if tid in wl]
    outroot=os.path.join(a.out,"tasks"); os.makedirs(outroot,exist_ok=True)
    done=set(os.listdir(outroot)) if os.path.isdir(outroot) else set()
    todo=[tid for tid in sel if wl[tid].get("task_name") not in done]
    print(f"selected {len(sel)}, to pull {len(todo)} ({a.workers}w)",flush=True)
    ok=0;err=0;n=0
    def one(tid):
        try: sp.pull_task(key, wl[tid], outroot); return tid,True,""
        except Exception as e: return tid,False,str(e)[:80]
    with cf.ThreadPoolExecutor(a.workers) as ex:
        for f in cf.as_completed([ex.submit(one,t) for t in todo]):
            tid,good,e=f.result(); n+=1
            if good: ok+=1
            else: err+=1
            if n%50==0 or n==len(todo): print(f"  [{n}/{len(todo)}] ok={ok} err={err}",flush=True)
    print(f"DONE pulled ok={ok} err={err} -> {outroot}",flush=True)

if __name__=="__main__": main()
