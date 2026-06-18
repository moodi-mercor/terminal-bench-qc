#!/usr/bin/env python3
"""Layer 1 — static reward-hack screen (deterministic, read-only).

Catches the two reward-hack flavors that are decidable WITHOUT running the task:

  A. Vacuous tests — tests that pass without the agent doing the work:
     - a test function whose body is trivial (only pass / return / assert True)
     - an assertion swallowed by a bare `except: pass` (can never fail)
     - a test whose only assertion is a bare file-existence check
     - `test.sh` swallowing the verifier's failure (`... || true`)
  B. Verifier trusts an agent-writable signal:
     - `test.sh` writes a positive reward unconditionally (not gated on the
       test exit code)
     - the verifier reads a reward/score/status file as the pass signal

These are CANDIDATES, not proofs — a real no-op run confirms them (delivery
gate). Heuristics are deliberately conservative (high precision) and mostly WARN;
only clearly-gameable patterns are FAIL. Uses `ast` for the Python analysis.

Usage:
    python check_reward_hack.py <tasks-dir> [--out findings_reward_hack.json]

Emits findings with area="anti_cheat".
"""
import argparse
import ast
import glob
import os
import re

from common import FAIL, WARN, PASS, finding, emit, read_text, discover_tasks, task_paths

EXISTENCE_FUNCS = {"exists", "isfile", "isdir", "is_file", "is_dir", "lexists"}
REWARDISH = re.compile(r"(reward|score|status|verdict|result|pass)\b", re.I)


def _calls_assertion(node):
    """True if the node-subtree contains any failure-capable construct."""
    for n in ast.walk(node):
        if isinstance(n, (ast.Assert, ast.Raise)):
            return True
        if isinstance(n, ast.Call):
            f = n.func
            name = getattr(f, "attr", getattr(f, "id", ""))
            if name in ("fail", "skip", "xfail") or name.startswith("assert"):
                return True
        if isinstance(n, ast.With):  # with pytest.raises(...)
            for item in n.items:
                src = ast.dump(item.context_expr)
                if "raises" in src:
                    return True
    return False


def _is_trivial_body(body):
    """Body is only docstring / pass / return / `assert True`-style."""
    real = []
    for stmt in body:
        if isinstance(stmt, ast.Expr) and isinstance(getattr(stmt, "value", None),
                                                      (ast.Constant, ast.Str)):
            continue  # docstring / bare literal
        real.append(stmt)
    if not real:
        return True
    for stmt in real:
        if isinstance(stmt, ast.Pass):
            continue
        if isinstance(stmt, ast.Return):
            continue
        if isinstance(stmt, ast.Assert):
            v = stmt.test
            if isinstance(v, ast.Constant) and bool(v.value):
                continue  # assert True / assert 1
        return False
    return True


def _existence_call(node):
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    name = getattr(f, "attr", getattr(f, "id", ""))
    return name in EXISTENCE_FUNCS


def _assert_is_existence_only(stmt):
    """An Assert whose test is purely a file-existence check."""
    t = stmt.test
    # assert os.path.exists(x)   /   assert Path(x).exists()
    if _existence_call(t):
        return True
    # assert os.path.exists(x) and ... (still existence-rooted) -> be strict: only bare
    return False


def _swallowed_assertion(func):
    """A try whose except body is just `pass` while the try contains an assert."""
    for n in ast.walk(func):
        if isinstance(n, ast.Try):
            try_has_assert = any(isinstance(x, ast.Assert) for x in ast.walk(n)
                                 if x not in n.handlers)
            for h in n.handlers:
                body = [s for s in h.body if not isinstance(s, ast.Pass)]
                if try_has_assert and not body:
                    return True
    return False


