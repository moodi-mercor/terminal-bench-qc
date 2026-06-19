#!/usr/bin/env python3
"""Layer 1 — leakage / anti-cheat static triage (deterministic, read-only).

Folds the two reference detectors plus Dockerfile-copy and hint-file scans into
one pass that emits findings. Catches the leak classes behind the Reflection /
GDM escalations ("ground-truth left by setup", "tests/solution copied into
image"):

  1. Dockerfile/setup COPY of `solution/` or `tests/` into the agent image.
  2. Build-time writes/copies into the absolute `/tests/` path (shadowed by the
     verify-time mount => baked into the agent image). Classified
     clean / removed / baked_unread / baked_read (verifier reads it = real leak).
  3. Truth/golden/expected/hash artifacts baked into agent-visible NON-/tests
     paths that the verifier later reads as the expected answer.
  4. Hint-style files (notes/hints/analysis/walkthrough/solution.md) copied into
     the agent image.

IMPORTANT: static flags are candidates, not verdicts. The skill's behavioral /
semantic layers must confirm survival (build + ls) and exploitability before a
fix. Severity here reflects likelihood, not certainty.

Usage:
    python check_leakage.py <tasks-dir> [--out findings_leakage.json]

Emits findings with area="anti_cheat" (and "dockerfile" for copy issues).
"""
import argparse
import base64
import glob
import os
import re

from common import (FAIL, WARN, PASS, finding, emit, read_text,
                    discover_tasks, task_paths)

BUILD_SCRIPTS = ("setup_commands.sh", "setup.sh", "setup_env.sh", "init.sh",
                 "bootstrap.sh", "prestart_setup.sh", "entrypoint.sh",
                 "docker-entrypoint.sh", "build.sh", "generate_data.py",
                 "generate.py", "generator.py", "gen_data.py")

# --- /tests bake patterns (from leak_detect.py) ---
WRITE_TESTS = re.compile(r"""(?:
    COPY\s+[^\n]*?\s(/tests/\S+)            |
    (?:>|>>)\s*(/tests/\S+)                 |
    \b(?:mv|cp)\s+[^\n]*?\s(/tests/\S+)     |
    \b(?:mv|cp)\s+[^\n]*?\s(/tests/?)\s*$   |
    open\(\s*['"](/tests/[^'"]+)['"]\s*,\s*['"][wax] |
    makedirs\(\s*['"](/tests[^'"]*)['"]     |
    cat\s*<<[^\n]*>\s*(/tests/\S+)
)""", re.VERBOSE)
RM_TESTS = re.compile(r"\brm\s+-[rf]+\s+[^\n]*?(/tests(?:/\S*)?)")

# --- non-/tests truth bake (from leak_detect2.py) ---
TRUTHY = re.compile(r"(truth|golden|expected|reference|\.ref\b|_hash|hidden|"
                    r"answer|\.verifier|lineage|oracle|ground|secret|private)", re.I)
# Any absolute path EXCEPT scratch/system dirs (/tmp is verifier scratch). The
# old whitelist (/app|/opt|/data|...) missed real leaks under dirs like /hidden,
# so match broadly and rely on the verifier-read intersection + truthy-name to
# control false positives.
ABS = r"(/(?!tmp/|tmp\b|proc/|sys/|dev/|run/)[A-Za-z0-9_.][^\s'\")]*)"
WRITE_ABS = re.compile(
    r"(?:COPY\s+\S+\s+%s|(?:>|>>)\s*%s|\b(?:cp|mv)\s+\S+\s+%s|"
    r"open\(\s*['\"]%s['\"]\s*,\s*['\"][wax]|makedirs\(\s*['\"]%s|"
    r"cat\s*<<[^\n]*>\s*%s)" % (ABS, ABS, ABS, ABS, ABS, ABS), re.VERBOSE)
