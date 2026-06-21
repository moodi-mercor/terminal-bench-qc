#!/usr/bin/env python3
"""Deterministic brittle-verifier scanner — no LLM, no execution.

Reads a task's verifier (`tests/test_outputs.py` AND any helper scripts it
delegates to, e.g. `tests/verify.py`) and flags the mechanical patterns that make
a check reject genuinely-correct solutions — the false-negative class the
trajectory audit keeps surfacing.

It is a CHEAP PRIOR, not a verdict. Three design choices make it precise:

  1. Scan helpers too. TB checks often run an opaque grader (`verify.py`) and
     exact-match its sentinel; the real brittleness lives inside that helper.
     We scan every tests/*.py + *.sh and fold a helper's patterns into the check
     that invokes it. (+ an `opaque-grader` pattern for when the helper isn't
     fetchable.)

  2. Join with failure data (--fail-data). A brittle pattern on a check that
     every model PASSES is not a real problem — only patterns on the check that
     actually FAILS across models matter. This is what removes false alarms.

  3. Never silently miss. A check that fails across models but shows NO visible
     pattern (a logical contradiction, or a grader we couldn't read) is emitted
     as `escalate-oracle` — routed to the behavioral oracle run / LLM judge,
     which is the only thing that can resolve it. Honest about its own limits.

STRONG patterns (point at a fixed/hidden expected value):
  byte-exact-compare, hash-equality, hidden-oracle-truth, jq-deep-equal,
  opaque-grader (delegate to a helper + exact-match its sentinel)

Usage:
  python scan_verifiers.py --src audit_out/src --out-dir audit_out
  python scan_verifiers.py --src audit_out/src --out-dir audit_out --fail-data audit_out/detail50.jsonl
"""
import argparse
import base64
import json
import os
import re
from collections import defaultdict

from common import finding, emit, WARN

PATTERNS = {
    "byte-exact-compare": (re.compile(r"\bdiff\s+-q\b|\bdiff\s+/|\bcmp\s+(-s\s+)?/?\w"), "byte-exact file compare (diff/cmp)"),
    "hash-equality":      (re.compile(r"\b(sha256sum|md5sum|sha1sum|hexdigest|hashlib|sha256|md5)\b"), "hash equality (one byte differs -> fail)"),
    "hidden-oracle-truth":(re.compile(r"/tests/\.truth|/\.truth/|expected_[\w]*\.(json|txt|csv)|truth_[\w]*\.|\.golden\b|golden_[\w]*"), "compares to a hidden answer key (truth/expected/golden)"),
    "jq-deep-equal":      (re.compile(r"jq[^\n]*==[^\n]*|\$a\[0\]\s*==\s*\$b\[0\]"), "jq structural deep-equal vs an oracle json"),
}
# the generic harness scripts — NOT custom graders, never folded as helpers
HARNESS = {"test_outputs.py", "test.sh"}
# a check that runs a custom grader we COULDN'T read + exact-matches a sentinel
SENTINEL_EQ = re.compile(r"==\s*['\"][A-Z][A-Z_]{4,}['\"]")
FUNC_RE = re.compile(r"^def\s+(test\w+)\s*\(", re.M)


def decode_b64_blobs(text):
    extra = []
    for m in re.finditer(r"b64decode\(\s*['\"]([A-Za-z0-9+/=]+)['\"]", text):
        try:
            extra.append(base64.b64decode(m.group(1)).decode("utf-8", "replace"))
        except Exception:
            pass
    return text + "\n" + "\n".join(extra)


def split_funcs(text):
    marks = [(m.start(), m.group(1)) for m in FUNC_RE.finditer(text)]
    for i, (pos, name) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        yield name, text[pos:end]


def strong_hits(text):
    return [p for p, (rx, _) in PATTERNS.items() if rx.search(text)]


def load_failing_checks(fail_data):
    """task -> set of check names that FAIL across all completed attempts."""
    if not fail_data or not os.path.isfile(fail_data):
        return None
    rows = [json.loads(l) for l in open(fail_data) if l.strip()]
    by = defaultdict(list)
    for r in rows:
        if r.get("status") == "completed" and r.get("test_statuses"):
            by[r["task_name"]].append(r)
    out = {}
    for task, atts in by.items():
        checks = set()
        for a in atts:
            checks |= set(a["test_statuses"].keys())
        out[task] = {c.split("::")[-1] for c in checks
                     if all(str(a["test_statuses"].get(c)).lower() != "pass" for a in atts)}
    return out


