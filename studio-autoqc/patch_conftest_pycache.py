#!/usr/bin/env python3
"""Local patch set for the 300 best hard-passing tasks: fix conftest-plant +
pycache-residue WARNs, re-run the static gates to confirm they clear, emit diffs.

NO Studio writes. Pulls each flagged task into <out>/work/<task>/, edits files in
place, re-runs all gates, writes a unified diff per changed file, and a manifest.

Fixes applied:
  - conftest-plant-vulnerable -> insert `--noconftest` into pytest calls in tests/test.sh
  - pycache-residue-after-script-removal -> add `ENV PYTHONDONTWRITEBYTECODE=1` to environment/Dockerfile
leftover-generator is NOT auto-patched (removing a build source file is unsafe blind) — reported only.

Usage:
    python patch_conftest_pycache.py --csv ../_local/hard_passing_best300.csv \
        --findings ../_local/qc_best300/findings.csv --out ../_local/qc_best300_patch
"""
import argparse, csv, difflib, os, re, sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
L1 = os.path.normpath(os.path.join(HERE, "..", "skills", "static-semantic-qc", "scripts"))
SH = os.path.normpath(os.path.join(HERE, "..", "shared"))
sys.path[:0] = [L1, SH]

import studio_pull, aggregate
import check_structure, check_metadata, check_leakage, check_reward_hack
import check_env_fairness, check_portability, check_dockerfile, check_instructions
import check_verifier_defenses, check_security
from common import task_paths

GATES = [check_structure, check_metadata, check_leakage, check_reward_hack,
         check_env_fairness, check_portability, check_dockerfile, check_instructions,
         check_verifier_defenses, check_security]

# lines we must NEVER touch (import checks, pip/uv installs)
_SKIP_LINE = re.compile(r"import\s+pytest|\b(pip3?|uv)\b[^\n]*\binstall\b|\buninstall\b")
# `python -m pytest ...` — always a real test run
_PYTEST_M = re.compile(r"\bpython3?\s+-m\s+pytest\b")
# bare `pytest`/`py.test` only when at a command position (start / after ;&|(){ or && || $( ` or `! `)
_PYTEST_CMD = re.compile(r"(?:(?<=^)|(?<=[;&|(){])|(?<=&&)|(?<=\|\|)|(?<=\$\()|(?<=`)|(?<=! ))\s*(pytest|py\.test)\b")


def run_gates(name, root):
    findings = []
    for mod in GATES:
        try:
            findings += mod.check_task(name, root)
        except Exception as e:
            findings.append({"task": name, "severity": "WARN", "title": "qc-gate-error",
                             "detail": str(e)})
    findings, _ = aggregate.reconcile(findings)
    return {f.get("title") for f in findings if f.get("severity") in ("FAIL", "WARN")}


def fix_conftest(path):
    """Insert --noconftest as the first pytest arg on each pytest invocation line."""
    txt = open(path).read()
    if "--noconftest" in txt or "--confcutdir" in txt:
        return txt, txt, False
    out_lines, changed = [], False
    for ln in txt.splitlines(keepends=True):
        if ln.lstrip().startswith("#") or _SKIP_LINE.search(ln):
            out_lines.append(ln); continue
        new = _PYTEST_M.sub(lambda m: m.group(0) + " --noconftest", ln)
        new = _PYTEST_CMD.sub(lambda m: m.group(0) + " --noconftest", new)
        if new != ln:
            changed = True
            ln = new
        out_lines.append(ln)
    new_txt = "".join(out_lines)
    return txt, new_txt, changed


