#!/usr/bin/env python3
"""Layer 0 — instruction-quality static heuristics (deterministic, read-only).

The deep, judgment-level instruction review (clarity, over-specification,
instruction↔verifier alignment) is the Layer 2 semantic sub-agent's job. This gate
catches only the mechanically-decidable instruction defects — the ones you don't
need judgment for — so they're caught cheaply on every task:

  - instruction-placeholder   leftover TODO/FIXME/lorem-ipsum/<PLACEHOLDER>/"your
                              answer here" — the task was shipped half-written
  - instruction-too-short     almost no prompt (likely underspecified)
  - instruction-too-long      1500+ o200k_base tokens (FAIL — Reflection requires <1500)
  - instruction-relative-path explicit ./ or ../ path the agent must use (Reflection
                              requires absolute paths for files the agent reads/writes)
  - prescriptive-instruction  "spec-sheet" smells — dictated function signatures,
                              step-by-step algorithm recipes, exact byte/hex layouts —
                              i.e. the prompt says *how* not *what*. A WARN CANDIDATE
                              only: some specificity is verifier-intrinsic (the test
                              links a named function), so the semantic reviewer confirms
                              whether it's gratuitous (`over-specified-instruction`).
  - structured-output-undocumented  the task requires a structured output (JSON/CSV/
                              YAML/config/DB rows/API response) but no schema is
                              documented in the instruction OR a spec/sample file in the
                              environment. WARN CANDIDATE — the schema may live in a
                              sample the agent studies, so the reviewer (which reads the
                              env) confirms.
  - instruction-missing       instruction.md absent or empty (also caught by
                              structure, but reported here for the instructions area)

All WARN except the empty/missing and too-long cases, which are FAIL. Kept deliberately
conservative — semantic review owns the nuanced calls.

Usage:
    python check_instructions.py <tasks-dir> [--out findings_instructions.json]

Emits findings with area="instructions".
"""
import argparse
import os
import re

import tiktoken

from common import WARN, FAIL, PASS, finding, emit, read_text, discover_tasks, task_paths

# HARD markers are never legitimate task language — always a half-written instruction.
PLACEHOLDER_HARD = re.compile(
    r"(lorem ipsum|your answer here|<\s*placeholder\s*>|\[insert |coming soon|"
    r"<\s*(?:your|the)[^>]{0,30}>)", re.I)
# SOFT markers (TODO/FIXME/"fill in"/stub/placeholder) are a defect ONLY as a leftover
# template marker — NOT when completing them IS the task ("implement the stub functions",
# "resolve the TODOs marked in the code"). Gated by TASK_WORK context below. (check-bug fix.)
PLACEHOLDER_SOFT = re.compile(
    r"(\bTODO\b|\bFIXME\b|\bXXX\b|fill (?:this |in)|\bplaceholder\b|\btbd\b)", re.I)
TASK_WORK = re.compile(
    r"\b(stub|implement|complete|resolve|finish|fix|function|method|class|marked|marker|"
    r"in the (?:code|source|file|repo|module|script)|each (?:function|method))\b", re.I)
# minimum "real" instruction length (chars, after stripping code fences/whitespace).
# Public TB instructions run 80-2000+ chars; <120 is almost always underspecified.
MIN_CHARS = 120
# Reflection requires instructions to stay under 1500 tokens. o200k_base is a
# deterministic tiktoken proxy for this gate; it is not the exact Qwen tokenizer.
# Count the WHOLE file (code fences included — they consume context too).
MAX_TOKENS = 1500
TOKEN_ENCODING = tiktoken.get_encoding("o200k_base")
# explicit relative paths the agent is told to use (Reflection wants absolute paths).
# Match ./foo or ../foo path tokens; ignore bare ./ and markdown link fragments.
REL_PATH = re.compile(r"(?<![\w./])\.\.?/[\w./-]*\w")

# ---- prescriptiveness ("spec-sheet") signals: the prompt dictates HOW, not WHAT ----
# A dictated function/method signature: a C-style prototype, a `def name(...)`, or a
# backtick-wrapped call with >=2 named params (`expand(template, vars_dict)`).
SIG_C = re.compile(r"\b(?:int|void|size_t|unsigned|char|double|float|bool|long)\s+\*?\w+\s*\([^;){]{0,200}\)")
SIG_PY = re.compile(r"\bdef\s+\w+\s*\(")
SIG_BACKTICK = re.compile(r"`\w+\([a-z_]\w*(?:\s*,\s*[a-z_]\w*)+\)`")
# a numbered step that opens with a compute/transform imperative = recipe, not a goal
STEP_RECIPE = re.compile(
    r"^\s*\d+[.)]\s*(read|comput|decode|encode|hash|loop|iterat|pars|hex|return|appl|"
    r"concat|split|sort|multipl|deriv|call|convert|map|extract|append|write|insert|"
    r"replace|verif|xor|shift|round|truncat|pad)\w*\b", re.I | re.M)
