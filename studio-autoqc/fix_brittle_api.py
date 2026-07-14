#!/usr/bin/env python3
"""Fix BRITTLE verifiers via the Claude API worker pool (billed to ANTHROPIC_API_KEY).

A brittle verifier rejects VALID solutions that differ harmlessly from the reference:
over-strict exact-match on output the instruction doesn't pin, hardcoded timestamps/
dates/UUIDs/absolute paths, order-dependent comparisons, float/dict '==' without
tolerance, single-valid-solution assumptions, or nondeterministic checks.

Per task (one Opus call): feed instruction.md (what's ACTUALLY required) + the verifier
(tests/test_outputs.py, tests/test.sh) + the reference solution. Opus relaxes ONLY genuine
brittleness to what the instruction requires, or returns 'not-brittle' (change nothing).
HARD CONSTRAINT: never weaken so a wrong/empty solution passes — the assertion must still
require the real work. Verified SEPARATELY on Modal: oracle still PASSES and no-op still
FAILS (never trust the model's self-report).

Reuses fix_leak_api's API/patch plumbing. Resumable (.brittlefix.done). Same CLI shape.
"""
import argparse, concurrent.futures as cf, json, os, re, sys, threading
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path[:0] = [HERE]
import fix_leak_api as fl  # noqa: E402  (call, load_key, readfile, apply_patch, MODEL)

lock = threading.Lock()
CTX = ["instruction.md", "tests/test_outputs.py", "tests/test.sh", "solution/solve.sh"]

SYS = (
 "You fix BRITTLE verifiers in Terminal-Bench tasks. A brittle verifier wrongly REJECTS valid "
 "solutions that differ harmlessly from the reference. Brittle patterns to fix: (a) exact-string/"
 "exact-value match on output the instruction does NOT pin down (accept any correct value/format); "
 "(b) hardcoded timestamps, dates, UUIDs, hostnames, or absolute paths that vary run-to-run; "
 "(c) order-dependent comparisons of unordered data (compare as sets/sorted); (d) float or dict/list "
 "'==' without tolerance (use approx/round or key-subset checks); (e) assuming ONE valid solution when "
 "the instruction allows several; (f) NONDETERMINISTIC checks (unseeded randomness, time-of-day, race). "
 "Fix by relaxing the assertion to EXACTLY what instruction.md requires — no more, no less.\n"
 "ABSOLUTE CONSTRAINTS: (1) NEVER weaken a check so that a WRONG or EMPTY solution would pass — the "
 "verifier must still require the real work; an empty/no-op container MUST still fail every test. "
 "(2) Keep identical top-level test function names, count, and order. (3) Do not change thresholds the "
 "instruction sets, add failure-swallowing try/except, or delete assertions. (4) Do not touch task.toml "
 "or timeouts. (5) Make the MINIMAL change. (6) If the verifier is NOT actually brittle (its strictness "
 "is exactly what the instruction demands), return outcome 'not-brittle' and change NOTHING.\n"
 "OUTPUT: ONLY a JSON object, no prose/fences:\n"
 '{"outcome":"fix-applied|not-brittle|cull","root_cause":"too-strict|nondeterministic|order-dependent|'
 'float-eq|hardcoded-value|multi-valid|none","files":[{"path":"tests/test_outputs.py","content":"<FULL '
 'new file content>"}],"summary":"1-2 sentences on the brittle pattern and the relaxation","confidence":'
 '"high|med|low"}\nInclude only files you actually change (full content each).'
)


def gather(tdir):
    parts = []
    for rel in CTX:
        fp = os.path.join(tdir, rel)
        if os.path.isfile(fp):
            txt = fl.readfile(fp)
            if txt is not None:
                parts.append(f"\n===== {rel} =====\n{txt}")
    return "\n".join(parts)


def fix_one(key, model, tdir, task):
    marker = os.path.join(tdir, ".brittlefix.done")
    if os.path.exists(marker):
        return task, "SKIP-DONE", ""
    if not os.path.isfile(os.path.join(tdir, "tests", "test_outputs.py")):
        return task, "NO-TEST", ""
    user = ("Fix the brittle verifier per the rules, or return not-brittle if its strictness is "
            "exactly what the instruction requires.\n" + gather(tdir))
    resp = fl.call(key, model, SYS, user)
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
    if outcome in ("not-brittle", "cull"):
        with lock:
            json.dump(patch, open(os.path.join(tdir, "brittle_fix_report.json"), "w"), indent=2)
            open(marker, "w").write(outcome)
        return task, outcome.upper(), patch.get("summary", "")[:80]
    with lock:
        changed, err = fl.apply_patch(tdir, patch)
    if err:
        return task, err[:80], ""
    with lock:
        patch["files_changed"] = changed
        json.dump(patch, open(os.path.join(tdir, "brittle_fix_report.json"), "w"), indent=2)
        open(marker, "w").write("fix-applied")
    return task, "FIX-APPLIED", f"{changed}"[:100]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks_dir"); ap.add_argument("task_list")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--model", default=fl.MODEL)
    ap.add_argument("--out", default="_local/brittle_fix_results.txt")
    a = ap.parse_args()
    key = fl.load_key()
    tasks = [t for t in open(a.task_list).read().split() if t]
    print(f"[brittle-fix] {len(tasks)} tasks x {a.model} ({a.workers} workers). Billed to Claude API key.", flush=True)
    counts = {}; done = 0
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with cf.ThreadPoolExecutor(a.workers) as ex:
        futs = {ex.submit(fix_one, key, a.model, os.path.join(a.tasks_dir, "tasks", t), t): t for t in tasks}
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
