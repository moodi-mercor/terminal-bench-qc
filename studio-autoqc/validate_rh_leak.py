#!/usr/bin/env python3
"""Validate 'remove-leaked-answer-from-image' labels: is the verifier's expected
answer literally sitting in an agent-readable environment file?

For each task under <tasks_dir>/<name>/:
  - extract distinctive expected tokens from tests/test_outputs.py
    (quoted strings len>=8, long ints >=4 digits, hex/hash-like >=12)
  - grep them across AGENT-VISIBLE files (environment/** + root files),
    excluding tests/, solution/, task.toml, instruction.md, fix_report.json
  - a verbatim hit => confirmed verbatim leak (HIGH precision).

A miss does NOT clear the task (answer may be generated into the image at build
time), so this is a FLOOR on the true-positive rate, not a refutation.

Usage: python validate_rh_leak.py <tasks_dir> <name_list>
"""
import os
import re
import sys

# tokens that are too generic to count as a leak signal
STOP = {"true", "false", "none", "null", "error", "success", "status", "result",
        "output", "expected", "actual", "value", "assert", "return", "python3"}


def expected_tokens(test_src):
    toks = set()
    # quoted string literals
    for m in re.finditer(r"""(['"])(.*?)\1""", test_src):
        s = m.group(2).strip()
        if len(s) >= 8 and not s.startswith(("/", "http", "test_", "{", "%")) \
           and s.lower() not in STOP and re.search(r"[A-Za-z0-9]", s) \
           and " " not in s[:1]:
            toks.add(s)
    # long integers / hashes / hex
    for m in re.finditer(r"\b([0-9a-fA-F]{12,}|\d{4,})\b", test_src):
        toks.add(m.group(1))
    # keep the most distinctive (longest) to limit grep noise
    return sorted(toks, key=len, reverse=True)[:60]


def visible_files(tdir):
    out = []
    for dp, dns, fns in os.walk(tdir):
        rel = os.path.relpath(dp, tdir)
        top = rel.split(os.sep)[0]
        if top in ("tests", "solution", "__pycache__", ".git"):
            dns[:] = []
            continue
        for f in fns:
            if f in ("task.toml", "instruction.md", "fix_report.json"):
                continue
            out.append(os.path.join(dp, f))
    return out


def scan(tdir):
    tf = os.path.join(tdir, "tests", "test_outputs.py")
    if not os.path.isfile(tf):
        return None
    toks = expected_tokens(open(tf, errors="replace").read())
    if not toks:
        return {"leak": False, "hits": [], "ntok": 0}
    blobs = []
    for fp in visible_files(tdir):
        try:
            blobs.append(open(fp, errors="replace").read())
        except Exception:
            pass
    blob = "\n".join(blobs)
    hits = [t for t in toks if t in blob]
    return {"leak": bool(hits), "hits": hits[:5], "ntok": len(toks)}


def main():
    tasks_dir, name_list = sys.argv[1], sys.argv[2]
    names = [l.strip() for l in open(name_list) if l.strip()]
    leak = nomatch = notest = 0
    rows = []
    for n in names:
        r = scan(os.path.join(tasks_dir, n))
        if r is None:
            notest += 1; rows.append((n, "NO-TEST", "")); continue
        if r["leak"]:
            leak += 1; rows.append((n, "LEAK", ", ".join(str(h)[:30] for h in r["hits"])))
        else:
            nomatch += 1; rows.append((n, "no-verbatim-hit", f"{r['ntok']} tokens checked"))
    for n, v, d in rows:
        print(f"  {v:16} {n}  {d}")
    tot = leak + nomatch + notest
    print(f"\nLEAK(verbatim): {leak}/{tot} | no-verbatim-hit: {nomatch} | no-test: {notest}")
    print("(verbatim LEAK = high-precision confirm; no-hit is inconclusive, "
          "build-time-generated leaks won't show statically)")


if __name__ == "__main__":
    main()
