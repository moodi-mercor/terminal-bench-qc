#!/usr/bin/env python3
"""Generate the conftest-plant fix (--noconftest) for the vulnerable pytest verifiers.

~9,021 OTS tasks share an IDENTICAL pytest grader line in tests/test.sh with no
--noconftest guard, so a root agent can plant /app/conftest.py (skip-all
pytest_collection_modifyitems) -> pytest exits 0 -> reward=1, defeating the verifier.
Validated: `--noconftest` ignores the planted file and restores correct grading.

This produces the PATCHED test.sh per task into _local/conftest_fix/<name>/test.sh
(local only — review/diff before any upload). It does NOT write to Studio.

The fix inserts `--noconftest` immediately after the pytest subcommand on the grader line:
  python3 -m pytest /app/test_outputs.py ...   ->   python3 -m pytest --noconftest /app/test_outputs.py ...

Usage:
  python fix_conftest.py                 # generate patched test.sh locally + a diff sample
"""
import glob
import json
import os
import re

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
OUT = f"{ROOT}/_local/conftest_fix"
INVOKE = re.compile(r'^(\s*)(python3?\s+-m\s+pytest|pytest)(\s)', re.M)


def cached_testsh():
    paths = {}
    for p in glob.glob(f"{ROOT}/_local/*/src/*/tests/test.sh") + glob.glob(f"{ROOT}/_local/qc_best300/**/test.sh", recursive=True):
        name = os.path.basename(os.path.dirname(os.path.dirname(p)))
        paths.setdefault(name, p)
    return paths


def is_grader(line):
    s = line.strip()
    if s.startswith("#") or "apt" in s or "pip" in s or "import pytest" in s or "command -v" in s:
        return False
    return bool(re.match(r'(python3?\s+-m\s+pytest|pytest)\s', s))


def fix_text(src):
    """Insert --noconftest into the FIRST real pytest grader line lacking it. Returns (new, changed)."""
    out, changed = [], False
    for line in src.splitlines(keepends=True):
        if not changed and is_grader(line) and "--noconftest" not in line and "--confcutdir" not in line:
            line = INVOKE.sub(r'\1\2 --noconftest\3', line, count=1)
            changed = True
        out.append(line)
    return "".join(out), changed


def main():
    os.makedirs(OUT, exist_ok=True)
    paths = cached_testsh()
    fixed = skipped = 0
    sample = None
    fixedlist = []
    for name, p in paths.items():
        src = open(p, errors="replace").read()
        new, changed = fix_text(src)
        if not changed:
            skipped += 1
            continue
        d = f"{OUT}/{name}"
        os.makedirs(d, exist_ok=True)
        open(f"{d}/test.sh", "w").write(new)
        fixed += 1
        fixedlist.append(name)
        if sample is None:
            ob = next(l for l in src.splitlines() if is_grader(l))
            nb = next(l for l in new.splitlines() if "--noconftest" in l)
            sample = (name, ob.strip(), nb.strip())
    json.dump(fixedlist, open(f"{OUT}/fixed_tasks.json", "w"))
    print(f"patched {fixed} test.sh -> {OUT}/<name>/test.sh ; skipped {skipped} (already guarded / no grader)")
    if sample:
        print(f"\nsample diff [{sample[0]}]:\n  - {sample[1]}\n  + {sample[2]}")


if __name__ == "__main__":
    main()