def fix_pycache(path):
    """Add `ENV PYTHONDONTWRITEBYTECODE=1` right after the first FROM line."""
    txt = open(path).read()
    if "PYTHONDONTWRITEBYTECODE" in txt:
        return txt, txt, False
    lines = txt.splitlines(keepends=True)
    out, inserted = [], False
    for ln in lines:
        out.append(ln)
        if not inserted and re.match(r"\s*FROM\s", ln, re.I):
            nl = "" if ln.endswith("\n") else "\n"
            if not ln.endswith("\n"):
                out[-1] = ln + "\n"
            out.append("ENV PYTHONDONTWRITEBYTECODE=1\n")
            inserted = True
    if not inserted:  # no FROM? prepend
        out.insert(0, "ENV PYTHONDONTWRITEBYTECODE=1\n")
        inserted = True
    return txt, "".join(out), True


def udiff(old, new, relpath):
    return "".join(difflib.unified_diff(old.splitlines(keepends=True),
                                        new.splitlines(keepends=True),
                                        fromfile="a/" + relpath, tofile="b/" + relpath))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--findings", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    work = os.path.join(args.out, "work"); os.makedirs(work, exist_ok=True)
    diffdir = os.path.join(args.out, "diffs"); os.makedirs(diffdir, exist_ok=True)

    # which tasks carry which flag
    flags = defaultdict(set)
    with open(args.findings) as f:
        for r in csv.DictReader(f):
            flags[r["task"]].add(r["title"])

    with open(args.csv) as f:
        rows = [r for r in csv.DictReader(f)]
    rkey = studio_pull.load_key()
    allt = {t["task_id"]: t for t in studio_pull.list_tasks(rkey, studio_pull.WORLD)}

    man = open(os.path.join(args.out, "manifest.csv"), "w")
    man.write("task,conftest_fixed,pycache_fixed,leftover_generator,flags_before,flags_after,cleared\n")
    ct = Counter()
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading
    lock = threading.Lock(); prog = {"n": 0}

    def handle(r):
        name = r["task_name"]; t = allt.get(r["task_id"])
        if not t:
            with lock: ct["unresolved"] += 1
            return
        troot = os.path.join(work, name)
        try:
            studio_pull.pull_task(rkey, t, work)
            before = run_gates(name, troot)
            p = task_paths(troot)
            diffs = []
            cf = pf = False
            if "conftest-plant-vulnerable" in flags.get(name, set()) and os.path.isfile(p["test.sh"]):
                old, new, ch = fix_conftest(p["test.sh"])
                if ch:
                    open(p["test.sh"], "w").write(new); cf = True
                    diffs.append(udiff(old, new, f"{name}/tests/test.sh"))
            if "pycache-residue-after-script-removal" in flags.get(name, set()) and os.path.isfile(p["Dockerfile"]):
                old, new, ch = fix_pycache(p["Dockerfile"])
                if ch:
                    open(p["Dockerfile"], "w").write(new); pf = True
                    diffs.append(udiff(old, new, f"{name}/environment/Dockerfile"))
            after = run_gates(name, troot)
            lg = "leftover-generator" in flags.get(name, set())
            target = {"conftest-plant-vulnerable", "pycache-residue-after-script-removal"}
            cleared = not (after & target)
            if diffs:
                open(os.path.join(diffdir, name + ".diff"), "w").write("\n".join(diffs))
            with lock:
                if cf: ct["conftest_fixed"] += 1
                if pf: ct["pycache_fixed"] += 1
                if lg: ct["leftover_generator"] += 1
                if cf or pf: ct["tasks_patched"] += 1
                man.write(f"{name},{cf},{pf},{lg},{len(before)},{len(after)},{cleared}\n"); man.flush()
        except Exception as e:
            with lock: ct["error"] += 1
            print(f"  [fail] {name}: {e}", flush=True)
        finally:
            with lock:
                prog["n"] += 1
                if prog["n"] % 50 == 0:
                    print(f"  {prog['n']}/{len(rows)} | {dict(ct)}", flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for _ in as_completed([ex.submit(handle, r) for r in rows]):
            pass
    man.close()
    print(f"\nDONE. {dict(ct)}")
    print(f"patched trees: {work}\ndiffs: {diffdir}\nmanifest: {os.path.join(args.out,'manifest.csv')}")


if __name__ == "__main__":
    main()
