#!/usr/bin/env python3
"""Fix BROKEN ORACLES via the Claude API worker pool (billed to ANTHROPIC_API_KEY).

A broken oracle = the reference solution (solve.sh) run in a clean container does NOT satisfy
the verifier (reward=0 after solve). The solve and the tests are inconsistent, or solve is
incomplete, or a test asserts something the task/solve never produces.

Per task (one Opus call): feed instruction.md + verifier (tests/test_outputs.py, tests/test.sh)
+ reference solution (solution/solve.sh) + the OBSERVED FAILURE (which check failed and why).
Opus diagnoses which side is wrong and makes the MINIMAL fix so the oracle passes, WITHOUT
weakening the check (a no-op container must still fail). Verified SEPARATELY on Modal
(oracle=1 / no-op=0) — never trust the model's self-report.

Reuses fix_leak_api plumbing. Resumable (.oraclefix.done). Failure map: <task>->text via --fails.
"""
import argparse, concurrent.futures as cf, json, os, re, sys, threading
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path[:0] = [HERE]
import fix_leak_api as fl  # call, load_key, readfile, apply_patch, MODEL

lock = threading.Lock()
CTX = ["instruction.md", "tests/test_outputs.py", "tests/test.sh", "solution/solve.sh"]

SYS = (
 "You fix BROKEN ORACLES in Terminal-Bench (Reflection Harbor) tasks. A broken oracle means: "
 "running the reference solution solution/solve.sh in a CLEAN container and then the verifier "
 "yields reward 0 (a check FAILS) — the reference solution and the verifier are INCONSISTENT. "
 "Common causes: solve.sh doesn't produce an output/path/format the test checks; the test asserts "
 "a value solve.sh never generates; a path/filename/schema mismatch between solve and test; a "
 "test collection/import error; an off-by-one/format/threshold bug in the test; solve missing a step.\n"
 "You are given the OBSERVED FAILURE (which test failed + error). Diagnose which side is wrong and "
 "make the MINIMAL change so the reference solution PASSES every check.\n"
 "ABSOLUTE CONSTRAINTS: (1) The task must still require the real work — a no-op/empty container MUST "
 "still fail. NEVER make a test vacuous or always-pass. (2) Keep identical top-level test function "
 "names, count, and order. (3) Prefer fixing solve.sh to complete the intended work; only change the "
 "test when the test itself is wrong (mismatched expectation, bad path, collection error). (4) Do not "
 "touch task.toml, timeouts, or tests/.truth. (5) Keep the task's difficulty and intent.\n"
 "OUTPUT FORMAT (NOT JSON — use these exact plain-text markers so file bodies need no escaping):\n"
 "OUTCOME: fix-applied   (or: cannot-fix)\n"
 "ROOT_CAUSE: solve-incomplete|path-mismatch|test-wrong|collection-error|format-mismatch|other\n"
 "SUMMARY: <1-2 sentences>\n"
 "Then, for EACH file you change, emit a block exactly:\n"
 "<<<FILE solution/solve.sh\n<full new file content verbatim>\n>>>END\n"
 "Emit nothing else. If you cannot confidently fix it, output only 'OUTCOME: cannot-fix' + SUMMARY."
)


def gather(tdir, fail):
    parts = [f"\n===== OBSERVED FAILURE (clean oracle run) =====\n{fail}"] if fail else []
    for rel in CTX:
        fp = os.path.join(tdir, rel)
        if os.path.isfile(fp):
            txt = fl.readfile(fp)
            if txt is not None:
                parts.append(f"\n===== {rel} =====\n{txt}")
    # extra tests/ helpers + .truth listing (names only for .truth)
    tdir_tests = os.path.join(tdir, "tests")
    if os.path.isdir(tdir_tests):
        for dp, _, fns in os.walk(tdir_tests):
            for fn in sorted(fns):
                rel = os.path.relpath(os.path.join(dp, fn), tdir)
                if rel not in CTX and fn.endswith(".py") and "/.truth/" not in "/" + rel:
                    txt = fl.readfile(os.path.join(dp, fn))
                    if txt: parts.append(f"\n===== {rel} =====\n{txt[:6000]}")
    return "\n".join(parts)