# exact byte/hex layout dictation (prose "Bytes 0-3"/"48-byte"/"12 bytes", hex tags,
# fixed-width ints, endianness, or a markdown offset/length table)
BYTE_LAYOUT = re.compile(r"\bbytes?\s+\d+|\b\d+\s+bytes?\b|\b\d+-byte\b|\b0x[0-9a-fA-F]{2}\b|"
                         r"\buint(?:8|16|32|64)\b|little-endian|big-endian|\bTLV\b|"
                         r"\|\s*offset\b", re.I)
# bare directive density (must/exactly/...) — a weak co-signal, never alone
DIRECTIVE = re.compile(r"\b(must|exactly|precisely|verbatim|do not|step\s+\d)\b", re.I)

# ---- structured-output schema specification ----
_FMT = r"(?:json|jsonl|ndjson|csv|tsv|yaml|yml|toml|xml|parquet)"
# the task is asked to PRODUCE a structured artifact (output context + a format/sink)
STRUCT_REQ = re.compile(
    r"\b(?:write|writes|output|outputs|produc\w*|return\w*|generat\w*|sav\w*|emit\w*|"
    r"creat\w*|populat\w*|insert\w*|store\w*|serializ\w*|export\w*)\b[^.\n]{0,70}"
    r"\b(?:" + _FMT + r"|config\s+file|database|\btable\b|\brows?\b|api\s+response|endpoint)\b",
    re.I)
# NOTE: deliberately no bare-path trigger — a standalone `.json`/`.csv` mention matches
# INPUT files too (e.g. "compare against /etc/allocations.json"), which over-flags.
# STRUCT_REQ requires an output verb, so it keys on producing structured output.
# the schema IS documented in the instruction: a tagged/sample block, JSON keys, or a
# field/column/key enumeration.
SCHEMA_FENCE = re.compile(r"```\s*" + _FMT, re.I)
SCHEMA_JSONKEYS = re.compile(r'"[\w-]+"\s*:')                      # "field": ...
SCHEMA_ENUM = re.compile(r"\b(columns?|fields?|keys?|schema|format|headers?|structure)\b\s*[:\-]", re.I)
SCHEMA_FOLLOWS = re.compile(r"\b(?:the following|this)\s+(?:fields?|columns?|keys?|schema|format|structure)\b", re.I)
# fields named inline near an output statement: "...contains `msg_id`, `saas_ts`, `action`"
SCHEMA_INLINE = re.compile(r"\b(?:contain|includ|with|having|consist|compris)\w*\b[^.\n]{0,80}"
                           r"`[\w-]+`[^.\n]{0,80}`[\w-]+`", re.I)
# a named key/field/column with a quoted name: "...a JSON object with a key 'result'"
SCHEMA_NAMED = re.compile(r"\b(?:key|field|column|attribute|propert|header|tag)s?\b[^.\n]{0,24}['\"`][\w-]+['\"`]", re.I)
# environment files that would document a schema/sample the agent can read
ENV_SPEC = re.compile(r"(schema|spec|openapi|swagger|\.proto$|sample|example|fixture|"
                      r"expected|template)", re.I)


def _visible_len(text):
    # drop fenced code blocks and collapse whitespace to estimate prose length
    t = re.sub(r"```.*?```", " ", text, flags=re.S)
    return len(re.sub(r"\s+", " ", t).strip())


def _count_tokens(text):
    return len(TOKEN_ENCODING.encode(text))


def _prescriptive_signals(text):
    """Return the list of strong spec-sheet smells present (>=2 ⇒ flag a candidate)."""
    n_sig = len(SIG_C.findall(text)) + len(SIG_PY.findall(text)) + len(SIG_BACKTICK.findall(text))
    n_step = len(STEP_RECIPE.findall(text))
    n_byte = len(BYTE_LAYOUT.findall(text))
    n_dir = len(DIRECTIVE.findall(text))
    smells = []
    if n_sig >= 2:
        smells.append(f"{n_sig} dictated function signatures")
    if n_step >= 3:
        smells.append(f"{n_step}-step algorithm recipe")
    if n_byte >= 4:
        smells.append("exact byte/hex layout")
    # a multi-step recipe with heavy directive language is itself a strong smell
    if n_step >= 3 and n_dir >= 10 and "exact byte/hex layout" not in smells:
        smells.append(f"{n_dir} prescriptive directives")
    return smells


