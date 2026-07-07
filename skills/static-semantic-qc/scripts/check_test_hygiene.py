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
`fragmented-test-helpers` (WARN — a structuring defect, not a correctness FAIL).

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

from common import WARN, PASS, finding, emit, read_text, discover_tasks, task_paths

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


def _subprocess_string_literals(src):
    """Every string literal passed to a subprocess runner helper (best-effort AST)."""
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
                        lits.append(s.value)
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


def check_task(name, root):
    p = task_paths(root)
    src = read_text(p["test_outputs.py"])
    if not src:
        return [finding(name, "tests", PASS, "test-hygiene-unknown",
                        detail="no tests/test_outputs.py to inspect.", location="tests/")]

    findings = []
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
            name, "tests", WARN, "base64-wrapped-command",
            detail=f"{len(b64_cmds)} base64-encoded command(s) fed to a shell runner — "
                   "opaque and unnecessary; decode and do the work natively in Python.",
            location="tests/test_outputs.py",
            fix="Remove the base64 wrapping; inline the logic as native Python "
                "(or a plain subprocess call to the agent's deliverable if that's what it runs)."))

    runner_lits = _subprocess_string_literals(src)
    haystacks = runner_lits + b64_cmds
    # 1a. python-in-subprocess — line-aware so explicitly-justified shell-outs
    # (a sanctioned fresh-interpreter import, or a comment marking it a deliverable /
    # process-level call) are not counted; those are the documented escape hatch.
    py_lines = [ln for ln in src.splitlines()
                if (PY_C.search(ln) or PY_HEREDOC.search(ln)) and not JUSTIFIED.search(ln)]
    if py_lines or any(PY_C.search(s) for s in b64_cmds):
        findings.append(finding(
            name, "tests", WARN, "shell-wrapped-python",
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
            name, "tests", WARN, "bash-op-doable-natively",
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
            name, "tests", WARN, "fragmented-test-helpers",
            detail=f"test logic split across {len(flagged)} helper file(s) under tests/: "
                   f"{', '.join(sorted(flagged)[:6])}. Keep checks in test_outputs.py so "
                   "verification is self-contained on both sides. (tests/.truth/ is exempt.)",
            location="tests/",
            fix="Fold each helper's logic into tests/test_outputs.py and delete the file; "
                "update tests/test.sh if it referenced it. Import a tests/.truth/ oracle "
                "in-process rather than duplicating it."))

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
    n = sum(1 for f in findings if f["severity"] == WARN)
    print(f"{len(findings)} findings, {n} WARN -> {a.out}")


if __name__ == "__main__":
    main()
