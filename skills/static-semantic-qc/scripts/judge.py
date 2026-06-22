#!/usr/bin/env python3
"""Layer-1 semantic judging via the Anthropic API (programmatic reviewer / adversary).

This runs the Part-2 reviewer and/or Part-3 adversary as direct Anthropic Messages
API calls — one per task — instead of dispatching interactive sub-agents. It exists
so the semantic layer can run **on a Claude API key**, NOT on your Claude.ai account /
Claude Code session: set `ANTHROPIC_API_KEY` (or `ANT_KEY` in `.env`) to an
`sk-ant-...` key and the LLM judging is billed to that key.

For each task it inlines the task files (instruction, tests, solve.sh, Dockerfile,
task.toml) into the prompt — the model cannot browse, so everything it needs is in
the message — applies the committed rubric (`prompts/tb-task-qc-{reviewer,adversary}-v1.md`),
and writes the finding JSON to `qc_out/`:
  - reviewer  -> sem_<task>.json   (the 6 semantic dims + verify-refuted/confirm of static flags)
  - adversary -> adv_<task>.json   (a surviving cheat is a WARN candidate, never an auto-FAIL)

These aggregate through `../../shared/aggregate.py` + `../../shared/gate.py` like every
other layer. Findings are stamped `layer="semantic"`.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...        # or put ANT_KEY=... in .env
    python judge.py <tasks-dir> --out-dir qc_out [--role reviewer|adversary|both]
                    [--model claude-opus-4-8] [--effort high] [--workers 6]
                    [--static-dir qc_out] [--only a,b,c]
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error

from common import discover_tasks, task_paths, read_text, emit

API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-opus-4-8"          # the latest, most capable Claude (skill default)
PRICE = {  # $ per 1M tokens (input, output) — for the cost estimate only
    "claude-opus-4-8": (5.0, 25.0), "claude-opus-4-7": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0), "claude-haiku-4-5": (1.0, 5.0),
}
MAX_FILE_CHARS = 16000   # cap any single file inlined into the prompt
HERE = os.path.dirname(os.path.abspath(__file__))


# ----------------------------------------------------------------- auth -----
def load_key():
    """Anthropic API key from the environment or a nearby .env. NOT your account."""
    for var in ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "ANT_KEY"):
        if os.environ.get(var):
            return os.environ[var].strip()
    here = HERE
    for _ in range(6):
        env = os.path.join(here, ".env")
        if os.path.isfile(env):
            for line in open(env):
                line = line.strip()
                for var in ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "ANT_KEY"):
                    if line.startswith(var + "="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        here = os.path.dirname(here)
    sys.exit("No Claude API key found. Set ANTHROPIC_API_KEY (sk-ant-...) "
             "or put ANT_KEY=... in .env — use a Claude API key, not your account.")


# ----------------------------------------------------- task → prompt --------
def _clip(text):
    return text if len(text) <= MAX_FILE_CHARS else text[:MAX_FILE_CHARS] + "\n...[truncated]..."


def task_context(name, root, static_findings=None):
    p = task_paths(root)
    parts = [f"# Terminal-Bench task: {name}\n"]
    files = [
        ("instruction.md", p["instruction.md"]),
        ("task.toml", p["task.toml"]),
        ("environment/Dockerfile", p["Dockerfile"]),
        ("tests/test.sh", p["test.sh"]),
        ("tests/test_outputs.py", p["test_outputs.py"]),
        ("solution/solve.sh", p["solve.sh"]),
    ]
    # any extra python under tests/ (helpers, .truth verifiers)
    tdir = p["tests"]
    if os.path.isdir(tdir):
        for dp, _, fns in os.walk(tdir):
            for fn in sorted(fns):
                rel = os.path.relpath(os.path.join(dp, fn), root)
                if fn.endswith(".py") and rel not in ("tests/test_outputs.py",):
                    files.append((rel, os.path.join(dp, fn)))
    # list what the Dockerfile build context contains (agent-visible surface)
    envdir = p["environment"]
    if os.path.isdir(envdir):
        listing = sorted(os.path.relpath(os.path.join(dp, fn), root)
                         for dp, _, fns in os.walk(envdir) for fn in fns)
        parts.append("## environment/ contents (agent-visible)\n" +
                     "\n".join(listing[:200]) + "\n")
    for label, path in files:
        body = read_text(path)
        if body:
            parts.append(f"## {label}\n```\n{_clip(body)}\n```\n")
    if static_findings:
        parts.append("## STATIC FINDINGS to verify (refute false positives)\n" +
                     json.dumps(static_findings, indent=2) + "\n")
    return "\n".join(parts)


REVIEWER_OUT = (
    "\nYou are given every file inline above — you cannot browse, so do not ask to. "
    "Apply the rubric. Output ONLY a JSON array (no prose, no markdown fence) of finding "
    'objects: {"task","area":"instructions|tests|solution","severity":"PASS|WARN|FAIL",'
    '"title":"<stable defect title, e.g. brittle-string-match / untested-requirement / '
    'weak-assertion / golden-patch-mismatch / task-realism>","location":"<file:line or \'\'>",'
    '"detail":"<file:line evidence>","fix":"<one line>","layer":"semantic"}. Emit one finding '
    "per dimension you assessed (a PASS finding per clean dimension is expected). For any item "
    "under STATIC FINDINGS you judge a false positive, also emit "
    '{"task","area":<same>,"severity":"PASS","title":"verify-refuted","ref":"<static title>",'
    '"detail":"why it is a false positive","layer":"semantic"}; if you confirm it, use '
    'title "verify-confirm". Presume the task is correct; only FAIL on concrete, cited evidence.'
)
ADVERSARY_OUT = (
    "\nYou are given every file inline above — you cannot browse. Output ONLY a JSON array "
    "(no prose, no fence). If a hack survives ALL of the verifier's defenses: "
    '[{"task","area":"tests","severity":"WARN","title":"semantic-cheat-vector",'
    '"location":"tests/...:line","detail":"exact recipe + which assertion it beats + why each '
    'defense fails","fix":"make the verifier outcome-based / non-gameable","layer":"semantic"}] '
    "— WARN, never FAIL (a surviving cheat is a candidate the behavioral run confirms). "
    'If the verifier resists every attempt: [{"task","area":"tests","severity":"PASS",'
    '"title":"cheat-vector-ok","detail":"which defense stops each hack","layer":"semantic"}]. '
    "Only claim a cheat you are highly confident passes every test; when unsure, treat it as resisted."
)


# ----------------------------------------------------------- api call -------
def call_api(key, model, system, user, effort, max_tokens=16000, retries=4):
    # 16000 is the safe non-streaming ceiling: at effort high, adaptive thinking
    # consumes most of max_tokens, so a smaller budget truncates the JSON output.
    body = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": effort},
        "messages": [{"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "x-api-key": key, "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    })
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            code = e.code
            if code in (429, 500, 503, 529) and attempt < retries - 1:
                time.sleep(2.0 * (attempt + 1) ** 2)
                continue
            raise SystemExit(f"API error {code}: {e.read()[:300].decode(errors='replace')}")
        except Exception:
            if attempt < retries - 1:
                time.sleep(2.0 * (attempt + 1))
                continue
            raise


def extract_findings(resp, name):
    """Pull the JSON array out of the response text; tolerate fences / stray prose."""
    if resp.get("stop_reason") == "refusal":
        return [{"task": name, "area": "tests", "severity": "WARN", "title": "judge-refused",
                 "detail": "the Claude safety classifier declined to judge this task "
                           "(often a security-themed task); judge it manually.", "layer": "semantic"}]
    text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return [{"task": name, "area": "tests", "severity": "WARN", "title": "judge-unparsable",
                 "detail": f"no JSON array in judge output: {text[:200]}", "layer": "semantic"}]
    try:
        arr = json.loads(m.group(0))
    except Exception as e:
        return [{"task": name, "area": "tests", "severity": "WARN", "title": "judge-unparsable",
                 "detail": f"JSON parse failed: {e}", "layer": "semantic"}]
    out = []
    for f in arr:
        if isinstance(f, dict) and f.get("area") and f.get("severity"):
            f["task"] = name   # force the canonical tree name — the model sometimes
            f.setdefault("layer", "semantic")   # rewrites it (drops timestamp suffixes),
            out.append(f)       # which would split a task into phantom SSOT rows
    return out


def usage_of(resp):
    u = resp.get("usage", {})
    return u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0), u.get("output_tokens", 0)


# --------------------------------------------------------------- main -------
def load_static(static_dir):
    """task -> [non-PASS static findings] for the reviewer's FP-verification."""
    by_task = {}
    if not static_dir or not os.path.isdir(static_dir):
        return by_task
    import glob
    for fp in glob.glob(os.path.join(static_dir, "findings_*.json")):
        try:
            for f in json.load(open(fp)):
                if f.get("severity") in ("WARN", "FAIL") and f.get("task"):
                    by_task.setdefault(f["task"], []).append(
                        {k: f.get(k) for k in ("area", "severity", "title", "location", "detail")})
        except Exception:
            continue
    return by_task


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks")
    ap.add_argument("--out-dir", default="qc_out")
    ap.add_argument("--role", choices=["reviewer", "adversary", "both"], default="reviewer")
    ap.add_argument("--model", default=os.environ.get("JUDGE_MODEL", DEFAULT_MODEL))
    ap.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--max-tokens", type=int, default=16000,
                    help="output budget; keep <=16000 for non-streaming (thinking shares it)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--static-dir", default="", help="qc_out with static findings for FP-verification")
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    key = load_key()
    roles = ["reviewer", "adversary"] if args.role == "both" else [args.role]
    prompts = {r: read_text(os.path.join(HERE, "..", "prompts", f"tb-task-qc-{r}-v1.md"))
               for r in roles}
    out_instr = {"reviewer": REVIEWER_OUT, "adversary": ADVERSARY_OUT}
    static = load_static(args.static_dir)

    only = {s for s in args.only.split(",") if s}
    tasks = [(n, r) for n, r in discover_tasks(args.tasks) if not only or n in only]
    if not tasks:
        print("No tasks found.")
        return
    print(f"[judge] {len(tasks)} task(s) x {roles} on {args.model} (effort={args.effort}, "
          f"{args.workers} workers). Billed to the Claude API key, not your account.")

    tot_in = tot_out = 0
    def run_one(role, name, root):
        ctx = task_context(name, root, static.get(name) if role == "reviewer" else None)
        resp = call_api(key, args.model, prompts[role], ctx + out_instr[role],
                        args.effort, max_tokens=args.max_tokens)
        findings = extract_findings(resp, name)
        prefix = "sem" if role == "reviewer" else "adv"
        emit(findings, os.path.join(args.out_dir, f"{prefix}_{name}.json"))
        return role, name, findings, usage_of(resp)

    jobs = [(role, n, r) for role in roles for n, r in tasks]
    done = 0
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(run_one, role, n, r) for role, n, r in jobs]
        for fut in cf.as_completed(futs):
            role, name, findings, (ti, to) = fut.result()
            tot_in += ti; tot_out += to
            done += 1
            worst = "FAIL" if any(f["severity"] == "FAIL" for f in findings) else (
                    "WARN" if any(f["severity"] == "WARN" for f in findings) else "PASS")
            print(f"  [{done}/{len(jobs)}] {role}:{name} -> {worst} ({len(findings)} findings)")

    pin, pout = PRICE.get(args.model, (5.0, 25.0))
    cost = tot_in / 1e6 * pin + tot_out / 1e6 * pout
    print(f"[judge] done. tokens in={tot_in:,} out={tot_out:,} "
          f"≈ ${cost:.2f} on {args.model}. Findings in {args.out_dir}/ "
          f"(aggregate with ../../shared/aggregate.py, then gate).")


if __name__ == "__main__":
    main()
