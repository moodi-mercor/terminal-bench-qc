#!/usr/bin/env python3
"""Layer 1 — test-structuring hygiene detector (deterministic, read-only).

From client feedback (Reflection): a verifier should keep all its checks in a
single native-Python `tests/test_outputs.py`, not shell out to run Python or
fragment logic across helper files. Two anti-patterns to flag:

  1. Unnecessary bash: `test_outputs.py` uses a `_run` / `_exec_check` /
     `subprocess` helper to invoke `python3 -c "..."`, a python heredoc, or a
     base64-encoded command — when the operation (load a JSON, hash a file, read
     a path, compare bytes) can be done natively in Python. The canonical smell is
     Python running a command that again runs Python to load a JSON file:

         _cmd = 'python3 -c "import json; print(json.load(open(\'/app/x.json\'))[k])"'
         result = _run(_cmd)

     Also flagged: shell builtins run via subprocess that have a native equivalent
     (`cat`, `test -f`, `diff`, `cmp`, `sha256sum`, `md5sum`, `grep`, `wc`, `head`,
     `tail`, `ls`, `stat`) → `open()`, `os.path`, `hashlib`, `re`, slicing.

  2. Fragmented tests: test logic split across extra `tests/*.py` helper files that
     `test_outputs.py` imports or invokes, instead of being folded into
     `test_outputs.py`. (`tests/.truth/` oracle fixtures are verifier-only and
     exempt — they are not test fragments.)

Legitimate subprocess use is NOT flagged: invoking the agent's own deliverable
(its CLI, `python3 -m <agent_pkg>`, a compiled binary), service clients
(psql/redis-cli), or genuine process-level behavior (a PTY via `script`,
backgrounded daemons). The detector only fires on `python -c`/python-script/
base64 wrappers and on bash builtins with a clean native equivalent.

Emits (area="tests"): `native-tests` (PASS) when clean, else one or more of
`shell-wrapped-python`, `base64-wrapped-command`, `bash-op-doable-natively`,
`fragmented-test-helpers`. All FAIL — Reflection requires self-contained native-Python
tests with no shell-outs, no encoded content, and no delegation to other verifier files.

Usage:
    python check_test_hygiene.py <tasks-dir> [--out findings_test_hygiene.json]
"""
import argparse
import ast
import os
import re
import sys

sys.path.insert(0, os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "shared")))

from common import WARN, FAIL, PASS, finding, emit, read_text, discover_tasks, task_paths

# a subprocess string that wraps Python to do work doable natively
PY_C = re.compile(r"python3?\s+-c\b")
PY_HEREDOC = re.compile(r"python3?\s+-\s*<<|<<\s*'?PY'?")
B64 = re.compile(r"b64decode\s*\(")
# bash builtins with a clean native-Python equivalent (leading token of a command).
# High-precision set only — file read/compare/hash/search. `sort`/`cut`/`ls`/`uniq`
# are excluded: they commonly appear inside legitimate deliverable pipelines.
NATIVE_BUILTINS = re.compile(
    r"""(?:^|["'`]|\|\s*|&&\s*|;\s*|\(\s*)"""      # command-start boundary
    r"""(cat|test\s+-[a-z]|diff|cmp|sha256sum|md5sum|sha1sum|grep|wc|head|tail)\b"""
)
# a subprocess-runner line explicitly justified as a sanctioned shell-out
JUSTIFIED = re.compile(r"fresh-interpreter import required|deliverable|process-level|PTY|pty\b")
# the in-file subprocess runner helpers the feedback calls out
RUNNER = re.compile(r"\b(_run|_exec_check|_exec|subprocess\.(run|check_output|Popen|call))\s*\(")