def _requires_structured_output(text):
    return bool(STRUCT_REQ.search(text))


def _schema_documented(text, root):
    """True if the structured output's schema is documented in the instruction OR a
    spec/sample file in the environment (so the agent can know the exact shape)."""
    if (SCHEMA_FENCE.search(text) or SCHEMA_JSONKEYS.search(text)
            or SCHEMA_ENUM.search(text) or SCHEMA_FOLLOWS.search(text)
            or SCHEMA_INLINE.search(text) or SCHEMA_NAMED.search(text)):
        return True
    env = task_paths(root)["environment"]
    if os.path.isdir(env):
        for dirpath, _dirs, files in os.walk(env):
            for fn in files:
                if ENV_SPEC.search(fn):
                    return True
    return False


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

    m = PLACEHOLDER_HARD.search(text)
    if not m:
        # a soft marker is a defect only if it is NOT describing the agent's task
        for sm in PLACEHOLDER_SOFT.finditer(text):
            lo, hi = max(0, sm.start() - 80), min(len(text), sm.end() + 80)
            if not TASK_WORK.search(text[lo:hi]):
                m = sm
                break
    if m:
        out.append(finding(name, "instructions", FAIL, "instruction-placeholder",
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

    toks = _count_tokens(text)
    if toks >= MAX_TOKENS:
        out.append(finding(name, "instructions", FAIL, "instruction-too-long",
                           detail=f"instruction.md is {toks} o200k_base tokens "
                                  f"(required: < {MAX_TOKENS}) — Reflection caps instruction "
                                  "length; concise prompts that "
                                  "encourage exploration are required, not long, over-specified ones.",
                           location=loc,
                           fix="Trim backstory/filler and step-by-step recipes; state what "
                               "success looks like, not how to get there."))

    rels = sorted({m.group(0) for m in REL_PATH.finditer(text)})
    if rels:
        shown = rels[:5]
        out.append(finding(name, "instructions", FAIL, "instruction-relative-path",
                           detail=f"instruction.md uses relative path(s) {shown}"
                                  f"{' …' if len(rels) > 5 else ''} — Reflection requires "
                                  "ABSOLUTE paths for any file the agent must read/modify/create.",
                           location=loc,
                           fix="Rewrite the path(s) as absolute (e.g. /app/... or /workdir/...)."))

    # prescriptiveness: needs >=2 independent spec-sheet smells to fire (so a task
    # that merely lists acceptance criteria isn't flagged). A CANDIDATE only — the
    # reviewer decides if the specificity is gratuitous vs verifier-intrinsic.
    smells = _prescriptive_signals(text)
    if len(smells) >= 2:
        out.append(finding(name, "instructions", FAIL, "prescriptive-instruction",
                           detail=f"instruction.md reads like an implementation spec ({'; '.join(smells)}) "
                                  "— it may dictate *how* rather than *what success looks like*, "
                                  "against Reflection's 'simple, exploration-encouraging' bar. "
                                  "CANDIDATE: confirm the detail is gratuitous, not verifier-intrinsic "
                                  "(a signature the test links / a format the env defines is legitimate).",
                           location=loc,
                           fix="State the deliverable and acceptance criteria; drop step-by-step "
                               "recipes and any implementation detail the verifier doesn't require."))

    # structured output required but no schema documented (instruction or env spec/sample)
    if _requires_structured_output(text) and not _schema_documented(text, root):
        out.append(finding(name, "instructions", FAIL, "structured-output-undocumented",
                           detail="instruction.md asks for a structured output (JSON/CSV/YAML/"
                                  "config/DB rows/API response) but no schema is documented — no "
                                  "sample/field-list/format block in the prompt and no spec/sample "
                                  "file in environment/. The agent can't know the exact shape. "
                                  "CANDIDATE: the schema may be in a sample the agent studies — the "
                                  "reviewer confirms against the env and the verifier's assertions.",
                           location=loc,
                           fix="Document the exact schema (fields/columns/types + an example) in "
                               "the instruction, or reference a spec file staged in environment/."))

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
