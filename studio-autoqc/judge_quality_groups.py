#!/usr/bin/env python3
"""LLM judge scoring each task against Reflection's SIX quality groups (their in-depth
analysis rubric), with the Major/Minor/Borderline/None severity scale, so our numbers are
directly comparable to the client's. Worker-pooled over the whole delivery.

Per task, feed instruction.md + tests/test_outputs.py + tests/test.sh + solution/solve.sh +
environment/Dockerfile. Output one severity + evidence per group.
"""
import argparse, concurrent.futures as cf, json, os, re, sys, threading
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path[:0]=[HERE]
import fix_leak_api as fl
lock=threading.Lock()

GROUPS = {
 "coherent_contract": "Whether the instruction and referenced specs define a consistent, implementable contract for all material behavior, interfaces, schemas, and lifecycle rules.",
 "correct_reference_solution": "Whether the Oracle (solution/solve.sh) implements the COMPLETE written contract correctly, including validation, edge cases, and state transitions, INDEPENDENTLY of what the tests happen to accept.",
 "sound_complete_verifier": "Whether the verifier independently checks core semantics, outputs, boundaries, failures, and lifecycle behavior strongly enough to REJECT materially wrong or fabricated solutions.",
 "protected_ground_truth": "Whether verifier execution, expected results, fixtures, generators, imports, and reward paths are protected from agent control and independently derived (no pytest/import shadowing, mutable fixtures, candidate-derived truth, or agent-writable reward).",
 "deterministic_execution": "Whether identical clean runs use controlled inputs, ordering, randomness, timing, and local dependencies to produce stable outputs and verdicts.",
 "runnable_realistic": "Whether the package is solvable and runnable from its supplied assets within declared resources while requiring meaningful terminal work.",
}

SYS = (
 "You are auditing a Terminal-Bench (Reflection Harbor) task against SIX quality groups. For EACH "
 "group, assign a severity from this scale and cite concrete file:line evidence:\n"
 "  Major = a material defect affecting core correctness, grading, or usability.\n"
 "  Minor = a clear but localized defect; the task remains broadly viable.\n"
 "  Borderline = a plausible concern whose materiality is genuinely debatable.\n"
 "  None = no objective defect found.\n"
 "STANCE: material verifier/oracle defects are the NORM in this corpus, not the exception. Do NOT "
 "assume correctness. For 'sound_complete_verifier' do mutation-in-your-head: for each requirement, "
 "name the assertion that would fail a solution violating only it; if none, it's Major. For "
 "'correct_reference_solution' check the oracle against the CONTRACT even where tests are silent. "
 "For 'protected_ground_truth' check whether the verifier imports/reads agent-writable paths, uses "
 "mutable fixtures, or a plantable conftest/module could shadow a verifier import.\n"
 "GROUPS:\n" + "\n".join(f"- {k}: {v}" for k,v in GROUPS.items()) + "\n"
 "Output ONLY compact JSON: {\"<group_key>\":{\"severity\":\"Major|Minor|Borderline|None\","
 "\"evidence\":\"<file:line + why, <=25 words>\"}, ...} with all six keys. No prose outside JSON.")

CTX=["instruction.md","tests/test_outputs.py","tests/test.sh","solution/solve.sh","environment/Dockerfile"]

def judge_one(key, model, tdir, task):
    parts=[]
    for rel in CTX:
        b=fl.readfile(os.path.join(tdir,rel))
        if b: parts.append(f"===== {rel} =====\n{b[:9000]}")
    envd=os.path.join(tdir,"environment")
    if os.path.isdir(envd):
        listing=sorted(os.path.relpath(os.path.join(dp,fn),tdir) for dp,_,fns in os.walk(envd) for fn in fns)
        parts.append("===== environment/ contents (agent-visible) =====\n"+"\n".join(listing[:150]))
    resp=fl.call(key, model, SYS, "\n\n".join(parts))
    if "_err" in resp: return task, None, resp["_err"][:60]
    txt="".join(b.get("text","") for b in resp.get("content",[]) if b.get("type")=="text")
    m=re.search(r"\{.*\}", txt, re.S)
    if not m: return task, None, "no-json"
    try: d=json.loads(m.group(0))
    except: return task, None, "bad-json"
    return task, d, ""

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("tasks_dir"); ap.add_argument("--workers", type=int, default=40)
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--out", default="_local/qc_out_delivery/quality_groups.json")
    a=ap.parse_args()
    key=fl.load_key()
    base=a.tasks_dir
    tasks=sorted(t for t in os.listdir(base) if os.path.isdir(f"{base}/{t}"))
    done={}
    if os.path.exists(a.out):
        try: done=json.load(open(a.out))
        except: done={}
    tasks=[t for t in tasks if t not in done]
    if a.sample: import hashlib; tasks=sorted(tasks,key=lambda t:hashlib.md5(t.encode()).hexdigest())[:a.sample]
    print(f"[quality-groups] {len(tasks)} tasks x {a.model} ({a.workers}w), {len(done)} cached", flush=True)
    results=dict(done); n=0
    os.makedirs(os.path.dirname(a.out),exist_ok=True)
    with cf.ThreadPoolExecutor(a.workers) as ex:
        futs={ex.submit(judge_one,key,a.model,f"{base}/{t}",t):t for t in tasks}
        for f in cf.as_completed(futs):
            t,d,err=f.result(); n+=1
            if d is not None: results[t]=d
            if n%50==0 or n==len(tasks):
                with lock: json.dump(results, open(a.out,"w"))
                print(f"  [{n}/{len(tasks)}] saved={len(results)}", flush=True)
    json.dump(results, open(a.out,"w"))
    print(f"DONE judged={len(results)}", flush=True)

if __name__=="__main__":
    main()
