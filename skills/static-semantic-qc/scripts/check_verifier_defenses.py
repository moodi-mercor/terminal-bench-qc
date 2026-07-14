#!/usr/bin/env python3
"""Layer 1 — verifier anti-cheat DEFENSE detector (deterministic, read-only).

The adversarial Part-3 agent over-claims cheat-vectors: reading a task, it can
always *imagine* "I'd just hardcode the answer / write the artifact directly", and
it can't tell from reading whether the verifier would catch that. Three agent
attempts (attack, reworded attack, skeptic) all over-claimed — it's a systematic
limit of reading-only judgement.

This flips the question to one a machine CAN answer reliably: does the verifier
have a DEFENSE that defeats the hardcode / fake-artifact class of cheats? A defense
is provable by reading the test's *structure*, not by guessing whether a cheat
slips through. If a strong defense is present, those cheats cannot work — so a
cheat-vector candidate on that verifier is suppressed (see aggregate.reconcile).

Strong defenses detected (any one ⇒ the verifier resists hardcode/fake-artifact):
  - mutated-rerun     : the test re-runs the agent's program on regenerated /
                        mutated / held-out / unseen inputs (hardcoded values die there)
  - recompute-or-hash : the test derives the expected value itself (hash / sum /
                        Counter / recompute) instead of comparing to a baked literal
  - source-grep-guard : the test greps the agent's source for hardcoded literals
  - re-exec-agent     : the test invokes the agent's produced program/script (not
                        just reads its output file), so a static fake won't satisfy it

Emits one finding per task (area="tests"): `verifier-defended` (PASS) listing the
defenses, or `verifier-undefended` (WARN) — a verifier with none of these and only
literal comparisons is genuinely gameable, and a cheat-vector on it is credible.

It also takes Reflection's "functional verification" stance: `source-match-verification`
(WARN) when the verifier matches the agent's SOURCE for keywords/regex but never
executes it / queries a service-DB / parses a produced artifact — verifying text, not
behaviour (brittle + gameable). A source grep alongside a real outcome test is a guard,
not this defect, so it fires only when there is NO functional signal.

A shell integrity guard (`sha256sum -c` / `md5sum -c` / `cmp`) against an in-image
baked reference is NOT a real defense when the agent runs as root (no USER drop):
the agent overwrites both the file and its reference. Such a `degenerate-integrity-guard`
(WARN) does not count as `recompute-or-hash`; it counts only if the reference lives
under tests/ (verify-time mount), is a literal in test_outputs.py, or the agent
drops privileges. (Verify-time scratch under /tmp is excluded.)

Usage:
    python check_verifier_defenses.py <tasks-dir> [--out findings_verifier_defenses.json]
"""
import argparse
import glob
import os
import re

from common import FAIL, WARN, PASS, finding, emit, read_text, discover_tasks, task_paths
import assert_classify

MUTATED = re.compile(r"\b(mutat\w*|regenerat\w*|re[_-]?generate|reshuffl\w*|"
                     r"held[_ -]?out|unseen|perturb\w*|fresh[_ ]?(?:data|input|set)|"
                     r"randomiz\w*|new[_ ]?(?:seed|dataset|inputs?))\b", re.I)
RECOMPUTE = re.compile(r"(hashlib|sha\d|md5|hmac|blake2|checksum|crc32|"
                       r"recompute\w*|recalculat\w*)|expected\s*=\s*(?:sum|len|sorted|"
                       r"Counter|max|min)\s*\(", re.I)
# a source-grep / anti-hardcode guard over the agent's own code (any token order)
SRC_GREP = re.compile(r"(grep\b[^\n]*/app|subprocess\.[a-z_]+\([^)]*['\"]grep|"
                      r"open\([^)\n]*\.(?:py|go|c|cc|cpp|rs|js|ts|java|sh)['\"][^)\n]*\)\.read\(\)|"
                      r"(?:not\s+)?in\s+open\([^)\n]*\)\.read\(\)|"
                      r"\.read\(\)[^\n]*\b(?:not in|in)\b|allowlist|lint[_-]?allow)", re.I)
# the verifier actually RUNS the agent's produced program (re-execution),
# not just reads an output file — a static fake artifact won't satisfy it
RE_EXEC = re.compile(r"(?:subprocess\.(?:run|check_output|check_call|call|Popen)|os\.system\(|"
                     r"os\.popen\()[^\n]*?(?:['\"]/app/|['\"]\./|/app/\S+\.(?:py|sh|bin|out)|"
                     r"\bpython3?\b[^\n]*/app/|/usr/local/bin/)|\bpython3?\s+/app/", re.I)

