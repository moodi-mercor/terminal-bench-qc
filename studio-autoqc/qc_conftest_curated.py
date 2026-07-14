#!/usr/bin/env python3
"""Conftest-QC the curated experiment set before zipping.

For every task tree under --root, scan each tests/*.sh:
  - classify the pytest grader line(s):
      no-pytest    : no pytest invocation (bash asserts / custom grader) -> safe
      guarded      : pytest already has --noconftest or --confcutdir      -> safe
      vulnerable   : pytest grader with neither guard                     -> PATCH
  - patch vulnerable lines in place by inserting --noconftest (idempotent).
Also flags any conftest.py present in the tree (informational).

Prints a summary and writes conftest_qc_report.json. Exits non-zero if any tree
is still vulnerable after patching (i.e. it could not be safely patched).

Usage:
  python qc_conftest_curated.py --root _local/curated_experiment_set/tasks
  python qc_conftest_curated.py --root ... --check-only   # no writes, just report
"""
import argparse, glob, json, os, re, sys

INVOKE = re.compile(r'(python3?\s+-m\s+pytest|pytest)(\s)')

def is_grader(line):
    s = line.strip()
    if not s or s.startswith("#"):
        return False
    if any(x in s for x in ("apt", "pip ", "pip3", "import pytest", "command -v", "pytest --version")):
        return False
    return bool(re.match(r'(python3?\s+-m\s+pytest|pytest)\s', s))

def guarded(line):
    return ("--noconftest" in line) or ("--confcutdir" in line)

def classify_and_patch(text):
    """Returns (status, new_text, n_patched). status in no-pytest/guarded/vulnerable/patched."""
    lines = text.splitlines(keepends=True)
    grader_lines = [i for i, l in enumerate(lines) if is_grader(l)]
    if not grader_lines:
        return "no-pytest", text, 0
    vuln = [i for i in grader_lines if not guarded(lines[i])]
    if not vuln:
        return "guarded", text, 0
    for i in vuln:
        lines[i] = INVOKE.sub(r'\1 --noconftest\2', lines[i], count=1)
    return "patched", "".join(lines), len(vuln)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args()
    tasks = sorted(d for d in os.listdir(a.root) if os.path.isdir(f"{a.root}/{d}"))
    rep = {"no-pytest": [], "guarded": [], "patched": [], "vulnerable": [],
           "no-testsh": [], "stray_conftest": []}
    total_lines_patched = 0
    for t in tasks:
        td = f"{a.root}/{t}"
        # any conftest.py present in the source tree?
        for cf in glob.glob(f"{td}/**/conftest.py", recursive=True):
            rep["stray_conftest"].append(os.path.relpath(cf, a.root))
        shs = sorted(glob.glob(f"{td}/tests/*.sh")) or sorted(glob.glob(f"{td}/tests/test.sh"))
        if not shs:
            rep["no-testsh"].append(t); continue
        worst = "no-pytest"
        for sh in shs:
            txt = open(sh, errors="replace").read()
            status, new, n = classify_and_patch(txt)
            if status == "patched":
                if a.check_only:
                    worst = "vulnerable"          # would patch, but check-only
                else:
                    open(sh, "w").write(new)
                    total_lines_patched += n
                    worst = "patched" if worst != "vulnerable" else worst
            elif status == "guarded" and worst == "no-pytest":
                worst = "guarded"
        rep[worst].append(t)
    # final re-scan: after patching, nothing should be vulnerable
    still_vuln = []
    if not a.check_only:
        for t in tasks:
            for sh in glob.glob(f"{a.root}/{t}/tests/*.sh"):
                for l in open(sh, errors="replace").read().splitlines():
                    if is_grader(l) and not guarded(l):
                        still_vuln.append(t); break
    out = f"{os.path.dirname(a.root)}/conftest_qc_report.json"
    summary = {k: len(v) for k, v in rep.items()}
    summary["lines_patched"] = total_lines_patched
    summary["still_vulnerable_after_patch"] = len(set(still_vuln))
    json.dump({"summary": summary, **rep, "still_vulnerable": sorted(set(still_vuln))},
              open(out, "w"), indent=1)
    print("=== conftest QC ===")
    for k in ("no-pytest", "guarded", "patched", "vulnerable", "no-testsh"):
        print(f"  {k:12s}: {len(rep[k])}")
    print(f"  pytest lines patched: {total_lines_patched}")
    print(f"  stray conftest.py in trees: {len(rep['stray_conftest'])}")
    print(f"  STILL vulnerable after patch: {len(set(still_vuln))}")
    print(f"report -> {out}")
    if set(still_vuln):
        sys.exit(1)

if __name__ == "__main__":
    main()