RM_ABS = re.compile(r"\brm\s+-[rf]+\s+[^\n]*?%s" % ABS)
OPEN_ABS = re.compile(
    r"(?:open\(\s*['\"]([^'\"]+)['\"]|"
    r"['\"]((?:/(?:app|root|opt|data|var|srv|home|etc|usr|mnt|workspace))[^'\"]+)['\"])")
B64 = re.compile(r"['\"]([A-Za-z0-9+/]{24,}={0,2})['\"]")

# --- Dockerfile copy of solution/tests ---
COPY_SRC = re.compile(r"^\s*(?:COPY|ADD)\s+(?:--\S+\s+)*([^\s]+)\s", re.M)
HINT_NAMES = re.compile(r"(notes|hint|hints|analysis|walkthrough|answer|"
                        r"solution|cheat|spoiler|readme_solution|approach)",
                        re.I)


def _build_parts(root):
    env = os.path.join(root, "environment")
    parts = {"Dockerfile": read_text(os.path.join(env, "Dockerfile"))}
    for n in BUILD_SCRIPTS:
        for h in glob.glob(os.path.join(env, "**", n), recursive=True):
            parts[os.path.relpath(h, root)] = read_text(h)
    # Also scan setup/generator scripts that don't match the fixed names above —
    # real OTS tasks use bespoke names (e.g. `__swegen_setup_commands.sh`,
    # `generate_mutated.py`) that still bake truth at build time. Match by shape.
    for pat in ("*setup*.sh", "*swegen*", "prestart*.sh", "bootstrap*.sh",
                "generate*.py", "gen_*.py", "*mutate*.py", "make_*.py", "seed*.py"):
        for h in glob.glob(os.path.join(env, "**", pat), recursive=True):
            rel = os.path.relpath(h, root)
            if rel not in parts and os.path.isfile(h):
                parts[rel] = read_text(h)
    # Append variable-resolved write paths so build-time bakes via a dir variable
    # (os.path.join(TRUTH_DIR, 'expected_*')) are visible to WRITE_ABS.
    for rel in list(parts):
        exp = _expand_indirect_paths(parts[rel], "write")
        if exp:
            parts[rel] += "\n" + exp
    return parts


def _expand_indirect_paths(text, mode):
    """Resolve simple `VAR = '/abs/path'` assignments and expand
    os.path.join(VAR, 'name'...) / f"{VAR}/name" / VAR + '/name' into concrete
    absolute paths, returned as synthetic open() lines so WRITE_ABS / OPEN_ABS
    catch them. Real OTS leaks (e.g. kiosk-deterministic-attest) bake the answer
    via a dir variable — `open(os.path.join(TRUTH_DIR,'expected_replay.log'),'w')`
    — which literal-path matching misses. `mode` is 'write' for build scripts
    (they create the artifact) or 'read' for the verifier (it reads it)."""
    vars = {}
    for m in re.finditer(r"^[ \t]*(\w+)\s*=\s*['\"](/[^'\"]+)['\"]", text, re.M):
        v = m.group(2)
        if not v.startswith(("/tmp", "/proc", "/sys", "/dev")):
            vars[m.group(1)] = v.rstrip("/")
    if not vars:
        return ""
    resolved = []
    for m in re.finditer(r"os\.path\.join\(\s*(\w+)\s*((?:,\s*['\"][^'\"]+['\"]\s*)+)\)", text):
        if m.group(1) in vars:
            segs = re.findall(r"['\"]([^'\"]+)['\"]", m.group(2))
            resolved.append(vars[m.group(1)] + "/" + "/".join(segs))
    for m in re.finditer(r"f['\"]\{(\w+)\}(/[^'\"{}]+)['\"]", text):
        if m.group(1) in vars:
            resolved.append(vars[m.group(1)] + m.group(2))
    for m in re.finditer(r"(\w+)\s*\+\s*['\"](/[^'\"]+)['\"]", text):
        if m.group(1) in vars:
            resolved.append(vars[m.group(1)] + m.group(2))
    # Indirect expansion is conservative: only surface paths NAMED like an answer
    # key (expected_*/truth/golden/answer/ground-truth). Setup scripts also build
    # the task's INPUT data via os.path.join loops (shards, spool files, reference
    # registries); synthesizing writes for those turned re-derivation inputs into
    # bogus "baked truth" FAILs. The kiosk-class leak we need is specifically the
    # baked expected-output file, which this pattern isolates.
    ANSWER_NAME = re.compile(r"(^|[_./-])(expected|truth|golden|answer|"
                             r"ground[_-]?truth)", re.I)
    resolved = [p for p in resolved if ANSWER_NAME.search(os.path.basename(p))]
    if mode == "write":
        return "\n".join(f"open('{p}', 'w')" for p in resolved)
    return "\n".join(f"open('{p}')" for p in resolved)


