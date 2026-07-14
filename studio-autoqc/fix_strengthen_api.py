#!/usr/bin/env python3
"""Strengthen VACUOUS verifiers via the Claude API worker pool (billed to ANTHROPIC_API_KEY).

A vacuous verifier PASSES an untouched/empty container — it doesn't actually require the
agent's deliverable. Fix: add assertions that check the real deliverable (files/values the
instruction requires and the reference solution produces) so an EMPTY/no-op container FAILS,
while the reference solution still PASSES. Verified on Modal: no-op now FAILS and oracle PASSES.

Reuses fix_leak_api plumbing. Resumable (.strengthen.done). Never trust self-report.
"""
import argparse, concurrent.futures as cf, json, os, re, sys, threading
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path[:0] = [HERE]
import fix_leak_api as fl  # noqa: E402
lock = threading.Lock()
CTX = ["instruction.md", "tests/test_outputs.py", "tests/test.sh", "solution/solve.sh"]

SYS = (
 "You STRENGTHEN vacuous Terminal-Bench verifiers. A vacuous verifier PASSES an untouched/empty "
 "container because its assertions don't actually check the agent's required deliverable. Fix it "
 "so that an EMPTY/no-op container FAILS while the correct reference solution still PASSES.\n"
 "METHOD: read instruction.md (what the agent must deliver) and solution/solve.sh (what a correct "
 "solution produces). Add/þtighten assertions in tests/test_outputs.py that verify the real "
 "deliverable exists and is correct (the output files/values/state the instruction requires). "
 "The new checks must be things a no-op container CANNOT already satisfy.\n"
 "CONSTRAINTS: (1) The reference solution MUST still pass — assert only what a correct solution "
 "actually produces (per solve.sh + instruction). (2) Keep existing test function names; you may add "
 "new test_* functions. (3) Do NOT hardcode an answer that leaks (don't bake expected values the "
 "agent could read; derive/compute from the deliverable). (4) No failure-swallowing try/except. "
 "(5) Minimal, targeted change. (6) If the verifier is actually NOT vacuous (a no-op would already "
 "fail it), return outcome 'not-vacuous' and change nothing.\n"
 "OUTPUT: ONLY JSON, no prose/fences:\n"
 '{"outcome":"fix-applied|not-vacuous|cull","root_cause":"missing-deliverable-check|too-weak|none",'
 '"files":[{"path":"tests/test_outputs.py","content":"<FULL new content>"}],"summary":"1-2 sentences",'
 '"confidence":"high|med|low"}'
)


def gather(tdir):
    parts = []
    for rel in CTX:
        fp = os.path.join(tdir, rel)
        if os.path.isfile(fp):
            t = fl.readfile(fp)
            if t is not None:
                parts.append(f"\n===== {rel} =====\n{t}")
    return "\n".join(parts)


def fix_one(key, model, tdir, task):
    marker = os.path.join(tdir, ".strengthen.done")
    if os.path.exists(marker):
        return task, "SKIP-DONE", ""
    if not os.path.isfile(os.path.join(tdir, "tests", "test_outputs.py")):
        return task, "NO-TEST", ""
    user = ("Strengthen this vacuous verifier per the rules (an empty container must fail after "
            "your change; the reference must still pass), or return not-vacuous.\n" + gather(tdir))
    resp = fl.call(key, model, SYS, user)
    if "_err" in resp:
        return task, "API-ERR", resp["_err"][:80]
    text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text").strip()
    text = re.sub(r"^```(json)?\n", "", text); text = re.sub(r"\n```$", "", text)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return task, "NO-JSON", text[:80]
    try:
        patch = json.loads(m.group(0))
    except Exception as e:
        return task, "BAD-JSON", str(e)[:80]
    if patch.get("outcome") in ("not-vacuous", "cull"):
        with lock:
            json.dump(patch, open(os.path.join(tdir, "strengthen_report.json"), "w"), indent=2)
            open(marker, "w").write(patch["outcome"])
        return task, patch["outcome"].upper(), patch.get("summary", "")[:80]
    with lock:
        changed, err = fl.apply_patch(tdir, patch)
    if err:
        return task, err[:80], ""
    with lock:
        patch["files_changed"] = changed
        json.dump(patch, open(os.path.join(tdir, "strengthen_report.json"), "w"), indent=2)
        open(marker, "w").write("fix-applied")
    return task, "FIX-APPLIED", f"{changed}"[:100]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks_dir"); ap.add_argument("task_list")
    ap.add_argument("--workers", type=int, default=7)
    ap.add_argument("--model", default=fl.MODEL)
    ap.add_argument("--out", default="_local/strengthen_results.txt")
    a = ap.parse_args()
    key = fl.load_key()
    tasks = [t for t in open(a.task_list).read().split() if t]
    print(f"[strengthen] {len(tasks)} tasks x {a.model} ({a.workers} workers). API key.", flush=True)
    counts = {}
    with cf.ThreadPoolExecutor(a.workers) as ex:
        futs = {ex.submit(fix_one, key, a.model, os.path.join(a.tasks_dir, "tasks", t), t): t for t in tasks}
        for f in cf.as_completed(futs):
            t, kind, detail = f.result(); counts[kind] = counts.get(kind, 0) + 1
            with lock:
                open(a.out, "a").write(f"{t}\t{kind}\t{detail}\n")
            print(f"  [{kind}] {t}  {detail}", flush=True)
    print("DONE", counts, flush=True)


if __name__ == "__main__":
    main()