DEFENSES = [("mutated-rerun", MUTATED), ("recompute-or-hash", RECOMPUTE),
            ("source-grep-guard", SRC_GREP), ("re-exec-agent", RE_EXEC)]

# FUNCTIONAL verification = the verifier executes code / queries a service or DB /
# parses a produced data artifact (i.e. checks BEHAVIOUR, not source text). Used to
# decide whether source-keyword matching is the PRIMARY signal (a defect) or just an
# anti-cheat guard sitting alongside a real outcome test (fine). RE_EXEC also counts.
# Require call SYNTAX, not bare keywords — a source-grep that searches for "import
# pandas" / "SELECT" must NOT read as functional (that was the bug). Each alternative
# ends in `(` (an actual call) or `.connect(`/`.execute(` (a real DB/service op).
FUNCTIONAL = re.compile(
    r"subprocess\.(?:run|Popen|check_output|check_call|call)\s*\(|os\.(?:system|popen)\s*\(|"
    r"requests\.(?:get|post|put|delete|head|patch|request)\s*\(|urlopen\s*\(|httpx\.\w+\s*\(|"
    r"socket\.(?:create_connection|socket)\s*\(|\.execute\s*\(|\.cursor\s*\(|"
    r"(?:sqlite3|psycopg2?|pymysql|duckdb|redis)\.(?:connect|Redis|StrictRedis)\s*\(|"
    r"json\.loads?\s*\(|yaml\.(?:safe_)?load\s*\(|pd\.read_\w+\s*\(|pandas\.read_\w+\s*\(|"
    r"\bImage\.open\s*\(|csv\.(?:reader|DictReader)\s*\(|"
    r"importlib\.|spec_from_file_location\s*\(|exec_module\s*\(|runpy\.|__import__\s*\(",
    re.I)
# the verifier reads the agent's SOURCE CODE (a file with a code extension) or greps
# it — keyword/text matching rather than behaviour. Keyed strictly on a source-code
# EXTENSION so reading the agent's OUTPUT (a report/log/.txt/.json, often in a var
# named `content`) is NOT mistaken for source matching.
_SRC_EXT = r"(?:py|go|c|cc|cpp|cxx|h|hpp|rs|js|ts|java|sh|rb|php|pl|scala|kt|lua)"
# require the source file's CONTENT to be read (open(...).read() / grep), NOT a bare
# Path(...py) — that's usually an existence check, and the verifier then imports & runs
# the module (functional). Keeps this to genuine "read the source text and match it".
SOURCE_MATCH = re.compile(
    r"open\s*\([^)\n]*\." + _SRC_EXT + r"['\"][^)\n]*\)[^\n]*\.read\s*\(\)|"
    r"\bgrep\b[^\n]*\." + _SRC_EXT + r"\b", re.I)

# A shell integrity guard (`sha256sum -c`, `md5sum -c`, `cmp`). When it compares
# against an *in-image baked* reference and the agent runs as root, it is decorative
# — the agent overwrites both the file and its reference. (Bare `diff` is excluded:
# it is overwhelmingly a legitimate output comparison, often over verify-time scratch.)
SHELL_INTEGRITY = re.compile(r"\b(?:sha\d*sum|md5sum)\s+-c\b|\bcmp\s+\S", re.I)
# a genuine (non-degenerate) recompute: Python derives the expected value itself.
REAL_RECOMPUTE = re.compile(r"(hashlib|hmac|blake2|recompute\w*|recalculat\w*|Counter\s*\(|"
                            r"expected\s*=\s*(?:sum|len|sorted|max|min)\s*\()", re.I)
# a non-root USER directive means the agent cannot overwrite the baked reference.
USER_DROP = re.compile(r"^\s*USER\s+(?!root\b)\S", re.M | re.I)

# --- BRITTLE-VERIFIER signals (deterministic, conservative) ---
# (B) wall-clock dependence: the verifier MEASURES elapsed time and asserts a bound
# on it. Reward then rides on host speed / CI load (telemetry/siem-hang class). Needs
# BOTH a clock read AND an elapsed-bound assertion to fire (a bare time.time() for a
# filename, or a startup sleep, must NOT trip it).
WALLCLOCK = re.compile(r"time\.(?:time|monotonic|perf_counter)\s*\(\)|datetime\.now\s*\(\)|"
                       r"\btime\s+\w", re.I)
ELAPSED_ASSERT = re.compile(
    r"(?:elapsed|duration|took|latency|runtime|wall[_ ]?clock)\w*\s*[<>]=?\s*[\d.]+|"
    r"assert[^\n]*\b(?:elapsed|duration|took|latency|runtime)\b[^\n]*[<>]|"
    r"assert[^\n]*[<>]=?\s*[\d.]+[^\n]*\b(?:elapsed|duration|seconds?|secs?)\b", re.I)