def _instruction_text(root):
    return read_text(task_paths(root)["instruction.md"]).lower()


def _all_instruction_referenced(paths, root):
    """True if every flagged path is referenced (by basename or path) in the
    instruction — i.e. it is task INPUT the agent is told about, not hidden truth.
    Encodes the FP rule 'never fix an instruction-promised sample/input'."""
    instr = _instruction_text(root)
    if not instr:
        return False
    for p in paths:
        base = os.path.basename(p).lower()
        stem = os.path.splitext(base)[0]
        if base in instr or p.lower() in instr or (len(stem) > 3 and stem in instr):
            continue
        return False
    return True


# A write is a redirect target (`> p` / `>> p` / `tee p`) or open(p, 'w'/'a'/'x').
# Anything else that names the path is treated as a read. Used to tell a reference
# that PRODUCES the deliverable (writes the answer path) from one that CHEATS
# (reads a baked truth file it never produced).
_REDIR_TAIL = re.compile(r"""(?:>>?|tee(?:\s+-a)?\s+)\s*['"]?\s*$""")


def _solve_produces(text, path):
    """True if the reference WRITES `path` (produces the deliverable) — directly
    (`... > path`, `open(path,'w')`) or through a variable bound to it
    (`OUT = "path"` ... `open(OUT,'w')` / `... > $OUT`). A reference that produces
    the answer file is emitting output, not reading baked truth — so it is not a
    leak, even though the verifier reads the same path.
    """
    base = os.path.basename(path)
    for tok in (path, base):
        for m in re.finditer(re.escape(tok), text):
            tail = text[max(0, m.start() - 48):m.start()]
            after = text[m.end():m.end() + 24]
            if _REDIR_TAIL.search(tail):
                return True
            if (re.search(r"open\(\s*['\"][^'\"]*$", tail)
                    and re.search(r"^['\"]?\s*,\s*['\"][wax]", after)):
                return True
    # variable indirection: VAR bound to the path, then VAR written
    for vm in re.finditer(r"(\w+)\s*=\s*['\"]%s['\"]" % re.escape(path), text):
        var = vm.group(1)
        if re.search(r"open\(\s*%s\s*,\s*['\"][wax]" % re.escape(var), text):
            return True
        if re.search(r"(?:>>?|tee(?:\s+-a)?\s+)\s*[\"']?\$?\{?%s\b" % re.escape(var), text):
            return True
    return False


def _verifier_text(root):
    ts = []
    for rel in ("tests/test_outputs.py", "tests/test.sh"):
        t = read_text(os.path.join(root, rel))
        ts.append(t)
        for m in B64.finditer(t):
            try:
                ts.append(base64.b64decode(m.group(1)).decode("utf-8", "replace"))
            except Exception:
                pass
    joined = "\n".join(ts)
    # Resolve variable-built read paths (verifier opens os.path.join(TRUTH,'expected_*'))
    exp = _expand_indirect_paths(joined, "read")
    return joined + ("\n" + exp if exp else "")


