#!/usr/bin/env python3
"""Fix reward-hack 'leaked answer in image' defects via the Claude API (worker pool),
billed to ANTHROPIC_API_KEY — NOT the Claude Code session. Same shape as refactor_tests.py.

Per task (one Opus call): feed the answer/verifier file(s) that survive in agent-visible
space + the scripts that WRITE and READ them + instruction.md + the fix rules. Opus returns
a JSON patch (full new file contents + deletes). We apply, AST-validate changed .py, and
mark done. Leak-gone + still-gradable are confirmed SEPARATELY on Modal (modal_rh_probe +
oracle-only gate) after the pool finishes — never trust the model's self-report.

Fix rule: the expected answer must live in tests/ (grade-time mount) or be inlined in
test_outputs.py — NEVER in the image. Leftover answer files read by nobody -> delete the
write. Verifier-read answer files -> relocate generation to grade time (tests/test.sh) or
into tests/. Never weaken/gut an assertion; keep the task solvable + gradable.

Usage:
  python fix_leak_api.py <tasks_dir> <task_list> --sigs _local/rh_sig_map.json \
      [--workers 6] [--model claude-opus-4-8] [--out _local/leak_fix_results.txt]
"""
import argparse, ast, concurrent.futures as cf, json, os, re, subprocess, sys, threading, time
import urllib.request, urllib.error

API_URL = "https://api.anthropic.com/v1/messages"; VER = "2023-06-01"; MODEL = "claude-opus-4-8"
HERE = os.path.dirname(os.path.abspath(__file__)); lock = threading.Lock()
MAXBYTES = 24000  # per-file cap fed to the model (skip huge/binary data files)
CTX_FILES = ["instruction.md", "tests/test_outputs.py", "tests/test.sh",
             "solution/solve.sh", "environment/Dockerfile"]


def load_key():
    for v in ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"):
        if os.environ.get(v):
            return os.environ[v].strip()
    here = HERE
    for _ in range(6):
        p = os.path.join(here, ".env")
        if os.path.isfile(p):
            for ln in open(p):
                for v in ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"):
                    if ln.strip().startswith(v + "="):
                        return ln.split("=", 1)[1].strip().strip('"').strip("'")
        here = os.path.dirname(here)
    sys.exit("no Claude API key")


SYS = (
 "You remove reward-hack ANSWER LEAKS from Terminal-Bench tasks. A leak = the verifier's "
 "expected answer is present in an AGENT-READABLE file baked into the Docker image. "
 "HARNESS FACT: tests/ and solution/ are mounted ONLY at grade time (not in the image the "
 "agent explores); everything else (environment/ and anything a build/setup script writes) "
 "IS in the image. So the answer must live in tests/ (or be inlined in tests/test_outputs.py), "
 "NEVER in the image.\n"
 "FIX RULES: (1) If an answer file is read by NOBODY (not the verifier, not an in-image grader) "
 "-> it's a leftover: remove the code that writes it (and delete the file). (2) If the verifier "
 "reads it, relocate: generate/copy the answer into tests/ at GRADE time (edit tests/test.sh to "
 "regenerate it deterministically, or move a static answer file under tests/) and point the "
 "verifier there; stop writing it into agent-visible space. (3) NEVER delete or weaken an "
 "assertion, change thresholds, or add failure-swallowing try/except. The task must stay "
 "solvable by doing the work, and the verifier must still have its expected values at grade time. "
 "An empty/no-op container must still FAIL. (4) Make the MINIMAL change. Do not touch task.toml "
 "timeouts. (5) If, after analysis, the flagged file is NOT actually a leak (legitimate input the "
 "agent must process, a value merely derived from input, a build-deleted generator, or an "
 "instruction-declared worked example), return outcome 'no-leak' and change nothing.\n"
 "OUTPUT: return ONLY a JSON object, no prose, no fences:\n"
 '{"outcome":"fix-applied|no-leak|cull","root_cause":"leftover|relocate|derived|by-design|unfixable",'
 '"files":[{"path":"<relpath under the task dir>","content":"<FULL new file content>"}],'
 '"delete":["<relpath to remove>"],"summary":"1-2 sentences","confidence":"high|med|low"}\n'
 "Only include files you actually change in 'files' (full content each). 'delete' for files to remove."
)


OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_OAI_KEY = [None]
_OAI_ENV_PATHS = ["/Users/mahmoodmapara/Desktop/code-qa-evals/.env"]


def load_openai_key():
    if _OAI_KEY[0]:
        return _OAI_KEY[0]
    if os.environ.get("OPENAI_API_KEY"):
        _OAI_KEY[0] = os.environ["OPENAI_API_KEY"].strip(); return _OAI_KEY[0]
    paths = list(_OAI_ENV_PATHS)
    here = HERE
    for _ in range(6):
        paths.append(os.path.join(here, ".env")); here = os.path.dirname(here)
    for p in paths:
        if os.path.isfile(p):
            for ln in open(p):
                if ln.strip().startswith("OPENAI_API_KEY="):
                    _OAI_KEY[0] = ln.split("=", 1)[1].strip().strip('"').strip("'"); return _OAI_KEY[0]
    return None


def _openai_call(model, system, user, retries=5):
    key = load_openai_key()
    if not key:
        return {"_err": "no OpenAI key"}
    payload = {"model": model,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}],
               "max_completion_tokens": 32000}
    ml = model.lower()
    if "sol" in ml or ml.startswith(("gpt-5", "o1", "o3", "o4")):
        payload["reasoning_effort"] = "high"   # highest reasoning for the solver variant
    body = json.dumps(payload).encode()
    req = urllib.request.Request(OPENAI_URL, data=body, method="POST", headers={
        "authorization": f"Bearer {key}", "content-type": "application/json"})
    for a in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                d = json.loads(r.read())
            txt = (d.get("choices") or [{}])[0].get("message", {}).get("content", "")
            return {"content": [{"type": "text", "text": txt}]}   # Anthropic-shaped
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and a < retries - 1:
                time.sleep(3 * (a + 1) ** 2); continue
            return {"_err": f"{e.code}:{e.read()[:200].decode(errors='replace')}"}
        except Exception as e:
            if a < retries - 1:
                time.sleep(3 * (a + 1)); continue
            return {"_err": str(e)[:200]}


def call(key, model, system, user, retries=5):
    if model.lower().startswith(("gpt", "o1", "o3", "o4")):
        return _openai_call(model, system, user, retries)
    body = json.dumps({"model": model, "max_tokens": 20000, "system": system,
                       "thinking": {"type": "adaptive"}, "output_config": {"effort": "high"},
                       "messages": [{"role": "user", "content": user}]}).encode()
    req = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "x-api-key": key, "anthropic-version": VER, "content-type": "application/json"})
    for a in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503, 529) and a < retries - 1:
                time.sleep(3 * (a + 1) ** 2); continue
            return {"_err": f"{e.code}:{e.read()[:200].decode(errors='replace')}"}
        except Exception as e:
            if a < retries - 1:
                time.sleep(3 * (a + 1)); continue
            return {"_err": str(e)[:200]}


def readfile(p):
    try:
        b = open(p, "rb").read(MAXBYTES + 1)
        if b"\x00" in b[:4096]:
            return None  # binary
        txt = b.decode(errors="replace")
        return txt[:MAXBYTES] + ("\n...[truncated]" if len(b) > MAXBYTES else "")
    except Exception:
        return None


def env_refs(tdir, basenames):
    """environment/ files that reference any sig basename (the writers/readers)."""
    out = []
    envd = os.path.join(tdir, "environment")
    if not os.path.isdir(envd):
        return out
    for base in basenames:
        if not base:
            continue
        try:
            r = subprocess.run(["grep", "-rl", base, envd], capture_output=True, text=True)
        except Exception:
            continue
        for f in r.stdout.splitlines():
            if not f.endswith(".pyc") and f not in out:
                out.append(f)
    return out[:6]


