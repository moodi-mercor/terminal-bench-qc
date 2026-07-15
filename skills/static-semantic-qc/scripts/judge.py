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

# The six dimensions the reviewer MUST cover, and the SSOT area each rolls into.
# Kept in sync with shared/common.py QC_DIMENSIONS (the aggregator's copy). A missing
# dimension — or one asserted with no evidence — is injected below as a FAIL so the
# skip surfaces in the SSOT instead of passing silently.
REVIEWER_DIMS = ["alignment", "coverage", "hygiene", "golden-patch", "realism", "constraints"]
DIM_AREA = {"alignment": "instructions", "coverage": "tests", "hygiene": "instructions",
            "golden-patch": "solution", "realism": "instructions", "constraints": "tests"}
MANDATORY_CHECKS = {
    "Q1": ("coverage", "tests"),
    "Q2": ("golden-patch", "solution"),
    "Q3": ("alignment", "instructions"),
    "Q4": ("coverage", "tests"),
}


def coverage_gaps(findings):
    """[(dim, reason)] for reviewer dimensions skipped or asserted without evidence."""
    covered, no_evidence = set(), set()
    for f in findings:
        dim = f.get("dimension")
        if dim not in REVIEWER_DIMS:
            continue
        if (f.get("detail") or "").strip() or (f.get("location") or "").strip():
            covered.add(dim)
        else:
            no_evidence.add(dim)
    return [(d, "asserted-without-evidence" if d in no_evidence else "not-assessed")
            for d in REVIEWER_DIMS if d not in covered]


def mandatory_check_gaps(findings):
    """Mandatory Q1-Q4 checks not explicitly evidenced in reviewer details."""
    assessed = set()
    for f in findings:
        detail = f.get("detail") or ""
        assessed.update(re.findall(r"\b(Q[1-4])\s*:", detail, flags=re.IGNORECASE))
    assessed = {q.upper() for q in assessed}
    return [q for q in MANDATORY_CHECKS if q not in assessed]


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
    'objects: {"task","dimension":"alignment|coverage|hygiene|golden-patch|realism|constraints",'
    '"area":"instructions|tests|solution","severity":"PASS|WARN|FAIL",'
    '"title":"<stable defect title, e.g. brittle-string-match / untested-requirement / '
    'weak-assertion / golden-patch-mismatch / task-realism>","location":"<file:line or \'\'>",'
    '"detail":"<file:line evidence>","fix":"<one line>","layer":"semantic"}. COVERAGE CONTRACT: '
    "emit EXACTLY ONE finding for EACH of the six dimensions (alignment, coverage, hygiene, "
    "golden-patch, realism, constraints) — a PASS finding per clean dimension is REQUIRED, not "
    "optional; a missing dimension fails the task's QC as incomplete. EVERY finding — including "
    "PASS — MUST have a non-empty `detail` citing the file:line you actually inspected (an empty "
    "PASS is rejected like a skip). You may add extra findings beyond the six, never fewer. For "
    "MANDATORY CHECK CONTRACT: answer all four mandatory checks in the relevant finding "
    "details and label them exactly `Q1:`, `Q2:`, `Q3:`, and `Q4:`. Q1 covers concrete "
    "false-accept submissions; Q2 covers oracle behavior plus written-contract correctness; "
    "Q3 covers instruction-to-assertion discoverability; Q4 covers protected ground truth and "
    "agent influence over grading. Omitting any label fails QC as incomplete. For any item "
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
_OAI = None
def _oai():
    """OpenAI client (for gpt-* judge models). Key from OPENAI_API_KEY or code-qa-evals/.env."""
    global _OAI
    if _OAI is None:
        import openai
        k = os.environ.get("OPENAI_API_KEY")
        if not k:
            for cand in ("/Users/mahmoodmapara/Desktop/code-qa-evals/.env",
                         os.path.expanduser("~/.env")):
                if os.path.isfile(cand):
                    for ln in open(cand):
                        if ln.startswith("OPENAI_API_KEY="):
                            k = ln.split("=", 1)[1].strip().strip('"').strip("'"); break
                if k: break
        _OAI = openai.OpenAI(api_key=k)
    return _OAI