def _dockerfile_copies(root, name):
    """Flag COPY/ADD of solution/ or tests/ into the agent image + hint files."""
    out = []
    df = task_paths(root)["Dockerfile"]
    text = read_text(df)
    for m in COPY_SRC.finditer(text):
        src = m.group(1).strip().strip('"').strip("'")
        low = src.lower().lstrip("./")
        if low.startswith("solution") or "/solution" in low:
            out.append(finding(name, "dockerfile", FAIL, "dockerfile-copies-solution",
                               detail=f"Dockerfile copies `{src}` — the reference "
                                      "solution would be readable by the agent.",
                               location="environment/Dockerfile",
                               fix="Remove the COPY; solution/ is oracle-only, mounted at verify time."))
        elif (low.startswith("tests") or ("/tests" in low and not low.startswith("test-"))):
            # The environment Dockerfile's build context is environment/, so
            # `COPY tests ...` resolves to environment/tests/ — NOT the grading
            # tests/ (which lives at the task root, outside the build context, and
            # is injected at /tests only at verify time so it CANNOT be COPY'd here).
            # Only a real leak if the copied dir actually holds the grader
            # (test_outputs.py / test.sh); otherwise it's a build fixture the task
            # intentionally exposes (e.g. a reader_stub contract).
            env_src = os.path.join(root, "environment", src.lstrip("./"))
            base = os.path.basename(low)
            # The copied path itself IS the grader file (e.g. COPY tests/test_outputs.py /app),
            # OR it is a dir that contains the grader. Either way the agent can read the
            # verifier. A dir holding only fixtures (no grader) is not a leak.
            holds_grader = (base in ("test_outputs.py", "test.sh")
                            or os.path.isfile(os.path.join(env_src, "test_outputs.py"))
                            or os.path.isfile(os.path.join(env_src, "test.sh")))
            if holds_grader:
                out.append(finding(name, "dockerfile", FAIL, "dockerfile-copies-tests",
                                   detail=f"Dockerfile copies `{src}`, which contains the "
                                          "grader (test_outputs.py/test.sh) — the verifier "
                                          "would be readable by the agent.",
                                   location="environment/Dockerfile",
                                   fix="Remove the COPY; tests/ is mounted at verify time only."))
            else:
                out.append(finding(name, "dockerfile", WARN, "dockerfile-copies-env-tests",
                                   detail=f"Dockerfile copies `{src}` (environment/{src.lstrip('./')}), "
                                          "a build fixture dir, not the grading tests/. Confirm it "
                                          "holds only agent-facing contract/fixture files, no answer key.",
                                   location="environment/Dockerfile",
                                   fix="Leave as-is if it is a fixture/contract the task exposes on "
                                       "purpose; the grading tests/ live at the task root and are not "
                                       "in this build context."))
        elif HINT_NAMES.search(os.path.basename(low)):
            out.append(finding(name, "dockerfile", WARN, "dockerfile-copies-hint-file",
                               detail=f"Dockerfile copies `{src}`, whose name suggests "
                                      "it may contain solution hints/notes.",
                               location="environment/Dockerfile",
                               fix="Inspect the file: it must define WHAT (rules/specs), "
                                   "not HOW (steps/approach). Remove if it leaks the approach."))
    return out


