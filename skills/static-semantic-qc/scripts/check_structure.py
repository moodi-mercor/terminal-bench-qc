#!/usr/bin/env python3
"""Layer 0 — structure / functional shape gate (deterministic, read-only).

Verifies every TB2 task has the required files, that they are non-empty, and
that the Dockerfile is non-trivial. This is the cheapest gate and the first one
the action items call for ("all files are present") before any semantic review.

Usage:
    python check_structure.py <tasks-dir> [--out findings_structure.json]

Emits findings with area="structure".
"""
import argparse
import os
import re
import subprocess

from common import (FAIL, WARN, PASS, finding, emit, read_text,
                    discover_tasks, task_paths)

# lowercase kebab-case, 1+ segments (TB/Harbor task-name convention)
KEBAB = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NAME_LEN = 50
# files/dirs that should never ship in a task package (caches, VCS, editor, venvs)
JUNK_NAMES = {".git", ".hg", ".svn", "__pycache__", ".venv", "venv", "node_modules",
              ".ds_store", ".idea", ".vscode", ".pytest_cache", ".mypy_cache",
              ".ipynb_checkpoints", ".tox", ".coverage"}
JUNK_SUFFIX = (".pyc", ".pyo", ".log", ".swp", ".egg-info", ".orig", ".bak")
# binary/non-text document assets that a non-multimodal model can't read (Reflection:
# task assets must be text-only / tool-parsable). Images/archives are often legit
# task inputs, so we only flag office/PDF document formats here.
NONTEXT_DOC = (".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx")
# only the known text files get a CRLF / encoding scan (cheap + avoids binary fixtures)
TEXT_KEYS = ("task.toml", "instruction.md", "Dockerfile", "test.sh",
             "solve.sh", "test_outputs.py")

# (relative-key, severity-if-missing, human label)
REQUIRED = [
    ("task.toml",       FAIL, "task.toml"),
    ("instruction.md",  FAIL, "instruction.md"),
    ("Dockerfile",      FAIL, "environment/Dockerfile"),
    ("test.sh",         FAIL, "tests/test.sh"),
    ("solve.sh",        FAIL, "solution/solve.sh"),
]
# present in most TB2 tasks but not strictly required (pure-bash verifiers exist)
RECOMMENDED = [
    ("test_outputs.py", WARN, "tests/test_outputs.py"),
]


