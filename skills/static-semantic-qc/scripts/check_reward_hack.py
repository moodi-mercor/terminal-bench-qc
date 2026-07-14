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
     - a grading script is COPY'd into the agent image and `tests/` invokes it
       (agent-writable-verifier): the agent overwrites it to force a pass
     - `test.sh` runs under `set -e` and branches on the verifier's exit code to
       write the reward — on failure `set -e` aborts before the reward=0 write

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

from common import (FAIL, WARN, PASS, finding, emit, read_text, discover_tasks,
                    task_paths, load_toml, is_reflection_schema)

EXISTENCE_FUNCS = {"exists", "isfile", "isdir", "is_file", "is_dir", "lexists"}
REWARDISH = re.compile(r"(reward|score|status|verdict|result|pass)\b", re.I)
# live dependency installs in the verifier (Reflection: tests/test.sh must NOT install
# at runtime — bake deps into the verifier image).
RUNTIME_INSTALL = re.compile(
    r"\b(apt-get\s+(?:-y\s+)?install|apt\s+install|pip3?\s+install|uv\s+pip\s+install|"
    r"python3?\s+-m\s+pip\s+install|conda\s+install|npm\s+(?:install|ci)|gem\s+install|"
    r"cargo\s+install|go\s+install)\b", re.I)