def _tests_bake(root, name):
    """Build-time bakes into absolute /tests (leak_detect classification)."""
    parts = _build_parts(root)
    writes = []
    for fn, txt in parts.items():
        for m in WRITE_TESTS.finditer(txt):
            path = next((g for g in m.groups() if g), None)
            if path:
                writes.append(path.rstrip())
    file_writes = [w for w in writes if w not in ("/tests", "/tests/", "/tests/.truth")]
    if not file_writes:
        return []
    rms = [m.group(1).rstrip("/") for fn, txt in parts.items()
           for m in RM_TESTS.finditer(txt)]

    def covered(p):
        for r in rms:
            if p == r:
                return True
            if r in ("/tests", "/tests/.truth") and p.startswith(r + "/"):
                return True
            if p.startswith(r.rstrip("/") + "/"):
                return True
        return False

    surviving = [p for p in file_writes if not covered(p)]
    if not surviving:
        return [finding(name, "anti_cheat", WARN, "tests-bake-removed",
                        detail=f"Build writes into /tests ({sorted(set(file_writes))}) "
                               "but a later rm removes it. Verify the rm actually runs.",
                        location="environment/Dockerfile",
                        fix="Confirm via build + `ls /tests`; relocate to source tests/ if it should persist.")]
    refs = _verifier_text(root)
    read_hits = sorted({p for p in surviving
                        if p in refs or os.path.basename(p) in refs})
    if read_hits:
        return [finding(name, "anti_cheat", FAIL, "tests-bake-verifier-reads",
                        detail=f"Build bakes {read_hits} into the agent image at /tests "
                               "AND the verifier reads it — likely a real leak (agent can "
                               "read the expected answer/grader).",
                        location="environment/Dockerfile",
                        fix="Move the artifact to source tests/.truth/ (mounted at verify "
                            "time), remove the build write, re-run oracle to confirm reward=1.")]
    return [finding(name, "anti_cheat", WARN, "tests-bake-unread",
                    detail=f"Build bakes {sorted(set(surviving))} into /tests with no "
                           "detected verifier read (dead or indirect).",
                    location="environment/Dockerfile",
                    fix="Confirm no base64/relative-path read; relocate out of /tests to "
                        "avoid the verify-time shadow failure.")]