def _active_code_py(src):
    """Python source with # comments and module/def/class docstrings removed, so a
    `.truth/...` path that appears only in a comment or a docstring (refactored files
    keep explanatory notes about the former .truth helper) is not mis-read as a live
    delegation/dangling reference. Real string args to subprocess() are preserved."""
    import io, tokenize, ast
    try:
        toks = [t for t in tokenize.generate_tokens(io.StringIO(src).readline)
                if t.type != tokenize.COMMENT]
        src2 = tokenize.untokenize((t.type, t.string) for t in toks)
    except Exception:
        src2 = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    try:
        tree = ast.parse(src2)
        lines = src2.split("\n")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                b = getattr(node, "body", None)
                if (b and isinstance(b[0], ast.Expr)
                        and isinstance(getattr(b[0], "value", None), ast.Constant)
                        and isinstance(b[0].value.value, str)):
                    ds = b[0].value
                    for ln in range(ds.lineno - 1, getattr(ds, "end_lineno", ds.lineno)):
                        if 0 <= ln < len(lines):
                            lines[ln] = ""
        src2 = "\n".join(lines)
    except Exception:
        pass
    return src2


def _strip_sh_comments(ts):
    """Shell source with full-line # comments removed."""
    return "\n".join(l for l in ts.splitlines() if not l.lstrip().startswith("#"))


