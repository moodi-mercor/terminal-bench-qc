#!/usr/bin/env python3
"""Mutation testing for verifier soundness (objective, reproducible — not an LLM opinion).

For each task: take the reference solution, generate K MUTANTS that each still do the task
but violate exactly one stated requirement (a plausible-but-wrong solution). Run each mutant
through the real verifier on Modal. A mutant that still scores reward=1 is a SURVIVOR — the
verifier failed to catch a materially wrong solution, which is objective proof it is too weak.

  mutation score = killed / total   (killed = reward 0 = verifier rejected the wrong solution)
  a task's verifier is WEAK if any mutant survives (reward 1).

Two phases:
  --generate : LLM worker pool writes K mutant solve.sh per task -> <mutdir>/<task>/mut_<i>.sh
  --run      : Modal builds each task image, runs each mutant, runs test.sh, reads reward.

Usage:
  python mutation_test.py <repo> <task_list> --generate --mutdir M --k 3 [--model ...]
  python mutation_test.py <repo> <task_list> --run --mutdir M --out R.txt [--workers 100]
"""
import argparse, concurrent.futures as cf, json, os, re, sys, threading, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "..", "..", "studio-autoqc"), HERE]
lock = threading.Lock()

# ----------------------------------------------------------------- generate --
GEN_SYS = (
 "You produce MUTANTS of a Terminal-Bench reference solution to test whether the task's verifier "
 "is strict enough. A mutant is a COMPLETE, runnable solution script that does the task the normal "
 "way BUT violates exactly ONE stated requirement in a plausible way a real, slightly-wrong "
 "submission might: skip an edge case, mishandle a malformed/boundary input, drop a required field, "
 "omit a validation, use a shortcut that ignores a durability/ordering rule, etc. The mutant should "
 "look like an honest attempt, NOT an obvious no-op — a good verifier must still reject it.\n"
 "You are given instruction.md, the reference solution/solve.sh, and tests/test_outputs.py.\n"
 "Produce K mutants, each violating a DIFFERENT requirement the verifier ought to catch. Each is a "
 "full script (with shebang) that would run in the task container. OUTPUT FORMAT — NOT JSON, use "
 "these exact plain-text markers so script bodies need no escaping. For EACH mutant emit:\n"
 "<<<MUTANT requirement=<one short phrase of what it violates>\n<full script verbatim>\n>>>END\n"
 "Emit nothing else between/around the blocks.")

def gen_mutants(key, model, tdir, task, k):
    import fix_leak_api as fl
    instr = fl.readfile(os.path.join(tdir, "instruction.md")) or ""
    solve = fl.readfile(os.path.join(tdir, "solution", "solve.sh")) or ""
    tout  = fl.readfile(os.path.join(tdir, "tests", "test_outputs.py")) or ""
    if not solve:
        return task, [], "no-solve"
    user = (f"Produce {k} mutants.\n\n===== instruction.md =====\n{instr[:6000]}\n\n"
            f"===== solution/solve.sh (reference) =====\n{solve[:6000]}\n\n"
            f"===== tests/test_outputs.py =====\n{tout[:7000]}")
    resp = fl.call(key, model, GEN_SYS, user, effort="low")
    if "_err" in resp: return task, [], resp["_err"][:60]
    txt = "".join(b.get("text","") for b in resp.get("content",[]) if b.get("type")=="text")
    blocks = re.findall(r"<<<MUTANT\s+requirement=(.*?)\r?\n(.*?)\r?\n>>>END", txt, re.S)
    if not blocks: return task, [], "no-blocks"
    muts = [{"requirement_violated": req.strip()[:80], "solve_sh": body}
            for req, body in blocks if body.strip()]
    return task, muts[:k], ""

def do_generate(repo, tasks, mutdir, k, model, workers):
    import fix_leak_api as fl
    key = fl.load_key()
    print(f"[gen] {len(tasks)} tasks x {k} mutants x {model} ({workers}w)", flush=True)
    done = 0; total = 0
    with cf.ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(gen_mutants, key, model, os.path.join(repo,"tasks",t), t, k): t for t in tasks}
        for f in cf.as_completed(futs):
            t, muts, err = f.result(); done += 1
            od = os.path.join(mutdir, t); os.makedirs(od, exist_ok=True)
            meta = []
            for i, mu in enumerate(muts):
                sh = mu.get("solve_sh","")
                if not sh.strip(): continue
                open(os.path.join(od, f"mut_{i}.sh"), "w").write(sh)
                meta.append({"i": i, "requirement_violated": mu.get("requirement_violated","")})
                total += 1
            json.dump(meta, open(os.path.join(od, "mutants.json"), "w"))
            if err: print(f"  [gen-err] {t}: {err}", flush=True)
            if done % 25 == 0 or done == len(tasks):
                print(f"  [{done}/{len(tasks)}] mutants written: {total}", flush=True)
    print(f"[gen] DONE mutants={total}", flush=True)

