#!/usr/bin/env python3
"""Adversarially verify the client's 'Correct reference solution' Major findings against the
actual task files. For each finding: read the client's rationale + instruction + solve.sh +
the cited environment files, and judge whether the claim is CONFIRMED (real oracle bug) or
REFUTED (false positive). Opus, worker-pooled. Honest: confirm real bugs, refute only with a
concrete reason grounded in the files."""
import argparse, concurrent.futures as cf, json, os, re, sys, threading
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path[:0]=[HERE]
import fix_leak_api as fl
lock=threading.Lock()

SYS=(
 "You are adversarially checking a reviewer's claim that a Terminal-Bench task's REFERENCE "
 "SOLUTION (solution/solve.sh) is incorrect against the written contract. You are given the "
 "reviewer's rationale and the actual task files. Try HARD to REFUTE the claim, but stay honest: "
 "confirm it if it is real.\n"
 "REFUTE when: the cited behavior is actually correct; the 'violated requirement' is not in the "
 "contract / instruction (the reviewer invented or over-read it); the cited lines do not say what "
 "the reviewer claims; the edge case cannot occur given the task's fixed inputs; or standard/"
 "documented behavior covers it.\n"
 "CONFIRM when: the solve.sh genuinely produces a wrong/contract-violating result on an input the "
 "task allows.\n"
 "Output ONLY JSON: {\"verdict\":\"CONFIRMED|REFUTED|UNSURE\",\"confidence\":0.0-1.0,"
 "\"reason\":\"<=40 words citing file:line>\"}.")

CTX=["instruction.md","solution/solve.sh"]

def refute_one(key, model, tdir, task, rationale):
    parts=[f"===== REVIEWER CLAIM (oracle is wrong) =====\n{rationale}"]
    for rel in CTX:
        b=fl.readfile(os.path.join(tdir,rel))
        if b: parts.append(f"===== {rel} =====\n{b[:8000]}")
    # pull any environment file cited in the rationale (e.g. environment/spec.md:51-53)
    for m in re.findall(r'([A-Za-z0-9_./\-]+\.(?:md|txt|c|py|h|json|toml|cfg|conf))', rationale):
        fp=os.path.join(tdir, m.split(":")[0])
        if os.path.isfile(fp):
            b=fl.readfile(fp)
            if b: parts.append(f"===== {m} =====\n{b[:5000]}")
    resp=fl.call(key, model, SYS, "\n\n".join(parts))
    if "_err" in resp: return task, None, resp["_err"][:60]
    txt="".join(b.get("text","") for b in resp.get("content",[]) if b.get("type")=="text")
    m=re.search(r"\{.*\}", txt, re.S)
    if not m: return task, None, "no-json"
    try: return task, json.loads(m.group(0)), ""
    except: return task, None, "bad-json"

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("delivery_dir"); ap.add_argument("findings_json")
    ap.add_argument("--workers", type=int, default=20)
    ap.add_argument("--model", default="claude-opus-4-8")
    ap.add_argument("--out", default="_local/qc_out_delivery/oracle_refute.json")
    a=ap.parse_args()
    key=fl.load_key()
    findings=json.load(open(a.findings_json))
    present=[f for f in findings if os.path.isdir(os.path.join(a.delivery_dir, f["task_id"]))]
    print(f"[refute] {len(present)} findings present in delivery x {a.model} ({a.workers}w)", flush=True)
    results={}; done=0; conf=0; ref=0
    with cf.ThreadPoolExecutor(a.workers) as ex:
        futs={ex.submit(refute_one,key,a.model,os.path.join(a.delivery_dir,f["task_id"]),f["task_id"],f["rationale"]):f for f in present}
        for fut in cf.as_completed(futs):
            t,d,err=fut.result(); done+=1
            if d is not None:
                results[t]=d
                if d.get("verdict")=="CONFIRMED": conf+=1
                elif d.get("verdict")=="REFUTED": ref+=1
            else: print(f"  [err] {t}: {err}", flush=True)
            if done%10==0 or done==len(present): print(f"  [{done}/{len(present)}] confirmed={conf} refuted={ref}", flush=True)
    json.dump(results, open(a.out,"w"), indent=1)
    print(f"DONE confirmed={conf} refuted={ref} unsure={done-conf-ref-0}", flush=True)

if __name__=="__main__":
    main()