def gather(tdir, sigs):
    basenames = [s.split("/")[-1] for s in sigs if s]
    parts = [f"AGENT-READABLE ANSWER FILE(S) FLAGGED (survive in the built image): {sigs}\n"]
    seen = set()
    files = [os.path.join(tdir, r) for r in CTX_FILES] + env_refs(tdir, basenames)
    for fp in files:
        if fp in seen or not os.path.isfile(fp):
            continue
        seen.add(fp)
        txt = readfile(fp)
        if txt is None:
            continue
        rel = os.path.relpath(fp, tdir)
        parts.append(f"\n===== {rel} =====\n{txt}")
    return "\n".join(parts)


def apply_patch(tdir, patch):
    changed = []
    for f in patch.get("files", []):
        rel = f.get("path", "").lstrip("/")
        if not rel or ".." in rel:
            continue
        dest = os.path.join(tdir, rel)
        if dest.endswith(".py"):
            try:
                ast.parse(f.get("content", ""))
            except Exception as e:
                return None, f"NEW-UNPARSEABLE:{rel}:{e}"
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w") as fh:
            fh.write(f.get("content", ""))
        changed.append(rel)
    for rel in patch.get("delete", []):
        rel = rel.lstrip("/")
        if not rel or ".." in rel:
            continue
        p = os.path.join(tdir, rel)
        if os.path.isfile(p):
            os.remove(p); changed.append("del:" + rel)
    return changed, None


def fix_one(key, model, tdir, task, sigs, feedback=""):
    marker = os.path.join(tdir, ".leakfix.done")
    if os.path.exists(marker):
        return task, "SKIP-DONE", ""
    if not os.path.isdir(os.path.join(tdir, "environment")):
        return task, "NO-TASK", ""
    fb = ""
    if feedback:
        fb = ("\n\nPRIOR ATTEMPT FEEDBACK (a first fix was applied but FAILED Modal "
              "verification — correct it fully this time; the files below already reflect "
              "your prior edits):\n" + feedback + "\n")
    user = ("Remove the answer leak from this task per the rules, or return no-leak if it "
            "isn't really a leak." + fb + "\n\n" + gather(tdir, sigs))
    resp = call(key, model, SYS, user)
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
    if outcome in ("no-leak", "cull"):
        with lock:
            json.dump(patch, open(os.path.join(tdir, "leak_fix_report.json"), "w"), indent=2)
            open(marker, "w").write(outcome)
        return task, outcome.upper(), patch.get("summary", "")[:80]
    with lock:
        changed, err = apply_patch(tdir, patch)
    if err:
        return task, err[:80], ""
    with lock:
        patch["files_changed"] = changed
        json.dump(patch, open(os.path.join(tdir, "leak_fix_report.json"), "w"), indent=2)
        open(marker, "w").write("fix-applied")
    return task, "FIX-APPLIED", f"{changed}"[:100]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks_dir"); ap.add_argument("task_list")
    ap.add_argument("--sigs", required=True, help="json: {task_name: [sig_path,...]}")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--out", default="_local/leak_fix_results.txt")
    ap.add_argument("--feedback", default="", help="json: {task_name: feedback_str} for a 2nd pass")
    a = ap.parse_args()
    key = load_key()
    sigmap = json.load(open(a.sigs))
    fbmap = json.load(open(a.feedback)) if a.feedback and os.path.isfile(a.feedback) else {}
    tasks = [t for t in open(a.task_list).read().split() if t]
    print(f"[leak-fix] {len(tasks)} tasks x {a.model} ({a.workers} workers). Billed to Claude API key.", flush=True)
    counts = {}; done = 0
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with cf.ThreadPoolExecutor(a.workers) as ex:
        futs = {ex.submit(fix_one, key, a.model, os.path.join(a.tasks_dir, "tasks", t), t,
                          sigmap.get(t, []), fbmap.get(t, "")): t for t in tasks}
        for f in cf.as_completed(futs):
            t, kind, detail = f.result(); counts[kind] = counts.get(kind, 0) + 1; done += 1
            with lock:
                open(a.out, "a").write(f"{t}\t{kind}\t{detail}\n")
            if kind not in ("SKIP-DONE",):
                print(f"  [{kind}] {t}  {detail}", flush=True)
            if done % 20 == 0 or done == len(tasks):
                print(f"  [{done}/{len(tasks)}] {counts}", flush=True)
    print("DONE", counts, flush=True)


if __name__ == "__main__":
    main()
