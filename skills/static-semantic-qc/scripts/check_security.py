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


# Binary / archive / media extensions: their raw bytes decode to garbage that
# spuriously matches the base64 and bidi-Unicode patterns (a .gz IS a long binary
# blob; a .zip contains RLO/ZWJ bytes). These are not agent-readable *text*, so the
# obfuscation / hidden-unicode / injection scans do not apply.
BINARY_EXT = {
    ".gz", ".tgz", ".bz2", ".xz", ".zst", ".zip", ".tar", ".7z", ".rar",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".pdf", ".svgz",
    ".so", ".o", ".a", ".bin", ".dat", ".pyc", ".pyd", ".wasm", ".class", ".jar",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp3", ".mp4", ".wav", ".flac", ".avi", ".mov", ".mkv", ".ogg",
    ".parquet", ".pkl", ".pickle", ".npy", ".npz", ".h5", ".hdf5", ".pt", ".pth",
    ".onnx", ".db", ".sqlite", ".sqlite3", ".xlsx", ".docx", ".pptx", ".feather", ".arrow",
}


def _is_binary(full):
    """True if the file is a known-binary extension or its bytes look non-textual."""
    if os.path.splitext(full)[1].lower() in BINARY_EXT:
        return True
    try:
        with open(full, "rb") as f:
            chunk = f.read(4096)
    except Exception:
        return True
    if not chunk:
        return False
    if b"\x00" in chunk:  # NUL byte => binary
        return True
    # high fraction of bytes outside the printable/whitespace range => binary
    nontext = sum(b < 9 or (13 < b < 32) for b in chunk)
    return nontext / len(chunk) > 0.10


def _agent_visible_files(root):
    """instruction.md + every text file under environment/ (what the agent sees).

    Binary/archive/media files are skipped: they are not agent-readable text, and
    scanning their raw bytes produces false obfuscation / hidden-unicode hits.
    """
    p = task_paths(root)
    files = []
    if os.path.isfile(p["instruction.md"]):
        files.append(("instruction.md", p["instruction.md"]))
    env = p["environment"]
    if os.path.isdir(env):
        for dirpath, _dirs, fnames in os.walk(env):
            for fn in fnames:
                full = os.path.join(dirpath, fn)
                if _is_binary(full):
                    continue
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

        # curl|sh AND base64-decode are spec-allowed at *build* time (Dockerfile RUN):
        # "Tools installed via curl | sh install during build." Only eval/exec of
        # decoded data (a real dynamic-exec vector) is blocking inside the Dockerfile.
        is_dockerfile = os.path.basename(rel) == "Dockerfile"
        hit = None
        for rx, label in OBFUSCATION:
            if not rx.search(text):
                continue
            if is_dockerfile and label in ("curl|sh dynamic execution",
                                           "base64 decode of an inline blob"):
                continue  # build-time install/curl|sh is permitted
            hit = label
            break
        # The bare long-base64 heuristic only applies to EXECUTABLE scripts — data
        # files (.b64/.hex/.jsonl/.txt/.csv/.md/...) legitimately hold encoded data.
        is_script = os.path.splitext(rel)[1].lower() in (".sh", ".bash", ".py", ".zsh")
        if not hit and is_script and LONG_B64.search(text):
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
