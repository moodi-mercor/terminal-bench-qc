#!/usr/bin/env python3
"""Semantic-defect JUDGE + FIX over the semantic-defect bucket, via the Claude API
worker pool (billed to ANTHROPIC_API_KEY). Same machinery as judge_fix_rh_api
(full-task context read, JSON patch out, apply+AST-validate, resumable), but tuned
for the semantic-defect classes instead of leak/gameable:

  wrong-expected-value   — test asserts a value that doesn't match the correct output
  nondeterministic-verifier — verifier depends on time/order/randomness -> flaky
  coverage-gap / weak-test  — verifier doesn't check what the instruction requires
  instruction-test-mismatch — instruction says X, test checks Y
  bad-tolerance          — numeric tolerance too tight/loose
  (instruction-incomplete / unclear / trivial-task -> usually no-issue or cull)

Never trust the self-report: Modal-verify fixes after (oracle-only gate must stay OK;
no-op must still FAIL). Reuses judge_fix_rh_api.gather + fix_leak_api helpers.

Usage: python judge_fix_semantic_api.py <tasks_dir> <task_list> [--workers 60] [--out ...]
"""
import argparse, concurrent.futures as cf, json, os, re, threading
import fix_leak_api as F
import judge_fix_rh_api as J

lock = threading.Lock()

SYS = (
 "You are a QC judge+fixer for Terminal-Bench tasks flagged with a possible SEMANTIC defect "
 "in the verifier or instruction. Decide if a REAL defect exists, and if it is safely fixable, "
 "fix it. HARNESS FACT: tests/ and solution/ are grade-time mounts (NOT readable by the agent "
 "during solve); environment/ IS in the image.\n"
 "DEFECT CLASSES: (1) wrong-expected-value — a test asserts an expected value that does NOT "
 "match the output the reference solution (solution/solve.sh) actually produces / that the "
 "instruction implies; the correct reference is unfairly failed. (2) nondeterministic-verifier — "
 "the verifier's pass/fail depends on wall-clock time, dict/set/listing ORDER, unseeded "
 "randomness, or concurrency, so a correct solution can flake. (3) coverage-gap / weak-test — the "
 "verifier fails to check a substantive requirement the instruction clearly states (so wrong "
 "work still passes). (4) instruction-test-mismatch — the test checks something the instruction "
 "does not ask for (or contradicts it). (5) bad-tolerance — numeric comparison tolerance is wrong.\n"
 "FIX RULES (only if a REAL, confidently-fixable defect): wrong-expected-value -> correct the "
 "expected value to what the reference solution genuinely produces (derive it from solve.sh + "
 "instruction; only if you are confident). nondeterministic -> make the check deterministic "
 "(sort before compare, pin a seed, remove timing/order dependence) WITHOUT weakening it. "
 "coverage-gap -> ADD the missing substantive assertion the instruction requires. mismatch -> "
 "align the test to the instruction (fix whichever is wrong per the instruction as source of "
 "truth). NEVER weaken/delete a real assertion, loosen a threshold to hide a bug, add "
 "failure-swallowing try/except, or make the task trivially passable. An empty/no-op container "
 "MUST still FAIL and the reference solution MUST still pass. Minimal change; don't touch "
 "task.toml timeouts.\n"
 "Return outcome 'no-issue' if it isn't really a defect (the test is correct, the value is right, "
 "or the flag is spurious). Return 'cull' ONLY if genuinely unfixable (self-contradictory "
 "instruction, trivial-by-design task, or you cannot determine the correct behavior).\n"
 "OUTPUT ONLY JSON, no prose/fences:\n"
 '{"outcome":"fix-applied|no-issue|cull","defect":"wrong-expected-value|nondeterministic-verifier|'
 'coverage-gap|instruction-test-mismatch|bad-tolerance|none|unfixable",'
 '"files":[{"path":"<relpath>","content":"<FULL new content>"}],"delete":[],'
 '"summary":"1-2 sentences","confidence":"high|med|low"}'
)


def judge_one(key, model, tdir, task):
    marker = os.path.join(tdir, ".semjudge.done")
    if os.path.exists(marker):
        return task, "SKIP-DONE", ""
    if not os.path.isdir(os.path.join(tdir, "environment")):
        return task, "NO-TASK", ""
    user = ("Judge this task for a REAL semantic defect (wrong expected value, nondeterministic "
            "verifier, coverage gap, instruction/test mismatch, bad tolerance) and fix it only if "
            "you are confident. Otherwise return no-issue.\n\n" + J.gather(tdir))
    resp = F.call(key, model, SYS, user)
    if "_err" in resp:
        return task, "API-ERR", resp["_err"][:80]
    if resp.get("stop_reason") == "refusal":
        return task, "REFUSED", ""
    text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text").strip()
    text = re.sub(r"^```(json)?\n", "", text); text = re.sub(r"\n```$", "", text)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return task, "NO-JSON", text[:80]
    try:
        patch = json.loads(m.group(0))
    except Exception as e:
        return task, "BAD-JSON", str(e)[:80]
    outcome = patch.get("outcome")
    if outcome in ("no-issue", "cull"):
        with lock:
            json.dump(patch, open(os.path.join(tdir, "sem_judge_report.json"), "w"), indent=2)
            open(marker, "w").write(outcome)
        return task, outcome.upper(), (patch.get("defect", "") + ": " + patch.get("summary", ""))[:90]
    with lock:
        changed, err = F.apply_patch(tdir, patch)
    if err:
        return task, err[:80], ""
    with lock:
        patch["files_changed"] = changed
        json.dump(patch, open(os.path.join(tdir, "sem_judge_report.json"), "w"), indent=2)
        json.dump({"files_changed": changed}, open(os.path.join(tdir, "leak_fix_report.json"), "w"))
        open(marker, "w").write("fix-applied")
    return task, f"FIX-{patch.get('defect','?').upper()}", f"{changed}"[:100]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks_dir"); ap.add_argument("task_list")
    ap.add_argument("--workers", type=int, default=50)
    ap.add_argument("--model", default=F.MODEL)
    ap.add_argument("--out", default="_local/sem_judge_results.txt")
    a = ap.parse_args()
    key = F.load_key()
    tasks = [t for t in open(a.task_list).read().split() if t]
    print(f"[sem-judge] {len(tasks)} tasks x {a.model} ({a.workers} workers). Billed to Claude API key.", flush=True)
    counts = {}; done = 0
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with cf.ThreadPoolExecutor(a.workers) as ex:
        futs = {ex.submit(judge_one, key, a.model, os.path.join(a.tasks_dir, "tasks", t), t): t for t in tasks}
        for f in cf.as_completed(futs):
            t, kind, detail = f.result(); counts[kind] = counts.get(kind, 0) + 1; done += 1
            with lock:
                open(a.out, "a").write(f"{t}\t{kind}\t{detail}\n")
            if kind.startswith("FIX") or kind in ("CULL", "BAD-JSON", "NO-JSON", "REFUSED", "API-ERR"):
                print(f"  [{kind}] {t}  {detail}", flush=True)
            if done % 25 == 0 or done == len(tasks):
                print(f"  [{done}/{len(tasks)}] {counts}", flush=True)
    print("DONE", counts, flush=True)


if __name__ == "__main__":
    main()