def _subprocess_string_literals(src):
    """(value, lineno) for every string literal passed to a subprocess runner helper.

    AST-based on purpose: a line-scan false-positives on comments/docstrings that
    merely *mention* `python3 -c` (refactored files keep explanatory comments about
    what the original shell command did). Only actual command strings count.
    """
    lits = []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return lits
    runner_names = {"_run", "_exec_check", "_exec"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            is_runner = (isinstance(f, ast.Name) and f.id in runner_names) or (
                isinstance(f, ast.Attribute) and f.attr in {"run", "check_output", "Popen", "call"})
            if not is_runner:
                continue
            for a in node.args:
                for s in ast.walk(a):
                    if isinstance(s, ast.Constant) and isinstance(s.value, str):
                        # span the whole call so a trailing justification comment
                        # on any of its lines is seen
                        lits.append((s.value, (node.lineno, node.end_lineno or node.lineno)))
    return lits


def _helper_test_files(tests_dir):
    """Non-.truth *.py under tests/ other than test_outputs.py — candidate fragments."""
    out = []
    if not os.path.isdir(tests_dir):
        return out
    for dirpath, dirnames, filenames in os.walk(tests_dir):
        if os.path.basename(dirpath) == ".truth" or "/.truth" in dirpath:
            dirnames[:] = [d for d in dirnames if d != ".truth"]
            continue
        dirnames[:] = [d for d in dirnames if d != ".truth"]
        for fn in filenames:
            if fn.endswith(".py") and fn != "test_outputs.py":
                out.append(os.path.join(dirpath, fn))
    return out


# base64/encoded ground-truth assignment (client flagged unused base64 `_TRUTH` blobs)
ENC_TRUTH = re.compile(
    r"(?i)\b\w*(?:truth|expected|golden|answer|reference|digest|checksum|payload)\w*"
    r"\s*=\s*[rbf]?['\"]([A-Za-z0-9+/]{40,}={0,2})['\"]")


def _encoded_ground_truth(name, root, p):
    """Encoded ground-truth blobs anywhere under tests/ or solution/, not just in
    test_outputs.py. Walks the whole tests/ tree (including .truth) and solution/, so it
    fires even when tests/test_outputs.py is absent (the early-return used to skip it)."""
    hits, hit_files = 0, []
    for base in (p["tests"], p["solution"]):
        if not os.path.isdir(base):
            continue
        for dp, _, fns in os.walk(base):
            for fn in fns:
                c = len(ENC_TRUTH.findall(read_text(os.path.join(dp, fn)) or ""))
                if c:
                    hits += c
                    hit_files.append(os.path.relpath(os.path.join(dp, fn), root))
    if not hits:
        return []
    return [finding(
        name, "tests", WARN, "encoded-ground-truth",
        detail=f"{hits} base64/encoded ground-truth blob(s) in {sorted(hit_files)[:5]}. "
               "Encoded truth is opaque and un-reviewable; the client requires readable, "
               "diffable ground truth (a plain file checked against), not base64 blobs.",
        location=sorted(hit_files)[0],
        fix="Store ground truth as a readable file the verifier reads and compares; "
            "remove base64/encoded blobs from tests and solution.")]


def check_task(name, root):
    p = task_paths(root)
    # encoded ground truth: scan tests/ + solution/ up front, independent of test_outputs.py
    findings = _encoded_ground_truth(name, root, p)
    src = read_text(p["test_outputs.py"])
    if not src:
        return findings or [finding(name, "tests", PASS, "test-hygiene-unknown",
                                    detail="no tests/test_outputs.py to inspect.",
                                    location="tests/")]
    # decode base64 blobs so a wrapped `python -c` / builtin inside them is seen too,
    # but ignore the disclosed `_TRUTH` description blob (not a command).
    b64_cmds = []
    for m in re.finditer(r"b64decode\(\s*((?:['\"][A-Za-z0-9+/=]*['\"]\s*)+)\)", src):
        line = src[:m.start()].count("\n")
        ctx = src.splitlines()[line] if line < len(src.splitlines()) else ""
        if "_TRUTH" in ctx:
            continue
        joined = "".join(re.findall(r"['\"]([A-Za-z0-9+/=]*)['\"]", m.group(1)))
        try:
            import base64
            b64_cmds.append(base64.b64decode(joined).decode("utf-8", "replace"))
        except Exception:
            pass
    if b64_cmds:
        findings.append(finding(
            name, "tests", FAIL, "base64-wrapped-command",
            detail=f"{len(b64_cmds)} base64-encoded command(s) fed to a shell runner — "
                   "opaque and unnecessary; decode and do the work natively in Python.",
            location="tests/test_outputs.py",
            fix="Remove the base64 wrapping; inline the logic as native Python "
                "(or a plain subprocess call to the agent's deliverable if that's what it runs)."))

    runner_lits = _subprocess_string_literals(src)
    src_lines = src.splitlines()
    haystacks = [v for v, _ in runner_lits] + b64_cmds
    # 1a. python-in-subprocess — only actual subprocess command STRINGS count
    # (comments/docstrings mentioning `python3 -c` are ignored), and a string whose
    # source line carries an explicit justification marker (sanctioned fresh-
    # interpreter import / deliverable / PTY) is the documented escape hatch.
    def _justified(span):
        a, b = span
        return any(JUSTIFIED.search(src_lines[i - 1])
                   for i in range(a, min(b, len(src_lines)) + 1))
    py_hits = [(v, sp) for v, sp in runner_lits
               if (PY_C.search(v) or PY_HEREDOC.search(v)) and not _justified(sp)]
    if py_hits or any(PY_C.search(s) for s in b64_cmds):
        findings.append(finding(
            name, "tests", FAIL, "shell-wrapped-python",
            detail="test_outputs.py shells out to run Python (`python3 -c` / heredoc) "
                   "for work that can be done natively — e.g. loading JSON, hashing, or "
                   "reading a path. Running Python from Python to load a file is the smell "
                   "the client called out.",
            location="tests/test_outputs.py",
            fix="Inline the Python directly in the test function (json.load(open(...)), "
                "hashlib, os/pathlib). Keep subprocess only for the agent's own deliverable."))
    # 1b. bash builtins doable natively
    builtin_hits = sorted({m.group(1).split()[0] for s in haystacks for m in [NATIVE_BUILTINS.search(s)] if m})
    if builtin_hits:
        findings.append(finding(
            name, "tests", FAIL, "bash-op-doable-natively",
            detail=f"subprocess calls use shell builtins with a native equivalent: "
                   f"{', '.join(builtin_hits)}. cat/test/diff/cmp/sha256sum/grep/wc → "
                   "open()/os.path/hashlib/re/bytes-compare.",
            location="tests/test_outputs.py",
            fix="Replace these shell calls with native Python file/hash/compare operations."))
    # 2. fragmented test helpers
    helpers = _helper_test_files(p["tests"])
    if helpers:
        bases = [os.path.basename(h) for h in helpers]
        # only a real fragment if test_outputs.py references it (import or invoke)
        referenced = [b for b in bases if re.search(rf"(?<![\w/]){re.escape(b)}", src)
                      or re.search(rf"(?<![\w.]){re.escape(os.path.splitext(b)[0])}\b", src)]
        flagged = referenced or bases
        findings.append(finding(
            name, "tests", FAIL, "fragmented-test-helpers",
            detail=f"test logic split across {len(flagged)} helper file(s) under tests/: "
                   f"{', '.join(sorted(flagged)[:6])}. Keep checks in test_outputs.py so "
                   "verification is self-contained on both sides. (tests/.truth/ is exempt.)",
            location="tests/",
            fix="Fold each helper's logic into tests/test_outputs.py and delete the file; "
                "update tests/test.sh if it referenced it. Import a tests/.truth/ oracle "
                "in-process rather than duplicating it."))

    # 3. delegation to / dangling references into tests/.truth/  (deterministic)
    #    client: test_outputs.py must be self-contained — calling a .truth/*.py verifier
    #    is delegation; referencing a .truth path that doesn't exist is a broken verifier.
    ts = read_text(p["test.sh"]) or ""
    # scan only ACTIVE code — a .truth path mentioned in a comment or docstring
    # (refactored, already-inlined files keep such notes) is not a live reference.
    active_src = _active_code_py(src)
    active_ts = _strip_sh_comments(ts)
    combined = active_src + "\n" + active_ts
    truth_dir = os.path.join(p["tests"], ".truth")
    truth_toks = set(re.findall(r'(?:/tests/)?\.truth/[\w./\-]+', combined))
    invokes_truth_py = (
        re.search(r'(subprocess\.\w+|_run|_exec|_exec_check|check_output|Popen|os\.system|run)\s*\([^\n]*\.truth/[\w./\-]+\.py', combined)
        or re.search(r'python3?\s+[^\n|]*\.truth/[\w./\-]+\.py', combined)
        or re.search(r'^\s*(from|import)\s+[^\n]*\btruth\b', active_src, re.M))
    if invokes_truth_py:
        findings.append(finding(
            name, "tests", FAIL, "delegates-to-truth-verifier",
            detail="test_outputs.py / test.sh delegates verification to a tests/.truth/*.py "
                   "verifier (subprocess or import). Reflection requires the test script to "
                   "contain all testing code, not call additional verifier scripts.",
            location="tests/",
            fix="Inline the .truth verifier's checking logic into test_outputs.py. Keep only "
                "read-only ground-truth DATA fixtures under tests/.truth/, never verifier code."))
    dangling = []
    _fileext = re.compile(r'\.(py|json|jsonl|txt|csv|tsv|sh|bin|dat|pkl|npy|npz|toml|ya?ml|md|db|sqlite|gz|tar|zip|so|h5|parquet)$', re.I)
    for tok in truth_toks:
        rel = tok.split(".truth/", 1)[1].rstrip('.,;:)"\'')
        disk = os.path.join(truth_dir, rel) if rel else truth_dir
        if os.path.exists(disk):
            continue
        # a concrete file reference that is missing is dangling; a bare directory
        # reference is dangling only when its parent is also absent (avoids FP on
        # dynamically-built subpaths).
        if _fileext.search(rel or "") or not os.path.exists(os.path.dirname(disk)):
            dangling.append(tok)
    if dangling:
        findings.append(finding(
            name, "tests", FAIL, "dangling-truth-reference",
            detail=f"test references tests/.truth/ path(s) that do not exist: "
                   f"{sorted(set(dangling))[:4]} — the verifier points at fixtures/verifiers "
                   "that were never shipped, so it cannot grade correctly.",
            location="tests/",
            fix="Ship the referenced .truth files, or remove the dead references and inline "
                "the ground truth the verifier needs."))

    # 4. generic DB bootstrap boilerplate (deterministic) — client flagged templated
    #    test.sh that boots >=3 of postgres/mysql/redis/mongo the task never uses.
    DBS = {"postgresql": r"pg_ctlcluster|pg_isready|pg_ctl|postgres",
           "mysql": r"mysqld|mysqladmin|\bmysql\b",
           "redis": r"redis-server|redis-cli",
           "mongodb": r"mongod|mongosh|mongo\b"}
    booted = [db for db, pat in DBS.items() if re.search(pat, ts)]
    if len(booted) >= 3:
        usage = ((read_text(p["solve.sh"]) or "") + "\n" + src + "\n"
                 + (read_text(p["instruction.md"]) or ""))
        unused = [db for db in booted if not re.search(DBS[db], usage)]
        if len(unused) >= 2:
            findings.append(finding(
                name, "tests", FAIL, "generic-bootstrap-blocks",
                detail=f"test.sh boots {len(booted)} databases ({', '.join(booted)}) but "
                       f"{len(unused)} are never used by the task ({', '.join(unused)}) — "
                       "templated bootstrap boilerplate with no cleanup.",
                location="tests/test.sh",
                fix="Delete the bootstrap block(s) for databases the task does not use; keep "
                    "only services the verifier or solution actually needs."))

    # 5. no subjective grading / LLM-as-judge in the verifier (spec: "Verifiable" §68,
    #    "No subjective grading" §170). Flag an LLM API call inside the verifier.
    # Require an actual LLM SDK import OR an API-call token — NOT a bare word/substring.
    # (The old pattern matched the English word "together" and "llm" inside hyphenated
    # identifiers/paths, which are false positives.)
    LLMJUDGE = re.compile(
        r"^\s*(?:import|from)\s+(?:openai|anthropic|litellm|ollama|cohere|replicate|together|"
        r"google\.generativeai|vertexai)\b"                       # a real SDK import
        r"|\b(?:openai|anthropic|litellm|cohere|genai)\.[A-Za-z_]"  # provider.<attr> call
        r"|chat\.completions|messages\.create|ChatCompletion"     # canonical API calls
        r"|api\.openai\.com|api\.anthropic\.com|generativelanguage\.googleapis"
        r"|as[-\s]?a[-\s]?judge|llm[-_\s]?judge|judge[-_\s]?llm",  # explicit judge phrasing
        re.I | re.M)
    if LLMJUDGE.search(src):
        findings.append(finding(
            name, "tests", FAIL, "llm-judge-in-verifier",
            detail="the verifier appears to call an LLM / subjective grader. Reflection requires "
                   "deterministic, programmatic grading — no LLM-as-a-judge or human-preference scoring.",
            location="tests/test_outputs.py",
            fix="Replace the LLM/subjective check with a deterministic programmatic assertion."))

    # 6. deterministic tests — unseeded randomness / wall-clock in the verifier
    #    (spec §154 deterministic tests, §89 no stale/time-dependent data).
    RNG = re.compile(r"\brandom\.(random|randint|choice|shuffle|uniform|sample|randrange)\s*\(|"
                     r"np\.random\.|numpy\.random\.|secrets\.(token|choice|randbelow)|os\.urandom\s*\(")
    seeded = re.search(r"random\.seed\s*\(|np\.random\.seed\s*\(|default_rng\s*\(|manual_seed\s*\(", src)
    # only datetime.now / date.today (computing an expected value from the clock) — NOT
    # time.time()/monotonic(), which are almost always duration/timeout guards (legit).
    walltime = re.compile(r"\b(datetime\.now|datetime\.today|date\.today|datetime\.utcnow)\s*\(")
    if RNG.search(src) and not seeded:
        findings.append(finding(
            name, "tests", FAIL, "unseeded-randomness-in-verifier",
            detail="the verifier uses randomness with no fixed seed — the same correct solution "
                   "can pass or fail across runs. Reflection requires deterministic tests.",
            location="tests/test_outputs.py",
            fix="Seed the RNG (random.seed / np.random.seed / default_rng(seed)) or use fixed inputs."))
    if walltime.search(src):
        findings.append(finding(
            name, "tests", FAIL, "wall-clock-in-verifier",
            detail="the verifier reads current time/date — grading can drift with wall-clock. "
                   "Reflection forbids time-dependent verification.",
            location="tests/test_outputs.py",
            fix="Pin any time/date the check depends on to a fixed value baked into the fixture."))

    if not findings:
        findings.append(finding(name, "tests", PASS, "native-tests",
                                detail="tests are self-contained native Python; no unnecessary "
                                       "shell-outs or fragmented helper files.",
                                location="tests/test_outputs.py"))
    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks")
    ap.add_argument("--out", default="findings_test_hygiene.json")
    a = ap.parse_args()
    findings = []
    for name, root in discover_tasks(a.tasks):
        findings.extend(check_task(name, root))
    emit(findings, a.out)
    nf = sum(1 for f in findings if f["severity"] == FAIL)
    nw = sum(1 for f in findings if f["severity"] == WARN)
    print(f"{len(findings)} findings, {nf} FAIL, {nw} WARN -> {a.out}")


if __name__ == "__main__":
    main()