CURL_PIPE_SH = re.compile(r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?(?:ba)?sh\b", re.I)
# the Harbor-mandated reward path: /logs/verifier/reward.txt holding 1 or 0.
STD_REWARD_PATH = "/logs/verifier/reward.txt"
REWARD_REDIRECT = re.compile(r">>?\s*([\"']?)(/?\S*reward\S*)\1", re.I)
# a grading script COPY'd into the agent image (verify.py / grader.sh / check.py …).
# NOTE: `mock` is deliberately excluded — a mock/stub/fixture *generator*
# (generate_mocks.py, mock_server.py) builds test data or stands up a fake service;
# it is never the pass signal. Including it produced false positives (e.g.
# sparse-hash-collision's generate_mocks.py).
VERIFIERISH = re.compile(r"(verif|validat|grade|grader|judge|checker|scorer|oracle)"
                         r"\w*\.(py|sh)$", re.I)


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
                out.append(finding(name, "anti_cheat", FAIL, "skipped-scored-test",
                                   detail=f"`{node.name}` is decorated `{attr}` — a scored "
                                          "test that may never run (silently un-verifies a "
                                          "requirement).",
                                   location=loc,
                                   fix="Remove the skip/xfail, or confirm the requirement is "
                                       "covered by another test."))
            if (attr == "parametrize" and isinstance(d, ast.Call) and len(d.args) >= 2
                    and isinstance(d.args[1], (ast.List, ast.Tuple))
                    and len(d.args[1].elts) == 0):
                out.append(finding(name, "anti_cheat", FAIL, "empty-parametrize",
                                   detail=f"`{node.name}` is parametrized over an EMPTY list "
                                          "— pytest collects zero cases, so it always passes.",
                                   location=loc,
                                   fix="Populate the parametrize cases, or remove the test."))
        # trivial body
        if _is_trivial_body(node.body):
            out.append(finding(name, "anti_cheat", FAIL, "vacuous-test",
                               detail=f"`{node.name}` has a trivial body (pass/return/"
                                      "assert True) — passes without exercising the task.",
                               location=loc,
                               fix="Make the test assert real, computed behaviour. "
                                   "Confirm with a no-op run."))
            continue
        # swallowed assertion
        if _swallowed_assertion(node):
            out.append(finding(name, "anti_cheat", FAIL, "swallowed-assertion",
                               detail=f"`{node.name}` wraps an assertion in a bare "
                                      "`except: pass` — the assertion can never fail it.",
                               location=loc,
                               fix="Remove the swallowing except, or assert on the outcome."))
            continue
        # has assertions? if so, check existence-only
        asserts = [s for s in ast.walk(node) if isinstance(s, ast.Assert)]
        if asserts and all(_assert_is_existence_only(a) for a in asserts):
            out.append(finding(name, "anti_cheat", FAIL, "existence-only-check",
                               detail=f"`{node.name}` only checks file existence, not "
                                      "content — an empty/stub file would pass.",
                               location=loc,
                               fix="Assert on the file's content/value, not just that it exists."))
        elif not _calls_assertion(node):
            # no failure-capable construct anywhere
            out.append(finding(name, "anti_cheat", FAIL, "no-assertion-test",
                               detail=f"`{node.name}` contains no assertion/raise/fail — "
                                      "it cannot fail.",
                               location=loc,
                               fix="Add an assertion on the real outcome."))
    # agent-writable reward signal: verifier opens a reward/score/status file
    for m in re.finditer(r"open\(\s*['\"]([^'\"]+)['\"]", src):
        p = m.group(1)
        if REWARDISH.search(os.path.basename(p)):
            out.append(finding(name, "anti_cheat", FAIL, "agent-writable-reward-signal",
                               detail=f"verifier reads `{p}` — if the agent can write that "
                                      "path, the pass signal is gameable.",
                               location=rel,
                               fix="Compute the verdict from real task state, not from a "
                                   "file the agent can write."))
            break
    return out


def _analyze_test_sh(path, name, reflection=False):
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
            out.append(finding(name, "anti_cheat", FAIL, "test-sh-swallows-failure",
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
    # `set -e` + a bare pytest whose exit code is branched on to write the reward:
    # on the failure path `set -e` aborts the script *before* the reward=0 write, so
    # the no-op produces no reward file instead of 0.0 (oracle/no-op grounding breaks).
    has_sete = re.search(r"^\s*set\s+-[a-zA-Z]*e", txt, re.M)
    toggles_off = re.search(r"^\s*set\s+\+e", txt, re.M)
    runs_pytest = re.search(r"(-m\s+pytest|\bpytest\s)", txt)
    branches_on_rc = re.search(r"\$\?|^\s*if\s+[^\n]*pytest", txt, re.M)
    writes_reward = re.search(r"(reward|score)[^\n]*>|>\s*\S*reward", txt, re.I)
    # the pytest RUNNER neutralised by `|| true|:` (not a `pip install pytest || true`)
    neutralised = re.search(r"(?:-m\s+pytest|\bpytest\s+[-/.\w])[^\n]*\|\|", txt)
    if has_sete and not toggles_off and runs_pytest and branches_on_rc \
            and writes_reward and not neutralised:
        out.append(finding(name, "anti_cheat", FAIL, "test-sh-set-e-reward-abort",
                           detail="tests/test.sh runs under `set -e` and branches on the "
                                  "verifier's exit code to write the reward — on a failing "
                                  "run `set -e` aborts before the reward=0 write, so a no-op "
                                  "yields no reward file instead of 0.0 (breaks no-op grounding).",
                           location="tests/test.sh",
                           fix="Wrap the verifier in `set +e` … `set -e`, or capture its exit "
                               "code with `rc=$?` immediately, so the reward is always written."))

    # Harbor/Reflection verifier conventions (no runtime installs; the standard reward
    # path) are delivery-specific — apply only to Reflection-schema tasks so the legacy
    # OTS corpus (where test.sh routinely installs deps) isn't blanketed with WARNs.
    if reflection:
        for i, ln in enumerate(lines, 1):
            s = ln.strip()
            if s.startswith("#"):
                continue
            if RUNTIME_INSTALL.search(ln) or CURL_PIPE_SH.search(ln):
                out.append(finding(name, "anti_cheat", FAIL, "test-runtime-install",
                                   detail=f"tests/test.sh line {i} installs a dependency at "
                                          "verify time — verifier deps must be preinstalled in "
                                          "the image, not pulled from the network during grading.",
                                   location=f"tests/test.sh:{i}",
                                   fix="Bake the verifier's dependencies into the (verifier) "
                                       "image; test.sh must not run apt-get/pip install or curl|sh."))
                break

        for i, ln in enumerate(lines, 1):
            m = REWARD_REDIRECT.search(ln)
            if not m:
                continue
            rpath = m.group(2)
            if "$" in rpath or "{" in rpath:
                continue  # variable-driven path — can't statically resolve
            if rpath and rpath != STD_REWARD_PATH and rpath.startswith("/"):
                out.append(finding(name, "anti_cheat", FAIL, "reward-path-nonstandard",
                                   detail=f"tests/test.sh writes the reward to `{rpath}`, not the "
                                          f"Harbor-standard `{STD_REWARD_PATH}` (must hold 1/0).",
                                   location=f"tests/test.sh:{i}",
                                   fix=f"Write the reward to {STD_REWARD_PATH} (`1` success / `0` fail)."))
                break
    return out


def _copied_scripts(dockerfile_text):
    """Yield dest paths of every COPY/ADD whose src or dest is a .py/.sh script."""
    out = []
    for ln in dockerfile_text.splitlines():
        m = re.match(r"\s*(?:COPY|ADD)\s+(.+)", ln, re.I)
        if not m:
            continue
        args = [a for a in m.group(1).split("#")[0].split() if not a.startswith("--")]
        if len(args) < 2:
            continue
        out.append((args[:-1], args[-1]))  # (srcs, dest)
    return out


def _refs_agent_copy(base, inimg, harness):
    """True if the harness invokes the AGENT-IMAGE copy of the grader — NOT a
    same-named copy that lives under the verify-time mount.

    The old `base in harness` substring test was path-blind: it fired when the
    basename merely appeared inside a `/tests/.truth/verify_plot.py` path (the
    verify-time copy the agent CANNOT write), producing false positives. We fix
    that here: a full in-image-path match is conclusive; otherwise a bare-basename
    reference counts only when at least one occurrence is NOT qualified by a
    verify-time path (`/tests`, `.truth`)."""
    if inimg and inimg in harness:
        return True
    hit = False
    for ln in harness.splitlines():
        if base not in ln:
            continue
        m = re.search(r"(\S*" + re.escape(base) + r")", ln)
        token = m.group(1) if m else base
        if "/tests" in token or ".truth" in token:
            continue  # the verify-time copy, not the agent-image one
        hit = True
    return hit


def _agent_writable_verifier(name, root, p):
    """A grading script COPY'd into the agent image that the verifier then invokes.

    Classic anti-cheat hole: `COPY verify.py /app/verify.py` and tests/ runs
    `python /app/verify.py` and greps SUCCESS — the agent (root, no USER drop)
    overwrites /app/verify.py with one that prints SUCCESS. The grader must live
    under tests/ (verify-time read-only mount), never in agent-writable space.

    Two precision controls keep this from over-firing (validated against the
    cognition delivery's confirmed-vs-refuted split):
      - path-aware invocation (`_refs_agent_copy`): the harness must invoke the
        AGENT-IMAGE copy, not a same-named verify-time copy (ots-tangled FP).
      - directory copies resolve to the verifier FILE's in-image path, so a
        `COPY svc/ /opt/sms-gateway/` whose harness only starts the SERVICE (not
        the verifier-named file inside it) does not fire.
    A verifier with an independent anti-cheat defense (recompute / mutated-rerun /
    source-grep) is separately down-graded FAIL->WARN in aggregate.reconcile —
    overwriting the copied script can't beat a defense the agent can't forge."""
    df = read_text(p["Dockerfile"])
    if not df.strip():
        return []
    harness = read_text(p["test.sh"]) + "\n" + read_text(p["test_outputs.py"])
    solve = read_text(p["solve.sh"])
    for srcs, dest in _copied_scripts(df):
        # the grading script(s) among this COPY — a verifier-named .py/.sh
        vfiles = [c for c in (srcs + [dest])
                  if VERIFIERISH.search(os.path.basename(c.rstrip("/")))]
        if not vfiles:
            continue
        # a directory destination (`COPY a b /dir/` or multiple srcs): the grader
        # file lands at dest/<basename(src)>, not at `dest` itself.
        is_dir = dest.endswith("/") or len(srcs) > 1
        for vf in vfiles:
            b = os.path.basename(vf.rstrip("/"))
            inimg = (dest.rstrip("/") + "/" + b) if is_dir else dest
            # grader copied UNDER tests/ (verify-time mount) is the correct shape;
            # the oracle (re)generating it at solve time makes it a real artifact.
            if "/tests" in inimg or ".truth" in inimg or b in solve:
                continue
            if _refs_agent_copy(b, inimg, harness):
                return [finding(name, "anti_cheat", FAIL, "agent-writable-verifier",
                                detail=f"Dockerfile copies `{inimg}` into the agent image and "
                                       f"tests/ invokes `{b}` to grade — the agent can "
                                       "overwrite it to force a pass.",
                                location="environment/Dockerfile",
                                fix="Move the grading script into tests/ (verify-time mount); "
                                    "never invoke an agent-writable in-image script as the verifier.")]
    return []


def _reward_pre_created(name, root, p):
    """A reward file must be written by the verifier at run time — never shipped in
    the environment image (else the task starts already 'passed')."""
    out = []
    env = p["environment"]
    if os.path.isdir(env):
        for dirpath, _dirs, files in os.walk(env):
            for fn in files:
                if fn.lower() in ("reward.txt", "reward"):
                    rel = os.path.relpath(os.path.join(dirpath, fn), root)
                    out.append(finding(name, "anti_cheat", FAIL, "reward-pre-created",
                                       detail=f"`{rel}` ships a reward file in the environment "
                                              "image — the reward must be generated by the "
                                              "verifier at run time, not pre-created.",
                                       location=rel,
                                       fix="Remove the reward file from environment/; let "
                                           "tests/test.sh write it after grading."))
                    return out
    df = read_text(p["Dockerfile"])
    if re.search(r"reward\.txt", df, re.I) and re.search(r"\b(COPY|ADD|RUN)\b", df, re.I):
        if re.search(r"(COPY|ADD)\s+[^\n]*reward\.txt|>\s*\S*reward\.txt", df, re.I):
            out.append(finding(name, "anti_cheat", FAIL, "reward-pre-created",
                               detail="environment/Dockerfile writes/copies a reward.txt into "
                                      "the image — the reward must not exist before verification.",
                               location="environment/Dockerfile",
                               fix="Do not bake a reward file; the verifier writes it at run time."))
    return out


def _verifier_helper_baked(name, root, p, agent_writable_fired):
    """A verifier/grader-named helper baked under environment/ (into the agent image)
    AND referenced by the grading harness — a build-isolation smell: verifier logic
    belongs under tests/ (verify-time mount), not in agent-writable space. WARN-level;
    the hard `agent-writable-verifier` FAIL supersedes it, so skip when that fired."""
    if agent_writable_fired:
        return []
    env = p["environment"]
    if not os.path.isdir(env):
        return []
    harness = read_text(p["test.sh"]) + "\n" + read_text(p["test_outputs.py"])
    solve = read_text(p["solve.sh"])
    hits = []
    for dirpath, _dirs, files in os.walk(env):
        for fn in files:
            if VERIFIERISH.search(fn) and fn in harness and fn not in solve:
                hits.append(os.path.relpath(os.path.join(dirpath, fn), root))
    hits = sorted(set(hits))
    if not hits:
        return []
    return [finding(name, "anti_cheat", FAIL, "verifier-helper-in-environment",
                    detail=f"Verifier/grader-named helper(s) {hits} are baked under "
                           "environment/ (into the agent image) and referenced by the grading "
                           "harness — verifier logic should live under tests/ (verify-time "
                           "mount), not in agent-writable space.",
                    location=hits[0],
                    fix="Move the grader/helper into tests/; keep verifier logic out of the agent image.")]


_USER_DROP = re.compile(r"^\s*USER\s+(?!root\b)\S", re.M | re.I)


def _conftest_plant_vulnerable(name, root, p):
    """The verifier runs pytest such that the agent's /app/conftest.py is auto-discovered,
    the reward is gated on pytest's exit code, the agent is root, and there is no
    `--noconftest` guard. A root agent plants /app/conftest.py with a skip-all
    `pytest_collection_modifyitems` hook -> all tests skipped -> pytest exits 0 -> reward 1,
    defeating the verifier without doing the work. Near-universal on the OTS corpus; the fix
    is a one-line `pytest --noconftest` (safe: these tasks ship no legit conftest)."""
    ts = read_text(p["test.sh"])
    if "pytest" not in ts:
        return []
    discovers = (re.search(r"pytest\b[^\n]*\s/app/\S", ts)
                 or re.search(r"cd\s+/app\b[^\n]*\n[^\n]*pytest", ts, re.S)
                 or re.search(r"cp\s+[^\n]*\s/app/\S*test_outputs\.py", ts))
    rc_gated = re.search(r"reward", ts, re.I) and re.search(
        r"\$\?|rc=|test_status|-eq\s*0|returncode|exit_code", ts)
    guarded = re.search(r"--noconftest|--confcutdir", ts)
    root_agent = not _USER_DROP.search(read_text(p["Dockerfile"]))
    if discovers and rc_gated and not guarded and root_agent:
        return [finding(name, "anti_cheat", FAIL, "conftest-plant-vulnerable",
                        detail="verifier runs pytest with /app on the conftest-discovery path, "
                               "gates the reward on pytest's exit code, and the agent is root with "
                               "no --noconftest guard — a planted /app/conftest.py (skip-all "
                               "collection hook) makes pytest exit 0 and forces reward=1 without "
                               "doing the work. Mechanism confirmed; ~76% of OTS pytest tasks.",
                        location="tests/test.sh",
                        fix="Run the verifier as `pytest --noconftest ...` (safe — these tasks ship "
                            "no legit conftest), or run pytest from a non-agent-writable dir / set "
                            "--confcutdir, so a planted /app/conftest.py is not discovered.")]
    return []


def check_task(name, root):
    out = []
    p = task_paths(root)
    out += _conftest_plant_vulnerable(name, root, p)
    reflection = is_reflection_schema(load_toml(p["task.toml"])) if os.path.isfile(p["task.toml"]) else False
    if os.path.isfile(p["test.sh"]):
        out += _analyze_test_sh(p["test.sh"], name, reflection)
    aw = _agent_writable_verifier(name, root, p)
    out += aw
    out += _verifier_helper_baked(name, root, p, bool(aw))
    out += _reward_pre_created(name, root, p)
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