# (C1) self-consistency: the EXPECTED/reference value is READ (not derived) from the
# agent's own writable tree — nothing external pins the answer, so the verifier checks
# the agent against itself (hospital-railyard class). Require open() to be the DIRECT
# value (optionally one int/float/str/json wrapper); if it is buried inside sum()/a
# comprehension/hashlib, that is RECOMPUTE-from-input (the good pattern) and must NOT
# fire — that was a false positive on legit recompute.
SELF_CONSISTENT = re.compile(
    r"(?:expected|reference|baseline|golden|truth)\w*\s*=\s*"
    r"(?:int\s*\(|float\s*\(|str\s*\(|bytes\s*\(|json\.loads?\s*\(|yaml\.\w+\s*\(|\.?\s*)*\s*"
    r"open\s*\(\s*['\"](?:/app|/workspace|/data|\./)", re.I)
# (C2) filename-encodes-answer: pass/validity decided from a file's NAME, not its
# content — a validity ADJECTIVE tested against an explicit filename extraction.
# Deliberately tight: a bare `.name`/`.stem`, or generic words like `expected`/`pass`/
# `fail`, must NOT trip it (those match benign idioms — `for filename, expected in
# expected.items()` dict iteration, error-message f-strings like `... in {f.name}`).
# Requires (a) an explicit basename/filename/fname token AND (b) a true validity
# adjective, in either order, close together.
_VALIDITY = r"valid|invalid|malicious|benign|legit(?:imate)?|forbidden|infected|clean|safe|unsafe|good|bad"
_FNAME = r"os\.path\.basename|\bbasename\s*\(|\bfilename\b|\bfname\b"
FILENAME_ENCODES = re.compile(
    r"(?:" + _FNAME + r")[^\n]{0,60}\b(?:" + _VALIDITY + r")\b|"
    r"\b(?:" + _VALIDITY + r")\b[^\n]{0,30}\bin\b[^\n]{0,25}(?:" + _FNAME + r")", re.I)


def _verifier_text(root):
    parts = [read_text(task_paths(root)["test.sh"]),
             read_text(task_paths(root)["test_outputs.py"])]
    tdir = task_paths(root)["tests"]
    for h in glob.glob(os.path.join(tdir, "**", "*.py"), recursive=True):
        if os.path.isfile(h) and not h.endswith("test_outputs.py"):
            parts.append(read_text(h))
    return "\n".join(parts)