def _analyze_py(path, name, rel):
    out = []
    src = read_text(path)
    if not src.strip():
        return out
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    # verifier imports the reference solution — it can assert against the oracle's
    # own output instead of the agent's, or just leak the answer into the test.
    for n in ast.walk(tree):
        mod = ""
        if isinstance(n, ast.ImportFrom):
            mod = (n.module or "").split(".")[0]
        elif isinstance(n, ast.Import):
            mod = next((a.name.split(".")[0] for a in n.names
                        if a.name.split(".")[0] in ("solution", "solve")), "")
        if mod in ("solution", "solve"):
            out.append(finding(name, "anti_cheat", FAIL, "test-imports-solution",
                               detail=f"{rel} imports `{mod}` — the verifier pulls in the "
                                      "reference solution; it can grade against the oracle's "
                                      "own output rather than the agent's work.",
                               location=f"{rel}:{n.lineno}",
                               fix="Remove the import; the verifier must check the agent's "
                                   "output independently of solution/."))
            break
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test"):
            continue
        loc = f"{rel}:{node.lineno}"
        # decorators: a scored test that is skipped/xfail'd, or parametrized over
        # an empty list, never actually runs.
        for d in node.decorator_list:
            dn = d.func if isinstance(d, ast.Call) else d
            attr = getattr(dn, "attr", getattr(dn, "id", "")) or ""
            if attr in ("skip", "skipif", "xfail"):
                out.append(finding(name, "anti_cheat", WARN, "skipped-scored-test",
                                   detail=f"`{node.name}` is decorated `{attr}` — a scored "
                                          "test that may never run (silently un-verifies a "
                                          "requirement).",
                                   location=loc,
                                   fix="Remove the skip/xfail, or confirm the requirement is "
                                       "covered by another test."))
            if (attr == "parametrize" and isinstance(d, ast.Call) and len(d.args) >= 2
                    and isinstance(d.args[1], (ast.List, ast.Tuple))
                    and len(d.args[1].elts) == 0):
                out.append(finding(name, "anti_cheat", WARN, "empty-parametrize",
                                   detail=f"`{node.name}` is parametrized over an EMPTY list "
                                          "— pytest collects zero cases, so it always passes.",
                                   location=loc,
                                   fix="Populate the parametrize cases, or remove the test."))
        # trivial body
        if _is_trivial_body(node.body):
            out.append(finding(name, "anti_cheat", WARN, "vacuous-test",
                               detail=f"`{node.name}` has a trivial body (pass/return/"
                                      "assert True) — passes without exercising the task.",
                               location=loc,
                               fix="Make the test assert real, computed behaviour. "
                                   "Confirm with a no-op run."))
            continue
        # swallowed assertion
        if _swallowed_assertion(node):
            out.append(finding(name, "anti_cheat", WARN, "swallowed-assertion",
                               detail=f"`{node.name}` wraps an assertion in a bare "
                                      "`except: pass` — the assertion can never fail it.",
                               location=loc,
                               fix="Remove the swallowing except, or assert on the outcome."))
            continue
        # has assertions? if so, check existence-only
        asserts = [s for s in ast.walk(node) if isinstance(s, ast.Assert)]
        if asserts and all(_assert_is_existence_only(a) for a in asserts):
            out.append(finding(name, "anti_cheat", WARN, "existence-only-check",
                               detail=f"`{node.name}` only checks file existence, not "
                                      "content — an empty/stub file would pass.",
                               location=loc,
                               fix="Assert on the file's content/value, not just that it exists."))
        elif not _calls_assertion(node):
            # no failure-capable construct anywhere
            out.append(finding(name, "anti_cheat", WARN, "no-assertion-test",
                               detail=f"`{node.name}` contains no assertion/raise/fail — "
                                      "it cannot fail.",
                               location=loc,
                               fix="Add an assertion on the real outcome."))
    # agent-writable reward signal: verifier opens a reward/score/status file
    for m in re.finditer(r"open\(\s*['\"]([^'\"]+)['\"]", src):
        p = m.group(1)
        if REWARDISH.search(os.path.basename(p)):
            out.append(finding(name, "anti_cheat", WARN, "agent-writable-reward-signal",
                               detail=f"verifier reads `{p}` — if the agent can write that "
                                      "path, the pass signal is gameable.",
                               location=rel,
                               fix="Compute the verdict from real task state, not from a "
                                   "file the agent can write."))
            break
    return out


