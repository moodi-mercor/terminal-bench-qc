#!/usr/bin/env python3
"""Deterministic fix for `unseeded-randomness-in-verifier`.

Injects a module-level RNG seed at the top of tests/test_outputs.py (after any
shebang / coding line / module docstring / __future__ imports). Seeding can only
make grading MORE deterministic — a general oracle still passes, a no-op still
fails — so this is safe to apply without re-running the oracle. It satisfies the
detector's `random.seed(...) / np.random.seed(...)` requirement and the Reflection
determinism spec.

Usage: python fix_unseeded_seed.py <delivery_dir> <task_list_file>
"""
import ast
import os
import sys

SEED_BLOCK = (
    "# QC determinism fix: seed all RNGs so the verifier is reproducible across runs.\n"
    "import random as _qc_random\n"
    "_qc_random.seed(0)\n"
    "try:\n"
    "    import numpy as _qc_numpy\n"
    "    _qc_numpy.random.seed(0)\n"
    "except Exception:\n"
    "    pass\n"
)


def insert_point(src):
    """1-based line AFTER which to insert (past shebang/coding/docstring/__future__)."""
    lines = src.splitlines()
    idx = 0
    # shebang + coding comment
    while idx < len(lines) and (lines[idx].startswith("#!")
                                or "coding" in lines[idx][:40] and lines[idx].lstrip().startswith("#")):
        idx += 1
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return idx
    body = tree.body
    if body and isinstance(body[0], ast.Expr) and isinstance(
            getattr(body[0], "value", None), ast.Constant) and isinstance(body[0].value.value, str):
        idx = max(idx, body[0].end_lineno)
    for node in body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            idx = max(idx, node.end_lineno)
    return idx


def fix(path):
    src = open(path).read()
    if not src.strip():
        return False
    if "_qc_random.seed" in src:
        return False  # already fixed
    ip = insert_point(src)
    lines = src.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    block = ("\n" if ip > 0 else "") + SEED_BLOCK + "\n"
    new = "".join(lines[:ip]) + block + "".join(lines[ip:])
    # must still parse
    try:
        ast.parse(new)
    except SyntaxError:
        return False
    open(path, "w").write(new)
    return True


def main():
    delivery, listfile = sys.argv[1], sys.argv[2]
    tasks = [t.strip() for t in open(listfile) if t.strip()]
    n_fixed = n_skip = n_missing = 0
    for t in tasks:
        p = os.path.join(delivery, t, "tests", "test_outputs.py")
        if not os.path.isfile(p):
            n_missing += 1
            continue
        if fix(p):
            n_fixed += 1
        else:
            n_skip += 1
    print(f"fixed={n_fixed} skipped={n_skip} missing_test_outputs={n_missing} of {len(tasks)}")


if __name__ == "__main__":
    main()
