#!/usr/bin/env python3
"""Semantic reward-hack JUDGE + FIX over every task, via the Claude API worker pool
(billed to ANTHROPIC_API_KEY, not the session). Closes the recall gap the filename
probe leaves: instead of trusting a sig-file flag, Opus READS the whole task and
decides whether a real defect exists, then fixes it.

Two defect classes judged per task:
  LEAK      — the verifier's expected answer is obtainable from AGENT-READABLE files
              (environment/ + anything a build script leaves in the image) WITHOUT
              doing the task work. (tests/ + solution/ are grade-time mounts, NOT
              readable during solve.)
  GAMEABLE  — a trivial / no-op / partial solution passes the verifier.

Context fed per task: instruction.md, tests/test_outputs.py, tests/test.sh,
solution/solve.sh, environment/Dockerfile, a LISTING of every environment/ file,
and the inlined contents of the small text files under environment/ (so a plainly
named answer file is visible, not just sig-named ones).

Fix rules + JSON output are identical to fix_leak_api. Never trust the self-report:
Modal-verify fixes afterwards (oracle-only gate = still grades; no-op = still fails;
re-run this judge or the probe = defect addressed).

Usage:
  python judge_fix_rh_api.py <tasks_dir> <task_list> [--workers 10] [--out ...]
"""
import argparse, ast, concurrent.futures as cf, json, os, re, sys, threading
import fix_leak_api as F  # reuse call/load_key/apply_patch/readfile

lock = threading.Lock()
CTX = ["instruction.md", "tests/test_outputs.py", "tests/test.sh",
       "solution/solve.sh", "environment/Dockerfile"]
INLINE_CAP = 8000       # per env file
TOTAL_CAP = 60000       # total context budget

SYS = (
 "You are a QC judge+fixer for Terminal-Bench tasks. Decide if the task has a REAL "
 "reward-hack defect, and if so fix it. HARNESS FACT: tests/ and solution/ are mounted "
 "ONLY at grade time — NOT present in the image the agent explores. Everything else "
 "(environment/ and anything a build/setup script writes) IS in the image and agent-readable.\n"
 "DEFECTS: (1) LEAK — the verifier's expected answer/output is obtainable from an "
 "agent-readable file (baked in environment/, or written into the image by a setup script) "
 "so the agent can copy it instead of doing the work. Values legitimately DERIVED from input, "
 "genuine task INPUT, instruction-declared worked EXAMPLES, and build-time generators that are "
 "DELETED before the image finalizes are NOT leaks. (2) GAMEABLE — a trivial/no-op/partial "
 "solution passes the verifier (e.g. it only checks a file exists, or asserts nothing real).\n"
 "FIX RULES (only if a REAL defect): leak -> if the answer file is read by nobody, remove the "
 "code that writes it; if the verifier reads it, relocate generation to GRADE time (edit "
 "tests/test.sh to regenerate deterministically, or move a static answer under tests/) and point "
 "the verifier there; stop writing it into agent-readable space. gameable -> strengthen the "
 "verifier to require the real work (add the missing substantive assertions the instruction "
 "implies), never by trivial means. NEVER delete/weaken an assertion, change thresholds, or add "
 "failure-swallowing try/except. The task must stay solvable by doing the work and an empty/no-op "
 "container must FAIL. Minimal change. Do not touch task.toml timeouts.\n"
 "OUTPUT ONLY JSON, no prose/fences:\n"
 '{"outcome":"fix-applied|no-issue|cull","defect":"leak|gameable|none|unfixable",'
 '"files":[{"path":"<relpath>","content":"<FULL new content>"}],"delete":["<relpath>"],'
 '"summary":"1-2 sentences","confidence":"high|med|low"}'
)