def check_task(name, root):
    txt = _verifier_text(root)
    if not txt.strip():
        return [finding(name, "tests", PASS, "verifier-defenses-unknown",
                        detail="no verifier text found to analyse.")]
    found = [label for label, rx in DEFENSES if rx.search(txt)]
    extra = []
    # Degenerate integrity guard: a shell `cmp`/`sha*sum -c` against an in-image
    # baked reference is decorative when the agent is root. If it's the ONLY thing
    # making the verifier look "recompute"-defended, drop that — the verifier is
    # actually gameable.
    guard_line = next((ln for ln in txt.splitlines() if SHELL_INTEGRITY.search(ln)), None)
    agent_is_root = not USER_DROP.search(read_text(task_paths(root)["Dockerfile"]))
    # only degenerate when the reference is an in-image baked file: exclude verify-time
    # scratch (/tmp) and the read-only verify-time mount (tests/ / .truth).
    if (guard_line and agent_is_root and "tests/" not in guard_line
            and ".truth" not in guard_line and "/tmp" not in guard_line
            and not REAL_RECOMPUTE.search(txt)):
        extra.append(finding(name, "tests", FAIL, "degenerate-integrity-guard",
                             detail=f"`{guard_line.strip()}` compares against an in-image baked "
                                    "reference while the agent runs as root (no USER drop) — the "
                                    "agent overwrites both the file and its reference, so the "
                                    "guard is decorative, not a real anti-cheat defense.",
                             location="tests/",
                             fix="Keep the reference under tests/ (verify-time mount) or as a "
                                 "literal in test_outputs.py, or drop privileges with USER."))
        if "recompute-or-hash" in found:
            found.remove("recompute-or-hash")
    # Functional-verification stance (Reflection): a verifier that matches the agent's
    # SOURCE for keywords/regex but never executes it, queries a service/DB, or parses
    # a produced artifact is verifying TEXT not BEHAVIOUR — brittle (rejects correct
    # alternative implementations) and gameable. A source grep ALONGSIDE a real outcome
    # test is an anti-cheat guard, not this defect — so require no functional signal.
    if SOURCE_MATCH.search(txt) and not (RE_EXEC.search(txt) or FUNCTIONAL.search(txt)):
        extra.append(finding(name, "tests", FAIL, "source-match-verification",
                             detail="verifier checks the agent's SOURCE for keywords/patterns "
                                    "(substring/regex/grep) but never executes the program, "
                                    "queries a service/DB, or parses a produced artifact — it "
                                    "verifies text, not behaviour. Brittle (a correct alternative "
                                    "implementation fails) and gameable. CANDIDATE — confirm the "
                                    "verifier has no functional assertion.",
                             location="tests/",
                             fix="Verify behaviour: run the agent's program / check its output / "
                                 "query the service, and keep source-greps only as an anti-cheat "
                                 "guard alongside the outcome test."))
    # literal-only verifier (AST-precise revival of the dropped weak-verifier check):
    # the grader executes nothing, recomputes nothing, and every scored test only
    # compares against baked literal constants. Brittle (a correct-but-different output
    # fails) and gameable if the agent can read the test. The whole-file functional/
    # recompute/import scan keeps real functional verifiers out (the old regex check's
    # 34 FPs). WARN candidate — confirm at runtime.
    cls = assert_classify.classify_path(task_paths(root)["test_outputs.py"]).get("file", {})
    if cls.get("all_literal_only"):
        vals = cls.get("literal_values") or []
        extra.append(finding(name, "tests", FAIL, "literal-only-verifier",
                             detail="every scored test compares the agent's output only against "
                                    f"hardcoded literals {vals[:8]} — the verifier neither executes "
                                    "the program, queries a service, nor recomputes the expected "
                                    "value. Brittle and gameable; confirm the baked answers can't "
                                    "be hardcoded by a no-op.",
                             location="tests/test_outputs.py",
                             fix="Recompute the expected value from the task inputs, run the agent's "
                                 "program, or assert on behaviour — don't compare only to baked literals."))
    # (B) wall-clock-dependent reward — measured elapsed time gates the verdict.
    if WALLCLOCK.search(txt) and ELAPSED_ASSERT.search(txt):
        extra.append(finding(name, "tests", FAIL, "wall-clock-dependent-verifier",
                             detail="verifier measures elapsed wall-clock time and asserts a "
                                    "bound on it — the reward then depends on host speed / CI "
                                    "load, so a correct-but-slow solution flakes. Brittle.",
                             location="tests/",
                             fix="Assert on the computed RESULT, not on how long it took; if "
                                 "timing matters, use a generous margin or a logical (not "
                                 "wall-clock) progress signal."))
    # (C1) self-consistency — expected value sourced from the agent's own output tree.
    if SELF_CONSISTENT.search(txt):
        extra.append(finding(name, "tests", FAIL, "verifier-self-consistent",
                             detail="the verifier's expected/reference value is read from the "
                                    "agent's own writable tree (/app, /workspace, /data, cwd) — "
                                    "nothing external pins the answer, so it can be checking the "
                                    "agent against itself. Confirm the expected value comes from "
                                    "tests/ or is recomputed independently.",
                             location="tests/",
                             fix="Source the expected value from tests/ (verify-time) or recompute "
                                 "it from the task inputs, not from a file the agent writes."))
    # (C2) filename-encodes-answer — validity decided from a file's name, not content.
    if FILENAME_ENCODES.search(txt):
        extra.append(finding(name, "tests", FAIL, "filename-encodes-answer",
                             detail="the verifier appears to decide pass/validity from a file's "
                                    "NAME (basename/stem matched against a validity label) rather "
                                    "than its content — gameable (rename to match) and brittle.",
                             location="tests/",
                             fix="Decide validity from the file's CONTENT/behaviour, not its name."))
    if found:
        return [finding(name, "tests", PASS, "verifier-defended",
                        detail=f"verifier has anti-cheat defense(s): {found} — resists "
                               "hardcode / fake-artifact cheats; cheat-vector candidates "
                               "against it are suppressed.",
                        location="tests/")] + extra
    return [finding(name, "tests", FAIL, "verifier-undefended",
                    detail="verifier shows no mutated-rerun / recompute / source-grep / "
                           "re-execution defense — if it compares only against baked "
                           "literals it is genuinely gameable; a cheat-vector here is credible.",
                    location="tests/",
                    fix="Add a mutated/held-out rerun, recompute the expected value, or "
                        "grep the agent's source for hardcoded answers.")] + extra


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks")
    ap.add_argument("--out", default="findings_verifier_defenses.json")
    args = ap.parse_args()
    findings = []
    tasks = discover_tasks(args.tasks)
    for name, root in tasks:
        findings.extend(check_task(name, root))
    n = emit(findings, args.out)
    defended = sum(1 for f in findings if f["title"] == "verifier-defended")
    print(f"[verifier_defenses] {len(tasks)} tasks, {defended} defended -> {args.out}")


if __name__ == "__main__":
    main()