def fix_one(key, model, tdir, task, fail):
    marker = os.path.join(tdir, ".oraclefix.done")
    if os.path.exists(marker):
        return task, "SKIP-DONE", ""
    if not os.path.isfile(os.path.join(tdir, "solution", "solve.sh")):
        return task, "NO-SOLVE", ""
    user = "Fix the broken oracle per the rules.\n" + gather(tdir, fail)
    resp = fl.call(key, model, SYS, user)
    if "_err" in resp:
        return task, "API-ERR", resp["_err"][:80]
    if resp.get("stop_reason") == "refusal":
        return task, "REFUSED", ""
    text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text").strip()
    outcome = (re.search(r"OUTCOME:\s*(\S+)", text) or [None, ""])[1] if re.search(r"OUTCOME:", text) else ""
    root = (re.search(r"ROOT_CAUSE:\s*(\S+)", text) or [None, ""])[1] if re.search(r"ROOT_CAUSE:", text) else ""
    summ = (re.search(r"SUMMARY:\s*(.+)", text) or [None, ""])[1] if re.search(r"SUMMARY:", text) else ""
    blocks = re.findall(r"<<<FILE\s+(\S+)\r?\n(.*?)\r?\n>>>END", text, re.S)
    patch = {"outcome": outcome or ("fix-applied" if blocks else "cannot-fix"),
             "root_cause": root, "summary": summ[:200],
             "files": [{"path": p, "content": c} for p, c in blocks]}
    if outcome == "cannot-fix" or (not blocks and outcome != "fix-applied"):
        patch["outcome"] = "cannot-fix"
    if not blocks and patch["outcome"] != "cannot-fix":
        return task, "NO-BLOCKS", text[:80]
    if patch.get("outcome") == "cannot-fix":
        with lock:
            json.dump(patch, open(os.path.join(tdir, "oracle_fix_report.json"), "w"), indent=2)
            open(marker, "w").write("cannot-fix")
        return task, "CANNOT-FIX", patch.get("summary", "")[:80]
    with lock:
        changed, err = fl.apply_patch(tdir, patch)
    if err:
        return task, err[:80], ""
    with lock:
        patch["files_changed"] = changed
        json.dump(patch, open(os.path.join(tdir, "oracle_fix_report.json"), "w"), indent=2)
        open(marker, "w").write("fix-applied")
    return task, "FIX-APPLIED", f"{patch.get('root_cause','')}: {changed}"[:100]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks_dir"); ap.add_argument("task_list")
    ap.add_argument("--fails", default="")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--model", default=fl.MODEL)
    ap.add_argument("--out", default="_local/qc_out_new238/oracle_fix_results.txt")
    a = ap.parse_args()
    key = fl.load_key()
    tasks = [t for t in open(a.task_list).read().split() if t]
    fails = json.load(open(a.fails)) if a.fails and os.path.isfile(a.fails) else {}
    print(f"[oracle-fix] {len(tasks)} tasks x {a.model} ({a.workers} workers). Billed to Claude API key.", flush=True)
    counts = {}; done = 0
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with cf.ThreadPoolExecutor(a.workers) as ex:
        futs = {ex.submit(fix_one, key, a.model, os.path.join(a.tasks_dir, "tasks", t), t, fails.get(t, "")): t
                for t in tasks}
        for f in cf.as_completed(futs):
            t, kind, detail = f.result(); counts[kind] = counts.get(kind, 0) + 1; done += 1
            with lock:
                open(a.out, "a").write(f"{t}\t{kind}\t{detail}\n")
            if kind not in ("SKIP-DONE",):
                print(f"  [{kind}] {t}  {detail}", flush=True)
            if done % 25 == 0 or done == len(tasks):
                print(f"  [{done}/{len(tasks)}] {counts}", flush=True)
    print("DONE", counts, flush=True)


if __name__ == "__main__":
    main()
