#!/usr/bin/env python3
"""Layer 0 — security / anti-cheat content scan (deterministic, read-only).

Covers the statically-decidable half of Reflection's "Security and anti-cheat" tab,
over the files the AGENT can see (instruction.md + environment/ — NOT tests/ or
solution/, which are verify-time mounts):

  - prompt-injection   text instructing the agent to ignore the task, reveal the
                       answer/secret, skip the tests, or tamper with evaluation
  - hidden-unicode     zero-width / bidirectional-control / BOM characters that hide
                       or reorder text from a human reviewer
  - obfuscated-payload base64|hex blobs piped into a shell, eval/exec of decoded
                       data, or `curl|sh` — unauditable dynamic execution

All WARN: these are review prompts, not proofs (a legitimate task may discuss
"injection" as its subject). Leakage of solution/answer files and reward tampering
are handled by check_leakage.py / check_reward_hack.py.

Usage:
    python check_security.py <tasks-dir> [--out findings_security.json]

Emits findings with area="anti_cheat".
"""
import argparse
import os
import re

from common import FAIL, WARN, PASS, finding, emit, read_text, discover_tasks, task_paths

PROMPT_INJECTION = re.compile(
    r"(ignore\s+(all\s+|any\s+|the\s+)?(previous|prior|above|earlier)\s+instructions|"
    r"disregard\s+(the\s+)?(above|previous|task|instructions)|"
    r"ignore\s+the\s+(task|rules|tests?)|"
    r"do\s+not\s+run\s+the\s+tests?|skip\s+the\s+(tests?|verifier)|"
    r"reveal\s+(the\s+)?(answer|solution|secret|flag|password)|"
    r"print\s+(the\s+)?(answer|flag|secret)|"
    r"you\s+are\s+now\s+|the\s+(correct\s+)?answer\s+is\s*[:=]|"
    r"to\s+pass(\s+the\s+tests?)?,?\s+(just|simply|only))", re.I)

# zero-width, BOM, and bidirectional-control code points
HIDDEN_CHARS = {
    "​": "ZERO WIDTH SPACE", "‌": "ZERO WIDTH NON-JOINER",
    "‍": "ZERO WIDTH JOINER", "⁠": "WORD JOINER",
    "﻿": "BOM / ZERO WIDTH NO-BREAK SPACE",
    "‪": "LRE", "‫": "RLE", "‬": "PDF", "‭": "LRO",
    "‮": "RLO", "⁦": "LRI", "⁧": "RLI", "⁨": "FSI",
    "⁩": "PDI",
}

OBFUSCATION = [
    (re.compile(r"\bbase64\b[^\n|]*\|\s*(?:ba)?sh\b", re.I), "base64 piped into a shell"),
    (re.compile(r"\bbase64\s+(?:-d|--decode)\b", re.I), "base64 decode of an inline blob"),
    (re.compile(r"\beval\s*\(\s*(?:base64|bytes\.fromhex|codecs\.decode)", re.I),
     "eval of decoded data"),
    (re.compile(r"\bexec\s*\(\s*(?:base64|bytes\.fromhex|codecs\.decode|__import__)", re.I),
     "exec of decoded/dynamic data"),
    (re.compile(r"\beval\s+\"\$\(", re.I), "shell eval of command substitution"),
    (re.compile(r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?(?:ba)?sh\b", re.I),
     "curl|sh dynamic execution"),
]
# a long unbroken base64-ish token (likely an encoded payload, not prose)
LONG_B64 = re.compile(r"[A-Za-z0-9+/]{120,}={0,2}")


def _agent_visible_files(root):
    """instruction.md + every text file under environment/ (what the agent sees)."""
    p = task_paths(root)
    files = []
    if os.path.isfile(p["instruction.md"]):
        files.append(("instruction.md", p["instruction.md"]))
    env = p["environment"]
    if os.path.isdir(env):
        for dirpath, _dirs, fnames in os.walk(env):
            for fn in fnames:
                full = os.path.join(dirpath, fn)
                files.append((os.path.relpath(full, root), full))
    return files


def check_task(name, root):
    out = []
    for rel, full in _agent_visible_files(root):
        text = read_text(full)
        if not text:
            continue

        m = PROMPT_INJECTION.search(text)
        if m:
            out.append(finding(name, "anti_cheat", FAIL, "prompt-injection",
                               detail=f"{rel} contains injection-like text "
                                      f"(`{m.group(0).strip()[:60]}`) that could steer the "
                                      "agent away from the task or leak the answer.",
                               location=rel,
                               fix="Remove the directive; agent-visible files must not tell the "
                                   "agent to ignore rules, reveal answers, or skip tests. (If the "
                                   "task is *about* prompt injection, confirm it's reviewed.)"))

        present = sorted({c for c in HIDDEN_CHARS if c in text})
        if present:
            names = ", ".join(HIDDEN_CHARS[c] for c in present)
            out.append(finding(name, "anti_cheat", FAIL, "hidden-unicode",
                               detail=f"{rel} contains hidden/bidi Unicode ({names}) — can hide "
                                      "or reorder text from a human reviewer.",
                               location=rel,
                               fix="Strip the zero-width/BOM/bidi control characters."))

        hit = next((label for rx, label in OBFUSCATION if rx.search(text)), None)
        if not hit and rel != "instruction.md" and LONG_B64.search(text):
            hit = "long base64-encoded blob"
        if hit:
            out.append(finding(name, "anti_cheat", FAIL, "obfuscated-payload",
                               detail=f"{rel} contains {hit} — obfuscated/dynamic execution is "
                                      "unauditable and a common cheat/host-escape vector.",
                               location=rel,
                               fix="Replace with explicit, readable commands/data; avoid "
                                   "decode-and-exec and curl|sh."))

    if not out:
        out.append(finding(name, "anti_cheat", PASS, "security-scan-clean"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks")
    ap.add_argument("--out", default="findings_security.json")
    args = ap.parse_args()
    findings = []
    tasks = discover_tasks(args.tasks)
    for name, root in tasks:
        findings.extend(check_task(name, root))
    n = emit(findings, args.out)
    warns = sum(1 for f in findings if f["severity"] == WARN)
    print(f"[security] {len(tasks)} tasks, {n} findings, {warns} WARN -> {args.out}")


if __name__ == "__main__":
    main()