# ---------------------------------------------------------------------- run --
APP_NAME = "refl-mutation-test"

def run_task_mutants(app, repo, task, mutdir, timeout):
    import modal
    tdir = os.path.join(repo, "tasks", task)
    od = os.path.join(mutdir, task)
    meta = json.load(open(os.path.join(od, "mutants.json"))) if os.path.exists(os.path.join(od,"mutants.json")) else []
    muts = [(m["i"], m.get("requirement_violated","")) for m in meta
            if os.path.exists(os.path.join(od, f"mut_{m['i']}.sh"))]
    if not muts:
        return task, {"total": 0, "survived": 0, "killed": 0, "detail": "no-mutants"}
    img = (modal.Image.from_dockerfile(
                os.path.join(tdir, "environment", "Dockerfile"),
                context_dir=os.path.join(tdir, "environment"))
           .add_local_dir(os.path.join(tdir, "tests"), remote_path="/tests")
           .add_local_dir(od, remote_path="/mutants"))
    sb = None; survived = []; killed = 0
    try:
        sb = modal.Sandbox.create(app=app, image=img, timeout=timeout, cpu=2, memory=4096)
        for i, req in muts:
            script = (
                "rm -f /logs/verifier/reward.txt /logs/tests/reward.txt 2>/dev/null; "
                "mkdir -p /logs/tests /logs/verifier /solution; "
                f"cp /mutants/mut_{i}.sh /solution/solve.sh; chmod +x /solution/solve.sh; "
                "bash /solution/solve.sh >/tmp/m.log 2>&1; "
                "bash /tests/test.sh >/tmp/t.log 2>&1; "
                "R=$(cat /logs/verifier/reward.txt /logs/tests/reward.txt 2>/dev/null | head -n1 | tr -dc '0-9.'); "
                "echo \"MUT_REWARD=[$R]\"")
            p = sb.exec("bash", "-lc", script); out = p.stdout.read(); p.wait()
            mr = re.search(r"MUT_REWARD=\[([\d.]*)\]", out)
            r = mr.group(1) if mr else ""
            if r and abs(float(r) - 1.0) < 1e-9:
                survived.append(req or f"mut_{i}")   # verifier accepted a wrong solution
            else:
                killed += 1
        return task, {"total": len(muts), "survived": len(survived), "killed": killed,
                      "survived_reqs": survived[:6]}
    except Exception as e:
        return task, {"total": len(muts), "survived": 0, "killed": 0, "detail": str(e)[-120:]}
    finally:
        if sb is not None:
            try: sb.terminate()
            except Exception: pass

def do_run(repo, tasks, mutdir, out, workers, timeout):
    import modal
    app = modal.App.lookup(APP_NAME, create_if_missing=True)
    tasks = [t for t in tasks if os.path.isdir(os.path.join(mutdir, t))]
    print(f"[run] {len(tasks)} tasks on Modal ({workers}w)", flush=True)
    weak = 0; done = 0; t0 = time.time()
    with cf.ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(run_task_mutants, app, repo, t, mutdir, timeout): t for t in tasks}
        for f in cf.as_completed(futs):
            t, res = f.result(); done += 1
            if res.get("survived", 0) > 0: weak += 1
            with lock:
                open(out, "a").write(json.dumps({"task": t, **res}) + "\n")
            if res.get("survived",0) > 0:
                print(f"  [WEAK] {t}: {res['survived']}/{res['total']} mutants survived {res.get('survived_reqs')}", flush=True)
            if done % 25 == 0 or done == len(tasks):
                print(f"  [{done}/{len(tasks)}] weak-verifiers={weak} ({done/(time.time()-t0)*60:.0f}/min)", flush=True)
    print(f"[run] DONE weak-verifiers={weak}/{done}", flush=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo"); ap.add_argument("tasks")
    ap.add_argument("--generate", action="store_true"); ap.add_argument("--run", action="store_true")
    ap.add_argument("--mutdir", required=True)
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--workers", type=int, default=40)
    ap.add_argument("--timeout", type=int, default=1500)
    ap.add_argument("--out", default="mutation_results.jsonl")
    a = ap.parse_args()
    repo = os.path.abspath(a.repo)
    tasks = [t for t in open(a.tasks).read().split() if t]
    os.makedirs(a.mutdir, exist_ok=True)
    if a.generate: do_generate(repo, tasks, a.mutdir, a.k, a.model, a.workers)
    if a.run: do_run(repo, tasks, a.mutdir, a.out, a.workers, a.timeout)

if __name__ == "__main__":
    main()
