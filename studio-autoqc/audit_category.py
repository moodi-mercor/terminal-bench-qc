#!/usr/bin/env python3
"""Estimate how many tasks have an incorrect category/subcategory label.

Per task: feed instruction.md + a solve.sh excerpt + the current label + the 14-category
taxonomy; ask an LLM whether the assigned category best describes the DOMINANT work, and
if not, what the correct one is. Sample-based -> rate; full-run -> exact count + retag map.
"""
import argparse, concurrent.futures as cf, json, os, re, sys, threading
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path[:0]=[HERE]
import fix_leak_api as fl
lock=threading.Lock()

TAXO = json.load(open(os.path.join(HERE, "..", "_local", "qc_out_delivery", "taxonomy.json")))

SYS = (
 "You are auditing the category/subcategory labels of a Terminal-Bench task against a fixed "
 "taxonomy. Assign the category+subcategory that best describe the DOMINANT work required to "
 "solve the task in a terminal (not incidental supporting steps). If multiple categories apply, "
 "pick the one for the MAIN objective. You are given the instruction, a solution excerpt, and the "
 "currently-assigned labels.\n"
 "TAXONOMY (category -> allowed subcategories); pick exactly one category and one of ITS subcategories:\n"
 + json.dumps(TAXO) + "\n"
 "Output ONLY compact JSON: {\"correct_category\":\"<taxonomy category>\","
 "\"correct_subcategory\":\"<a subcategory listed under that category>\","
 "\"assigned_ok\":true|false,\"confidence\":0.0-1.0,\"why\":\"<=15 words\"}. "
 "assigned_ok=false ONLY when the assigned category is clearly not the dominant-work category. "
 "correct_subcategory MUST be from the chosen category's list verbatim.")

def audit_one(key, model, tdir, task, assigned):
    instr = fl.readfile(os.path.join(tdir,"instruction.md")) or ""
    solve = (fl.readfile(os.path.join(tdir,"solution","solve.sh")) or "")[:2500]
    user = (f"CURRENT category: {assigned[0]}\nCURRENT subcategory: {assigned[1]}\n\n"
            f"===== instruction.md =====\n{instr[:6000]}\n\n"
            f"===== solution/solve.sh (excerpt) =====\n{solve}")
    resp = fl.call(key, model, SYS, user)
    if "_err" in resp: return task, None, resp["_err"][:60]
    txt = "".join(b.get("text","") for b in resp.get("content",[]) if b.get("type")=="text")
    m = re.search(r"\{.*\}", txt, re.S)
    if not m: return task, None, "no-json"
    try: d=json.loads(m.group(0))
    except: return task, None, "bad-json"
    return task, d, ""

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("tasks_dir"); ap.add_argument("--list", default="")
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--workers", type=int, default=20)
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--out", default="_local/qc_out_delivery/category_audit.json")
    a=ap.parse_args()
    key=fl.load_key()
    base=a.tasks_dir
    tasks=[t for t in os.listdir(base) if os.path.isdir(f"{base}/{t}")]
    if a.list and os.path.isfile(a.list):
        keep=set(json.load(open(a.list))) if a.list.endswith(".json") else set(open(a.list).read().split())
        tasks=[t for t in tasks if t in keep]
    if a.sample:
        import hashlib
        tasks=sorted(tasks, key=lambda t: hashlib.md5(t.encode()).hexdigest())[:a.sample]
    def assigned_of(t):
        s=open(f"{base}/{t}/task.toml").read()
        c=re.search(r'^\s*category\s*=\s*"([^"]*)"',s,re.M)
        sc=re.search(r'^\s*subcategory\s*=\s*"([^"]*)"',s,re.M)
        return (c.group(1) if c else "", sc.group(1) if sc else "")
    print(f"[cat-audit] {len(tasks)} tasks x {a.model} ({a.workers}w)", flush=True)
    results={}; wrong=0; done=0
    with cf.ThreadPoolExecutor(a.workers) as ex:
        futs={ex.submit(audit_one,key,a.model,f"{base}/{t}",t,assigned_of(t)):t for t in tasks}
        for f in cf.as_completed(futs):
            t,d,err=f.result(); done+=1
            if d is not None:
                asg=assigned_of(t)
                results[t]={"assigned_category":asg[0],"assigned_subcategory":asg[1],**d}
                if d.get("assigned_ok") is False and d.get("confidence",0)>=0.6: wrong+=1
            if done%50==0 or done==len(tasks):
                with lock: json.dump(results, open(a.out,"w"), indent=1)  # incremental save
                print(f"  [{done}/{len(tasks)}] wrong-so-far={wrong}", flush=True)
    os.makedirs(os.path.dirname(a.out),exist_ok=True)
    json.dump(results, open(a.out,"w"), indent=1)
    n=len(results)
    print(f"DONE audited={n} incorrect(conf>=0.6)={wrong} rate={wrong/max(n,1)*100:.1f}%", flush=True)

if __name__=="__main__":
    main()
