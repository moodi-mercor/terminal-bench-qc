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

from common import (FAIL, WARN, PASS, finding, emit, read_text,
                    discover_tasks, task_paths)

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
        if body and not any(ln.upper().startswith("FROM") for ln in body):
            out.append(finding(
                name, "structure", FAIL, "dockerfile-no-base-image",
                detail="Dockerfile has no FROM instruction.",
                location="environment/Dockerfile",
                fix="Add a `FROM <base-image>` line."))
        if len(body) <= 1:
            out.append(finding(
                name, "structure", WARN, "dockerfile-trivial",
                detail="Dockerfile is a single line — verify the task needs no "
                       "dependencies or setup.",
                location="environment/Dockerfile",
                fix="If the task needs packages/data, add the required RUN/COPY steps."))

    # test.sh should reference a verifier (pytest or explicit checks)
    ts = p["test.sh"]
    if os.path.isfile(ts):
        t = read_text(ts)
        if t.strip() and "pytest" not in t and "test_outputs" not in t \
                and "python" not in t and "assert" not in t and "[ " not in t \
                and "[[" not in t:
            out.append(finding(
                name, "structure", WARN, "test-sh-no-visible-checks",
                detail="tests/test.sh contains no obvious verifier invocation "
                       "(pytest / python / shell assertions).",
                location="tests/test.sh",
                fix="Confirm test.sh actually runs the verifier."))

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
