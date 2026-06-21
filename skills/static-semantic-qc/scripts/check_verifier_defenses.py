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

from common import WARN, PASS, finding, emit, read_text, discover_tasks, task_paths

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
        extra.append(finding(name, "tests", WARN, "degenerate-integrity-guard",
                             detail=f"`{guard_line.strip()}` compares against an in-image baked "
                                    "reference while the agent runs as root (no USER drop) — the "
                                    "agent overwrites both the file and its reference, so the "
                                    "guard is decorative, not a real anti-cheat defense.",
                             location="tests/",
                             fix="Keep the reference under tests/ (verify-time mount) or as a "
                                 "literal in test_outputs.py, or drop privileges with USER."))
        if "recompute-or-hash" in found:
            found.remove("recompute-or-hash")
    if found:
        return [finding(name, "tests", PASS, "verifier-defended",
                        detail=f"verifier has anti-cheat defense(s): {found} — resists "
                               "hardcode / fake-artifact cheats; cheat-vector candidates "
                               "against it are suppressed.",
                        location="tests/")] + extra
    return [finding(name, "tests", WARN, "verifier-undefended",
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