def env_context(tdir):
    envd = os.path.join(tdir, "environment")
    if not os.path.isdir(envd):
        return "(no environment/)"
    listing, inlined, total = [], [], 0
    for dp, dns, fns in os.walk(envd):
        dns[:] = [d for d in dns if d not in ("__pycache__", ".git")]
        for f in sorted(fns):
            fp = os.path.join(dp, f)
            rel = os.path.relpath(fp, tdir)
            try:
                sz = os.path.getsize(fp)
            except OSError:
                continue
            listing.append(f"  {rel} ({sz}B)")
            if total < TOTAL_CAP and rel not in CTX:
                txt = F.readfile(fp)  # None if binary/huge (readfile caps at MAXBYTES)
                if txt is not None and sz <= INLINE_CAP:
                    inlined.append(f"\n----- {rel} -----\n{txt}")
                    total += len(txt)
    return ("environment/ FILE LISTING (all files present in the image):\n"
            + "\n".join(listing)
            + "\n\nENVIRONMENT FILE CONTENTS (small text files inlined):"
            + "".join(inlined))


def gather(tdir):
    parts = []
    for rel in CTX:
        fp = os.path.join(tdir, rel)
        if os.path.isfile(fp):
            t = F.readfile(fp)
            if t is not None:
                parts.append(f"===== {rel} =====\n{t}")
    parts.append("===== " + "environment listing + inlined files" + " =====\n" + env_context(tdir))
    return "\n\n".join(parts)


def judge_one(key, model, tdir, task):
    marker = os.path.join(tdir, ".rhjudge.done")
    if os.path.exists(marker):
        return task, "SKIP-DONE", ""
    if not os.path.isdir(os.path.join(tdir, "environment")):
        return task, "NO-TASK", ""
    user = ("Judge this task for a reward-hack LEAK or GAMEABLE-verifier defect, and fix it "
            "if the defect is real. If there is no real defect, return no-issue.\n\n" + gather(tdir))
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
            json.dump(patch, open(os.path.join(tdir, "rh_judge_report.json"), "w"), indent=2)
            open(marker, "w").write(outcome)
        return task, outcome.upper(), (patch.get("defect", "") + ": " + patch.get("summary", ""))[:90]
    with lock:
        changed, err = F.apply_patch(tdir, patch)
    if err:
        return task, err[:80], ""
    with lock:
        patch["files_changed"] = changed
        json.dump(patch, open(os.path.join(tdir, "rh_judge_report.json"), "w"), indent=2)
        # also write leak_fix_report.json so the uploader picks up files_changed
        json.dump({"files_changed": changed}, open(os.path.join(tdir, "leak_fix_report.json"), "w"))
        open(marker, "w").write("fix-applied")
    return task, f"FIX-{patch.get('defect','?').upper()}", f"{changed}"[:100]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks_dir"); ap.add_argument("task_list")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--model", default=F.MODEL)
    ap.add_argument("--out", default="_local/rh_judge_results.txt")
    a = ap.parse_args()
    key = F.load_key()
    tasks = [t for t in open(a.task_list).read().split() if t]
    print(f"[rh-judge] {len(tasks)} tasks x {a.model} ({a.workers} workers). Billed to Claude API key.", flush=True)
    counts = {}; done = 0
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with cf.ThreadPoolExecutor(a.workers) as ex:
        futs = {ex.submit(judge_one, key, a.model, os.path.join(a.tasks_dir, "tasks", t), t): t for t in tasks}
        for f in cf.as_completed(futs):
            t, kind, detail = f.result(); counts[kind] = counts.get(kind, 0) + 1; done += 1
            with lock:
                open(a.out, "a").write(f"{t}\t{kind}\t{detail}\n")
            if kind.startswith("FIX") or kind in ("CULL", "API-ERR", "BAD-JSON", "NO-JSON", "REFUSED"):
                print(f"  [{kind}] {t}  {detail}", flush=True)
            if done % 25 == 0 or done == len(tasks):
                print(f"  [{done}/{len(tasks)}] {counts}", flush=True)
    print("DONE", counts, flush=True)


if __name__ == "__main__":
    main()