def scan_task(task, task_dir, failing):
    """failing: set of failing check names, or None (no fail-data -> scan all)."""
    tdir = os.path.join(task_dir, "tests")
    main = os.path.join(tdir, "test_outputs.py")
    if not os.path.isfile(main):
        return []
    # CUSTOM grader scripts the test may delegate to (verify.py, *-ref.py, ...).
    # Exclude the generic harness (test_outputs.py / test.sh) — those aren't graders.
    helpers = {}
    for fn in os.listdir(tdir):
        if fn not in HARNESS and fn.endswith((".py", ".sh")):
            helpers[fn] = decode_b64_blobs(open(os.path.join(tdir, fn), errors="replace").read())

    findings = []
    raw = open(main, errors="replace").read()
    for name, body in split_funcs(raw):
        short = name
        # if joining with fail-data, only consider the checks that actually fail
        if failing is not None and short not in failing:
            continue
        decoded = decode_b64_blobs(body)
        hits = set(strong_hits(decoded))
        # fold in the STRONG patterns of any custom grader this check invokes BY NAME
        invoked = [h for h in helpers if h in decoded]
        for h in invoked:
            hits |= set(strong_hits(helpers[h]))
        # a custom grader we read but found no pattern in, gated by a sentinel,
        # is still opaque-brittle (the real logic is hidden inside it)
        if invoked and not hits and SENTINEL_EQ.search(decoded):
            hits.add("opaque-grader")

        if hits:
            mech = []
            for h in sorted(hits):
                if h == "opaque-grader":
                    mech.append(f"delegates to opaque custom grader ({','.join(invoked)}) "
                                "+ exact-matches its sentinel; real logic hidden inside it")
                elif h in PATTERNS:
                    mech.append(PATTERNS[h][1])
            findings.append(finding(
                task, "tests", WARN, "brittle-verifier-pattern",
                location=f"tests/test_outputs.py::{name}",
                detail=f"{name} gates on: " + "; ".join(mech)
                       + ("  [FAILS across all models]" if failing is not None else ""),
                fix="Confirm the matched value is fully specified by the instruction; if not, make it outcome-based. Oracle run confirms."))
        elif failing is not None:
            # fails across models but no visible brittle pattern -> can't decide statically
            findings.append(finding(
                task, "tests", WARN, "escalate-oracle",
                location=f"tests/test_outputs.py::{name}",
                detail=f"{name} fails across all models but shows NO brittle text pattern "
                       f"(possible logical contradiction or hidden grader). Mechanism not "
                       f"statically visible.",
                fix="Run the reference solution (behavioral oracle): if it also fails this check, the task is broken; else the check may be fair-but-hard."))
    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="tree of <task>/tests/*.py")
    ap.add_argument("--out-dir", default="audit_out")
    ap.add_argument("--fail-data", default="", help="detail JSONL (pull_batch --with-tests) to join on the FAILING check")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    failing_by_task = load_failing_checks(args.fail_data)
    all_findings = []
    for task in sorted(os.listdir(args.src)):
        tdir = os.path.join(args.src, task)
        if not os.path.isdir(tdir):
            continue
        failing = failing_by_task.get(task, set()) if failing_by_task is not None else None
        all_findings.extend(scan_task(task, tdir, failing))

    for f in all_findings:  # cross-layer provenance: these are Layer 2 findings
        f.setdefault("layer", "trajectory")
    emit(all_findings, os.path.join(args.out_dir, "findings_scan.json"))
    brittle = [f for f in all_findings if f["title"] == "brittle-verifier-pattern"]
    escal = [f for f in all_findings if f["title"] == "escalate-oracle"]
    report = ["# Brittle-verifier scan (deterministic, no LLM)\n",
              f"- brittle-pattern findings: **{len(brittle)}**",
              f"- escalate-to-oracle (fails-all, no visible pattern): **{len(escal)}**",
              f"- joined with failure data: **{'yes' if failing_by_task is not None else 'no'}**\n",
              "## Brittle (pattern visible on the failing check)\n"]
    for f in brittle:
        report.append(f"- ⚠️ **{f['task']}** `{f['location'].split('::')[-1]}` — {f['detail']}")
    report.append("\n## Escalate to oracle run (mechanism not statically visible)\n")
    for f in escal:
        report.append(f"- ❓ **{f['task']}** `{f['location'].split('::')[-1]}` — {f['detail']}")
    with open(os.path.join(args.out_dir, "scan.md"), "w") as fh:
        fh.write("\n".join(report) + "\n")

    print(f"Brittle (pattern): {len(brittle)}  |  Escalate-oracle: {len(escal)}  "
          f"|  join={'yes' if failing_by_task is not None else 'no'}")
    print(f"  -> {args.out_dir}/findings_scan.json")
    print(f"  -> {args.out_dir}/scan.md")


if __name__ == "__main__":
    main()