def check_task(name, root):
    out = []
    p = task_paths(root)

    for key, sev, label in REQUIRED:
        path = p[key]
        if not os.path.isfile(path):
            out.append(finding(
                name, "structure", sev, "missing-required-file",
                detail=f"Required file `{label}` is absent.",
                location=label,
                fix=f"Add `{label}`; a TB2 task cannot build or verify without it."))
        elif not read_text(path).strip():
            out.append(finding(
                name, "structure", sev, "empty-required-file",
                detail=f"`{label}` exists but is empty.",
                location=label,
                fix=f"Populate `{label}`."))

    for key, sev, label in RECOMMENDED:
        path = p[key]
        if not os.path.isfile(path):
            out.append(finding(
                name, "structure", sev, "missing-recommended-file",
                detail=f"`{label}` is absent. Most TB2 tasks verify via a "
                       "pytest module; confirm tests/test.sh is self-contained.",
                location=label,
                fix=f"Add `{label}` unless tests/test.sh fully implements the verifier."))

    # Dockerfile triviality
    df = p["Dockerfile"]
    if os.path.isfile(df):
        body = [ln for ln in read_text(df).splitlines()
                if ln.strip() and not ln.strip().startswith("#")]
        if body and not any(ln.strip().upper().startswith("FROM") for ln in body):
            out.append(finding(
                name, "structure", FAIL, "dockerfile-no-base-image",
                detail="Dockerfile has no FROM instruction.",
                location="environment/Dockerfile",
                fix="Add a `FROM <base-image>` line."))
        if len(body) <= 1:
            out.append(finding(
                name, "structure", FAIL, "dockerfile-trivial",
                detail="Dockerfile is a single line — verify the task needs no "
                       "dependencies or setup.",
                location="environment/Dockerfile",
                fix="If the task needs packages/data, add the required RUN/COPY steps."))

    # ---- package identity / hygiene ----
    if name and not KEBAB.match(name):
        out.append(finding(
            name, "structure", FAIL, "task-name-not-kebab",
            detail=f"task name `{name}` is not lowercase kebab-case.",
            location="<task dir>",
            fix="Rename the task to lowercase-kebab-case (e.g. `fix-nginx-tls-config`)."))
    if name and len(name) > MAX_NAME_LEN:
        out.append(finding(
            name, "structure", FAIL, "task-name-too-long",
            detail=f"task name is {len(name)} chars (> {MAX_NAME_LEN}) — keep it concise.",
            location="<task dir>",
            fix="Shorten to a specific, meaningful name."))

    # unnecessary files (caches / VCS / editor / venvs / build droppings)
    junk = []
    for dirpath, dirs, files in os.walk(root):
        for d in list(dirs):
            if d.lower() in JUNK_NAMES:
                junk.append(os.path.relpath(os.path.join(dirpath, d), root))
                dirs.remove(d)  # don't descend into it
        for fn in files:
            low = fn.lower()
            if low in JUNK_NAMES or low.endswith(JUNK_SUFFIX):
                junk.append(os.path.relpath(os.path.join(dirpath, fn), root))
    if junk:
        shown = sorted(set(junk))[:6]
        out.append(finding(
            name, "structure", FAIL, "unnecessary-files",
            detail=f"package contains stale/cache/VCS files: {shown}"
                   f"{' …' if len(set(junk)) > 6 else ''}.",
            location="<task dir>",
            fix="Remove them and add a .gitignore/.dockerignore so they don't recur."))

    # CRLF line endings + non-text doc assets (text-only-assets requirement)
    crlf = []
    for key in TEXT_KEYS:
        path = p[key]
        if os.path.isfile(path):
            try:
                with open(path, "rb") as fh:
                    if b"\r\n" in fh.read():
                        crlf.append(key)
            except Exception:
                pass
    if crlf:
        out.append(finding(
            name, "structure", FAIL, "crlf-line-endings",
            detail=f"Windows CRLF line endings in {crlf} — task files must be Unix-LF "
                   "unless the task explicitly targets Windows.",
            location="<task dir>",
            fix="Convert to LF (`dos2unix` / `sed -i 's/\\r$//'`)."))

    nontext = []
    env_dir = p["environment"]
    if os.path.isdir(env_dir):
        for dirpath, _dirs, files in os.walk(env_dir):
            for fn in files:
                if fn.lower().endswith(NONTEXT_DOC):
                    nontext.append(os.path.relpath(os.path.join(dirpath, fn), root))
    if nontext:
        out.append(finding(
            name, "structure", FAIL, "non-text-asset",
            detail=f"agent-visible non-text document asset(s): {sorted(nontext)[:5]} — a "
                   "non-multimodal model can't read these.",
            location="environment/",
            fix="Convert to a text/tool-parsable form, or confirm the asset is readable "
                "without a multimodal model."))

    # test.sh should reference a verifier (pytest or explicit checks)
    ts = p["test.sh"]
    if os.path.isfile(ts):
        t = read_text(ts)
        if t.strip() and "pytest" not in t and "test_outputs" not in t \
                and "python" not in t and "assert" not in t and "[ " not in t \
                and "[[" not in t:
            out.append(finding(
                name, "structure", FAIL, "test-sh-no-visible-checks",
                detail="tests/test.sh contains no obvious verifier invocation "
                       "(pytest / python / shell assertions).",
                location="tests/test.sh",
                fix="Confirm test.sh actually runs the verifier."))

    # well-formed: python + shell files must be syntactically valid (spec §59)
    for key, label in (("test_outputs.py", "tests/test_outputs.py"),):
        path = p[key]
        if os.path.isfile(path):
            body = read_text(path)
            if body.strip():
                try:
                    compile(body, label, "exec")
                except SyntaxError as e:
                    out.append(finding(
                        name, "structure", FAIL, "python-syntax-error",
                        detail=f"{label} has a Python syntax error: {str(e)[:80]} — the verifier "
                               "cannot run.",
                        location=label, fix="Fix the syntax error."))
    for key, label in (("test.sh", "tests/test.sh"), ("solve.sh", "solution/solve.sh")):
        path = p[key]
        if os.path.isfile(path) and read_text(path).strip():
            r = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
            if r.returncode != 0:
                out.append(finding(
                    name, "structure", FAIL, "shell-syntax-error",
                    detail=f"{label} is not valid bash: {r.stderr.strip()[:80]}.",
                    location=label, fix="Fix the shell syntax error."))

    if not out:
        out.append(finding(name, "structure", PASS, "structure-ok"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks")
    ap.add_argument("--out", default="findings_structure.json")
    args = ap.parse_args()

    findings = []
    tasks = discover_tasks(args.tasks)
    for name, root in tasks:
        findings.extend(check_task(name, root))
    n = emit(findings, args.out)
    fails = sum(1 for f in findings if f["severity"] == FAIL)
    print(f"[structure] {len(tasks)} tasks, {n} findings, {fails} FAIL -> {args.out}")


if __name__ == "__main__":
    main()
