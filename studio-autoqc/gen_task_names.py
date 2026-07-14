#!/usr/bin/env python3
"""Generate meaningful lowercase-kebab names for task_<hash> tasks.

Reflection spec: the task name must be meaningful, specific, concise, and kebab-case.
The name is the directory name only (no `name` field in task.toml), and no task
references its own hash internally, so renaming is a safe directory move.

Per task: read instruction.md, ask the model for a 2-5 word slug describing the
DOMINANT work. New name = <slug>-<first8hex-of-original-hash> to guarantee
uniqueness (matches the existing kebab tasks' 8-hex-suffix convention).

Output: JSON {old_name: new_name}. Apply step is separate (apply_task_names.py).
"""
import argparse, concurrent.futures as cf, json, os, re, sys, threading
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path[:0]=[HERE]
import fix_leak_api as fl
lock=threading.Lock()

KEBAB=re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
HASH=re.compile(r'^task_([0-9a-f]{6,})$')

SYS=(
 "You name Terminal-Bench tasks. Given a task instruction, produce a short, meaningful, "
 "SPECIFIC name for the task in lowercase kebab-case (words joined by hyphens). Rules:\n"
 "- 2 to 5 words, describing the DOMINANT work / objective (not incidental steps).\n"
 "- Specific to THIS task (name the artifact/system/action), not generic ('fix-bug', 'process-data' are bad).\n"
 "- lowercase letters, digits, hyphens only. No trailing/leading hyphen. No file extension.\n"
 "Output ONLY compact JSON: {\"name\":\"<kebab-slug>\"}. No prose.")

def slugify(s):
    s=s.lower().strip()
    s=re.sub(r'[^a-z0-9]+','-',s).strip('-')
    s=re.sub(r'-{2,}','-',s)
    return s

def gen_one(key, model, tdir, task, h):
    instr=(fl.readfile(os.path.join(tdir,"instruction.md")) or "")[:5000]
    resp=fl.call(key, model, SYS, f"===== instruction.md =====\n{instr}")
    if "_err" in resp: return task, None, resp["_err"][:60]
    txt="".join(b.get("text","") for b in resp.get("content",[]) if b.get("type")=="text")
    m=re.search(r'\{.*\}', txt, re.S)
    if not m: return task, None, "no-json"
    try: slug=slugify(json.loads(m.group(0)).get("name",""))
    except: return task, None, "bad-json"
    # cap length, keep at most 5 words
    parts=[p for p in slug.split('-') if p][:5]
    slug='-'.join(parts)
    if not slug or not KEBAB.match(slug): return task, None, f"bad-slug:{slug[:30]}"
    return task, f"{slug}-{h[:8]}", ""

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("tasks_dir"); ap.add_argument("--workers", type=int, default=150)
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--out", default="_local/qc_out_delivery/rename_map.json")
    a=ap.parse_args()
    key=fl.load_key()
    base=a.tasks_dir
    all_names=set(os.listdir(base))
    todo=[]
    for t in sorted(all_names):
        m=HASH.match(t)
        if m and os.path.isdir(os.path.join(base,t)): todo.append((t,m.group(1)))
    done={}
    if os.path.exists(a.out):
        try: done=json.load(open(a.out))
        except: done={}
    todo=[(t,h) for t,h in todo if t not in done]
    print(f"[gen-names] {len(todo)} task_hash to name x {a.model} ({a.workers}w), {len(done)} cached", flush=True)
    results=dict(done); n=0; errs=0
    os.makedirs(os.path.dirname(a.out),exist_ok=True)
    with cf.ThreadPoolExecutor(a.workers) as ex:
        futs={ex.submit(gen_one,key,a.model,f"{base}/{t}",t,h):t for t,h in todo}
        for f in cf.as_completed(futs):
            t,new,err=f.result(); n+=1
            if new: results[t]=new
            else: errs+=1
            if n%50==0 or n==len(todo):
                with lock: json.dump(results, open(a.out,"w"), indent=1)
                print(f"  [{n}/{len(todo)}] named={len(results)-len(done)} err={errs}", flush=True)
    # collision-resolve: ensure every new name is unique vs existing kebab names + each other
    used=set(nm for nm in all_names if not HASH.match(nm))
    final={}
    for old,new in results.items():
        cand=new; i=2
        while cand in used or cand in final.values():
            cand=f"{new}-{i}"; i+=1
        final[old]=cand; used.add(cand)
    json.dump(final, open(a.out,"w"), indent=1)
    print(f"DONE named={len(final)} errs={errs} -> {a.out}", flush=True)

if __name__=="__main__":
    main()