def _call_openai(model, system, user, effort, max_tokens, retries=4):
    """gpt-* dispatch; returns an Anthropic-shaped dict so extract_findings/usage_of work."""
    for attempt in range(retries):
        try:
            r = _oai().chat.completions.create(
                model=model, reasoning_effort=(effort if effort in ("low","medium","high") else "high"),
                max_completion_tokens=max_tokens,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
            u = r.usage
            return {"content": [{"type": "text", "text": r.choices[0].message.content or ""}],
                    "usage": {"input_tokens": getattr(u, "prompt_tokens", 0),
                              "output_tokens": getattr(u, "completion_tokens", 0)},
                    "stop_reason": r.choices[0].finish_reason}
        except Exception:
            if attempt < retries - 1:
                time.sleep(2.0 * (attempt + 1)); continue
            raise


def call_api(key, model, system, user, effort, max_tokens=16000, retries=4):
    if model.startswith("gpt"):
        return _call_openai(model, system, user, effort, max_tokens, retries)
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
    ap.add_argument("--prompt-version", default="v1",
                    help="reviewer/adversary prompt version (v1=raw-corpus adversarial; "
                         "v2=remediated-delivery, objective gates trusted, PASS-default)")
    ap.add_argument("--static-dir", default="", help="qc_out with static findings for FP-verification")
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    key = load_key()
    roles = ["reviewer", "adversary"] if args.role == "both" else [args.role]
    prompts = {r: read_text(os.path.join(HERE, "..", "prompts", f"tb-task-qc-{r}-{args.prompt_version}.md"))
               for r in roles}
    out_instr = {"reviewer": REVIEWER_OUT, "adversary": ADVERSARY_OUT}
    static = load_static(args.static_dir)

    if args.only.startswith("@"):
        only = {ln.strip() for ln in open(args.only[1:]) if ln.strip() and not ln.startswith("#")}
    else:
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
        if role == "reviewer":
            # Enforce the coverage contract: a dimension the model left out (or asserted
            # with no evidence) becomes a visible FAIL rather than a silent pass — this is
            # what stops the judge from skipping a dimension.
            for dim, reason in coverage_gaps(findings):
                findings.append({
                    "task": name, "dimension": dim, "area": DIM_AREA[dim], "severity": "FAIL",
                    "title": "dimension-not-assessed", "location": "",
                    "detail": f"reviewer produced no evidence-backed finding for dimension "
                              f"'{dim}' ({reason}); QC is incomplete until it is assessed.",
                    "fix": f"re-run the reviewer and cite file:line evidence for '{dim}'.",
                    "layer": "semantic"})
            for check in mandatory_check_gaps(findings):
                dim, area = MANDATORY_CHECKS[check]
                findings.append({
                    "task": name, "dimension": dim, "area": area, "severity": "FAIL",
                    "title": "mandatory-check-not-assessed", "location": "",
                    "detail": f"reviewer did not explicitly provide evidence labeled "
                              f"'{check}:'; all four mandatory checks are required.",
                    "fix": f"re-run the reviewer and include concrete file:line evidence for {check}.",
                    "layer": "semantic"})
        prefix = "sem" if role == "reviewer" else "adv"
        emit(findings, os.path.join(args.out_dir, f"{prefix}_{name}.json"))
        return role, name, findings, usage_of(resp)

    jobs = [(role, n, r) for role in roles for n, r in tasks]
    if os.environ.get("JUDGE_RESUME"):
        def _out(role, n):
            return os.path.join(args.out_dir, f"{'sem' if role == 'reviewer' else 'adv'}_{n}.json")
        before = len(jobs)
        jobs = [(role, n, r) for role, n, r in jobs if not os.path.exists(_out(role, n))]
        print(f"[resume] skipping {before - len(jobs)} already-done; {len(jobs)} left")
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