def _truth_bake(root, name):
    """Truth baked into agent-visible non-/tests paths (leak_detect2)."""
    parts = _build_parts(root)
    btxt = "\n".join(parts.values())
    baked = set()
    for m in WRITE_ABS.finditer(btxt):
        p = next((g for g in m.groups() if g), None)
        if p and not p.startswith("/tests"):
            baked.add(p.rstrip().rstrip('"\').,'))
    if not baked:
        return []
    rms = {m.group(1).rstrip("/") for m in RM_ABS.finditer(btxt)}
    baked = {p for p in baked if not any(p == r or p.startswith(r + "/") for r in rms)}
    if not baked:
        return []
    vtxt = _verifier_text(root)
    vreads = {m.group(1) or m.group(2) for m in OPEN_ABS.finditer(vtxt)}
    vreads = {v for v in vreads if v and not v.startswith("/tests")
              and not v.startswith("/tmp")}
    read_hits = sorted(p for p in baked if p in vreads
                       or any(os.path.basename(p) == os.path.basename(v) for v in vreads))
    # Precision filter: a baked file the verifier reads is only "truth" if it
    # plausibly holds the expected answer. Source-code files (scaffold the agent
    # works on) and files under input-looking dirs (the task's own inputs) are NOT
    # truth unless their name is explicitly truthy. Keeps real value-file leaks
    # (e.g. /opt/valid_initial_count.txt) while dropping input/source false-positives.
    SRC_EXT = (".py", ".go", ".c", ".cc", ".cpp", ".h", ".hpp", ".js", ".ts",
               ".java", ".rs", ".rb", ".sh", ".pl", ".php", ".scala", ".kt")
    INPUT_DIR = re.compile(r"/(incoming|in[-_]?folder|in[-_]?box|inputs?|raw|"
                           r"fixtures?|samples?|corpus|evidence|data|datasets?|"
                           r"policy|policies|config|configs|probes?|grids?|"
                           r"streams?|logs?|journals?|uploads?|feed)s?(?:/|$)", re.I)
    # Dirs that are by construction NOT the agent's cheat surface for the expected
    # answer: transient/cache scratch, and verifier/grader-owned scaffolding.
    CACHE_DIR = re.compile(r"/(\.?cache|caches?|tmp|temp|work|scratch|run|state|"
                           r"build|dist|out|output)s?(?:/|$)", re.I)
    VERIFIER_DIR = re.compile(r"/(verifier|grader|checker|judge|harness)s?(?:/|$)", re.I)
    # mutated_* siblings are a re-derivation / mutated-rerun DEFENSE (the verifier
    # re-runs on perturbed inputs to defeat hardcoding), not a leaked answer.
    rederivation = any(os.path.basename(p).lower().startswith(("mutated_", "mutated-"))
                       or "/mutated" in p.lower() for p in read_hits)

    def is_truth_candidate(p):
        if TRUTHY.search(os.path.basename(p)):
            return True
        if p.lower().endswith(SRC_EXT):
            return False
        if INPUT_DIR.search(p) or CACHE_DIR.search(p) or VERIFIER_DIR.search(p):
            return False
        return True

    read_hits = [] if rederivation else [p for p in read_hits if is_truth_candidate(p)]
    truthy = sorted(p for p in baked if TRUTHY.search(os.path.basename(p)))
    if read_hits:
        # FP rule: if every read path is instruction-referenced, it is task INPUT
        # the agent is told about (e.g. a "provided control file"), not hidden
        # truth — downgrade to WARN for manual confirmation rather than FAIL.
        if _all_instruction_referenced(read_hits, root):
            return [finding(name, "anti_cheat", WARN, "verifier-reads-instruction-input",
                            detail=f"Verifier reads build-baked path(s) {read_hits}, but "
                                   "they are referenced in instruction.md — likely "
                                   "legitimate task input, not leaked truth.",
                            location="environment/Dockerfile",
                            fix="Confirm the path is input the agent is meant to have "
                                "(not the expected answer). If it encodes truth, relocate to tests/.")]
        return [finding(name, "anti_cheat", FAIL, "truth-baked-verifier-reads",
                        detail=f"Verifier reads build-baked agent-visible path(s) "
                               f"{read_hits} as expected truth (not referenced in "
                               "instruction.md, not under an input/cache/verifier dir, "
                               "no mutated-rerun defense) — likely a real leak.",
                        location="environment/Dockerfile",
                        fix="Move the truth artifact under tests/ (verify-time mount); "
                            "remove the agent-visible bake; re-run oracle.")]
    if truthy:
        return [finding(name, "anti_cheat", WARN, "truth-named-baked",
                        detail=f"Truth-named artifact(s) baked into agent-visible paths "
                               f"{truthy}; verifier read not statically detected.",
                        location="environment/Dockerfile",
                        fix="Inspect: if it encodes the expected answer, relocate to tests/.")]
    return []