def _analyze_test_sh(path, name):
    out = []
    txt = read_text(path)
    if not txt.strip():
        return out
    lines = txt.splitlines()
    # `|| true` on a SETUP/install line is fine (best-effort deps); only the
    # actual verifier invocation swallowing its failure is a problem.
    install = re.compile(r"(apt-get|apt\s|dpkg|pip\s*install|pip3\s*install|"
                         r"uv\s+pip|-m\s+pip|conda\s+install|\binstall\b)")
    # Only the actual pytest runner counts. `|| true` on a cp of test_outputs.py,
    # an anti-shortcut pre-run, or a dep install is benign, not a swallowed verifier.
    verifier = re.compile(r"(-m\s+pytest|\bpytest\s+[-/.\w])")
    fileop = re.compile(r"\b(cp|mv|cat|cmp|diff|chmod|mkdir|ln)\b")
    for i, ln in enumerate(lines, 1):
        if "|| true" not in ln or install.search(ln) or fileop.search(ln):
            continue
        if verifier.search(ln):
            out.append(finding(name, "anti_cheat", WARN, "test-sh-swallows-failure",
                               detail=f"tests/test.sh line {i} appends `|| true` to the "
                                      "verifier — its failure won't fail the task.",
                               location=f"tests/test.sh:{i}",
                               fix="Remove `|| true` from the verifier invocation; capture "
                                   "its exit code and gate the reward on it."))
    # unconditional positive reward write
    cond = re.compile(r"(\$\?|-eq\s*0|-ne\s*0|test_status|exit_code|returncode|if\s|\$test)")
    for i, ln in enumerate(lines, 1):
        m = re.search(r"(echo|printf)\s+['\"]?1['\"]?\s*>", ln)
        if m and REWARDISH.search(ln):
            window = "\n".join(lines[max(0, i - 7):i])
            if not cond.search(window):
                out.append(finding(name, "anti_cheat", FAIL, "unconditional-reward",
                                   detail=f"tests/test.sh line {i} writes a passing reward "
                                          "with no preceding success check — always passes.",
                                   location=f"tests/test.sh:{i}",
                                   fix="Gate the reward write on the verifier's exit code."))
    return out


def check_task(name, root):
    out = []
    p = task_paths(root)
    if os.path.isfile(p["test.sh"]):
        out += _analyze_test_sh(p["test.sh"], name)
    tdir = p["tests"]
    if os.path.isdir(tdir):
        # only files pytest collects — NOT helper scripts (verifier.py, conftest.py),
        # which signal pass via print/exit, not assert (would be false positives).
        pyfiles = set()
        for pat in ("test_*.py", "*_test.py"):
            pyfiles.update(glob.glob(os.path.join(tdir, "**", pat), recursive=True))
        for py in sorted(pyfiles):
            rel = os.path.relpath(py, root)
            out += _analyze_py(py, name, rel)
    if not out:
        out.append(finding(name, "anti_cheat", PASS, "reward-hack-static-clean"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks")
    ap.add_argument("--out", default="findings_reward_hack.json")
    args = ap.parse_args()
    findings = []
    tasks = discover_tasks(args.tasks)
    for name, root in tasks:
        findings.extend(check_task(name, root))
    n = emit(findings, args.out)
    fails = sum(1 for f in findings if f["severity"] == FAIL)
    warns = sum(1 for f in findings if f["severity"] == WARN)
    print(f"[reward_hack] {len(tasks)} tasks, {n} findings, {fails} FAIL, {warns} WARN -> {args.out}")


if __name__ == "__main__":
    main()
