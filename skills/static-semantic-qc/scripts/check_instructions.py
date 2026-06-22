#!/usr/bin/env python3
"""Layer 0 — instruction-quality static heuristics (deterministic, read-only).

The deep, judgment-level instruction review (clarity, over-specification,
instruction↔verifier alignment) is the Layer 2 semantic sub-agent's job. This gate
catches only the mechanically-decidable instruction defects — the ones you don't
need judgment for — so they're caught cheaply on every task:

  - instruction-placeholder   leftover TODO/FIXME/lorem-ipsum/<PLACEHOLDER>/"your
                              answer here" — the task was shipped half-written
  - instruction-too-short     almost no prompt (likely underspecified)
  - instruction-too-long      over ~1500 tokens (Reflection caps instruction length)
  - instruction-relative-path explicit ./ or ../ path the agent must use (Reflection
                              requires absolute paths for files the agent reads/writes)
  - instruction-missing       instruction.md absent or empty (also caught by
                              structure, but reported here for the instructions area)

All WARN except the empty/missing case. Kept deliberately conservative — semantic
review owns the nuanced calls.

Usage:
    python check_instructions.py <tasks-dir> [--out findings_instructions.json]

Emits findings with area="instructions".
"""
import argparse
import re

from common import WARN, FAIL, PASS, finding, emit, read_text, discover_tasks, task_paths

PLACEHOLDER = re.compile(
    r"(\bTODO\b|\bFIXME\b|\bXXX\b|lorem ipsum|your answer here|fill (?:this |in)|"
    r"<\s*placeholder\s*>|\bplaceholder\b|tbd\b|\[insert |coming soon|"
    r"<\s*(?:your|the)[^>]{0,30}>)", re.I)
# minimum "real" instruction length (chars, after stripping code fences/whitespace).
# Public TB instructions run 80-2000+ chars; <120 is almost always underspecified.
MIN_CHARS = 120
# Reflection caps instructions at ~1500 tokens. Estimate tokens ~ chars/4 over the
# WHOLE file (code fences included — they count toward the agent's context too).
MAX_TOKENS = 1500
# explicit relative paths the agent is told to use (Reflection wants absolute paths).
# Match ./foo or ../foo path tokens; ignore bare ./ and markdown link fragments.
REL_PATH = re.compile(r"(?<![\w./])\.\.?/[\w./-]*\w")


def _visible_len(text):
    # drop fenced code blocks and collapse whitespace to estimate prose length
    t = re.sub(r"```.*?```", " ", text, flags=re.S)
    return len(re.sub(r"\s+", " ", t).strip())


def _est_tokens(text):
    return len(text) // 4


def check_task(name, root):
    out = []
    path = task_paths(root)["instruction.md"]
    text = read_text(path)
    loc = "instruction.md"
    if not text.strip():
        # structure.py also flags this; emit in the instructions area for the SSOT.
        return [finding(name, "instructions", FAIL, "instruction-empty",
                        detail="instruction.md is missing or empty — the agent has no prompt.",
                        location=loc,
                        fix="Write the task instruction (what success looks like).")]

    m = PLACEHOLDER.search(text)
    if m:
        out.append(finding(name, "instructions", WARN, "instruction-placeholder",
                           detail=f"instruction.md contains a placeholder/marker "
                                  f"(`{m.group(0).strip()}`) — looks half-written.",
                           location=loc,
                           fix="Remove the placeholder and finish the instruction text."))

    if _visible_len(text) < MIN_CHARS:
        out.append(finding(name, "instructions", WARN, "instruction-too-short",
                           detail=f"instruction.md has ~{_visible_len(text)} chars of prose "
                                  f"(< {MIN_CHARS}) — likely underspecified; a competent dev "
                                  "would have to guess the requirements.",
                           location=loc,
                           fix="State the concrete deliverable, inputs, and success criteria."))

    toks = _est_tokens(text)
    if toks > MAX_TOKENS:
        out.append(finding(name, "instructions", WARN, "instruction-too-long",
                           detail=f"instruction.md is ~{toks} tokens (> {MAX_TOKENS}) — "
                                  "Reflection wants concise prompts that encourage exploration, "
                                  "not long, over-specified ones.",
                           location=loc,
                           fix="Trim backstory/filler and step-by-step recipes; state what "
                               "success looks like, not how to get there."))

    rels = sorted({m.group(0) for m in REL_PATH.finditer(text)})
    if rels:
        shown = rels[:5]
        out.append(finding(name, "instructions", WARN, "instruction-relative-path",
                           detail=f"instruction.md uses relative path(s) {shown}"
                                  f"{' …' if len(rels) > 5 else ''} — Reflection requires "
                                  "ABSOLUTE paths for any file the agent must read/modify/create.",
                           location=loc,
                           fix="Rewrite the path(s) as absolute (e.g. /app/... or /workdir/...)."))

    if not out:
        out.append(finding(name, "instructions", PASS, "instructions-static-ok"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks")
    ap.add_argument("--out", default="findings_instructions.json")
    args = ap.parse_args()
    findings = []
    tasks = discover_tasks(args.tasks)
    for name, root in tasks:
        findings.extend(check_task(name, root))
    n = emit(findings, args.out)
    fails = sum(1 for f in findings if f["severity"] == FAIL)
    warns = sum(1 for f in findings if f["severity"] == WARN)
    print(f"[instructions] {len(tasks)} tasks, {n} findings, {fails} FAIL, {warns} WARN -> {args.out}")


if __name__ == "__main__":
    main()