def _reference_solve_reads_truth(root, name):
    """The reference solve.sh reads the very truth path the verifier compares
    against — i.e. it "solves" the task by reading the answer (14 tasks in the
    customer's second-5k audit). Pure static, high-value anti-cheat."""
    vtxt = _verifier_text(root)
    truth_paths = set()
    for m in OPEN_ABS.finditer(vtxt):
        p = m.group(1) or m.group(2)
        if (p and TRUTHY.search(os.path.basename(p))
                and not p.startswith("/tmp") and not p.startswith("/tests")):
            truth_paths.add(p)
    if not truth_paths:
        return []
    sol = read_text(task_paths(root)["solve.sh"])
    for h in glob.glob(os.path.join(root, "solution", "**", "*"), recursive=True):
        if os.path.isfile(h):
            sol += "\n" + read_text(h)
    # Only a real leak if the reference REFERENCES the truth path but never
    # PRODUCES it. Writing the (truthy-named) path — e.g. `... > /app/answer.txt`
    # for a task whose instruction says "write the answer to /app/answer.txt", or
    # via an output-path variable — is the reference emitting the deliverable, not
    # reading the answer. (Killed a whole class of false positives on real TB tasks
    # that write an answer.txt / secret.txt output file.)
    hits = sorted({p for p in truth_paths
                   if (p in sol or os.path.basename(p) in sol)
                   and not _solve_produces(sol, p)})
    if not hits:
        return []
    # FP rule (mirror of _truth_bake): a path the instruction references is task
    # INPUT the reference legitimately reads (e.g. a "reference DB" dir), not the
    # hidden answer — downgrade to WARN rather than FAIL.
    real = [h for h in hits if not _all_instruction_referenced([h], root)]
    if real:
        return [finding(name, "anti_cheat", FAIL, "reference-solve-reads-truth",
                        detail=f"solution/solve.sh references {real}, the same truth path the "
                               "verifier compares against — the reference solves by reading the "
                               "answer, not by doing the task.",
                        location="solution/solve.sh",
                        fix="Make the reference solve the task for real; move the truth file to "
                            "tests/.truth/ (verify-time only) so neither agent nor solve can read it.")]
    return [finding(name, "anti_cheat", WARN, "reference-reads-instruction-input",
                    detail=f"solution/solve.sh reads {hits}, but they are referenced in "
                           "instruction.md — likely legitimate task input, not the answer.",
                    location="solution/solve.sh",
                    fix="Confirm the path is input the reference is meant to read (not the "
                        "expected answer). If it encodes truth, move it to tests/.truth/.")]


# high-confidence live-credential signatures (low false-positive; example data
# rarely contains a real private key or cloud token).
SECRET_SIGS = [
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}")),
    ("slack-token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}")),
    ("gcp-key", re.compile(r'"private_key_id"\s*:\s*"[0-9a-f]{20,}"')),
]


def _baked_secrets(root, name):
    """Live credentials baked into agent-visible files (NVIDIA: 'no personal data';
    anti-cheat: no leaked secrets). High-confidence signatures only — WARN for
    manual confirmation, since a planted credential is occasionally the task."""
    env = os.path.join(root, "environment")
    hits = set()
    for h in glob.glob(os.path.join(env, "**", "*"), recursive=True):
        if not os.path.isfile(h):
            continue
        try:
            if os.path.getsize(h) > 256 * 1024:
                continue
        except OSError:
            continue
        txt = read_text(h)
        for label, rx in SECRET_SIGS:
            if rx.search(txt):
                hits.add((label, os.path.relpath(h, root)))
    if not hits:
        return []
    shown = sorted(hits)[:6]
    return [finding(name, "anti_cheat", WARN, "secret-baked-in-image",
                    detail=f"agent-visible file(s) contain credential-shaped data {shown} — "
                           "a leaked secret / personal data the agent shouldn't see.",
                    location="environment/",
                    fix="Remove the credential, or confirm it is intended task input (a "
                        "deliberately-planted secret for a recovery task).")]


def check_task(name, root):
    out = []
    out += _dockerfile_copies(root, name)
    out += _tests_bake(root, name)
    out += _truth_bake(root, name)
    out += _reference_solve_reads_truth(root, name)
    out += _baked_secrets(root, name)
    # keep both reported dimensions populated with a PASS sentinel when clean
    areas = {f["area"] for f in out}
    if "dockerfile" not in areas:
        out.append(finding(name, "dockerfile", PASS, "dockerfile-copy-clean"))
    if "anti_cheat" not in areas:
        out.append(finding(name, "anti_cheat", PASS, "leakage-static-clean"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks")
    ap.add_argument("--out", default="findings_leakage.json")
    args = ap.parse_args()
    findings = []
    tasks = discover_tasks(args.tasks)
    for name, root in tasks:
        findings.extend(check_task(name, root))
    n = emit(findings, args.out)
    fails = sum(1 for f in findings if f["severity"] == FAIL)
    print(f"[leakage] {len(tasks)} tasks, {n} findings, {fails} FAIL -> {args.out}")


if __name__ == "__main__":
    main()
