#!/usr/bin/env python3
"""Stage 2c — deterministic per-attempt checks over the pulled DIFFS. NO model.

triage.py reasons across attempts (split / all-fail / high-fail test). This
script reasons WITHIN a single attempt's diff + score, catching defects that a
cross-attempt view misses. All three signals are the cheapest false-positive /
harness-bug catches we have — pure string + arithmetic over data the batch
already produced. Needs the `--with-tests` detail file (diff + per-test map).

Signals (each emits a CANDIDATE finding — WARN, never an auto-verdict):

  - noop-pass               score >= 1 but the solution diff is empty / blank /
                            comment-only. The verifier passed a no-op: either a
                            weak (false-positive) verifier or the task is already
                            solved in the base image. (Could be a legitimately
                            empty task — the judge confirms.)
  - verifier-tampering      the diff WRITES to the verifier itself (tests/,
                            test_outputs.py, conftest.py, run-tests.sh, test.sh,
                            *_test.py, the grader). Reward tampering — the #1
                            named reward-hack category. Invisible to a score-only
                            view. (Could be a fixture the spec asked for.)
  - score-test-mismatch     the recorded score disagrees with the per-test map /
                            counts: score>=1 while a check is `fail` or
                            tests_failed>0, or score==0 while every check passed.
                            That's a SCORING/HARNESS bug, distinct from a bad test.

Everything here is a CANDIDATE. The Stage-3 judge confirms whether a no-op pass
is a real false-positive, whether a tests/ edit is a cheat or a legit fixture,
and whether a mismatch is a harness bug vs a metadata artifact.

Usage:
  python diff_signals.py detail.jsonl --out-dir audit_out   # needs --with-tests pull
"""
import argparse
import json
import os
import re

from common import finding, emit, WARN

# Installed-dependency / vendored test trees that show up in the filesystem
# snapshot but are NOT the task's verifier. Editing these is noise, not tampering.
DEP_DIR = re.compile(
    r"(^|/)(site-packages|dist-packages|node_modules|\.julia|\.cargo|\.rustup|"
    r"\.cache|\.venv|venv)/", re.IGNORECASE)

# The TB grader is always test_outputs.py; allow the harness wrapper scripts too.
# We confirm the real verifier basename per-task from the test_statuses keys and
# only fall back to this set when a task has no per-test map.
DEFAULT_VERIFIER_FILES = {"test_outputs.py", "run-tests.sh", "test.sh"}


def _verifier_files(test_statuses):
    """The verifier's own filename(s), read from the per-test map keys
    (e.g. 'test_outputs.py::test_check_1' -> 'test_outputs.py')."""
    files = {os.path.basename(k.split("::", 1)[0]) for k in (test_statuses or {})}
    files = {f for f in files if f}
    return files or set(DEFAULT_VERIFIER_FILES)

# a diff body line that carries real added content (not metadata / context / blank)
def _added_content_lines(diff):
    out = []
    for ln in diff.splitlines():
        if ln.startswith("+++") or ln.startswith("---"):
            continue
        if ln.startswith("+"):
            body = ln[1:].strip()
            if body and not body.startswith("#") and not body.startswith("//"):
                out.append(body)
    return out


def _changed_paths(diff):
    """Paths the diff writes to, from `+++ b/<path>` and `diff --git` headers."""
    paths = set()
    for ln in diff.splitlines():
        m = re.match(r"\+\+\+ b/(.+)", ln)
        if m:
            paths.add(m.group(1).strip())
        m = re.match(r"diff --git a/\S+ b/(\S+)", ln)
        if m:
            paths.add(m.group(1).strip())
    return paths


def is_pass(score):
    try:
        return float(score) >= 1.0
    except (TypeError, ValueError):
        return False


def load(path):
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("attempts", help="detail JSONL from pull_batch.py --with-tests")
    ap.add_argument("--out-dir", default="audit_out")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    rows = [r for r in load(args.attempts) if r.get("status") == "completed"]
    findings = []
    n_diff = 0

    for r in rows:
        task = r.get("task_name") or r.get("task_id")
        tid = r.get("trajectory_id")
        diff = r.get("diff")
        if diff is None:
            continue                       # no detail pulled for this attempt
        n_diff += 1
        passed = is_pass(r.get("score"))
        ts = r.get("test_statuses") or {}
        tfailed = r.get("tests_failed")
        tpassed = r.get("tests_passed")
        any_check_failed = any(str(v).lower() != "pass" for v in ts.values())
        all_checks_passed = bool(ts) and not any_check_failed

        # ---- noop-pass: passed on an empty / comment-only diff ----
        if passed and not _added_content_lines(diff):
            tc = r.get("tool_calls")
            tc_note = (f" The agent made {tc} tool call(s)." if tc is not None else "")
            findings.append(finding(
                task, "tests", WARN, "noop-pass",
                detail=(f"trajectory {tid} scored PASS with an empty / comment-only "
                        f"diff (no added content lines).{tc_note} The verifier accepts "
                        f"a no-op — weak/gameable verifier, or the task is pre-solved in "
                        f"the base image (a 0-tool-call pass measures nothing)."),
                location=f"trajectory {tid}",
                fix="Have the judge confirm; if real, make the verifier require the "
                    "produced artifact rather than passing by default."))

        # ---- verifier-tampering: the diff writes to the verifier itself ----
        # Only the TASK's own grader (basename from the test map) counts — not the
        # agent's scratch tests and not vendored/site-packages test suites.
        vfiles = _verifier_files(ts)
        bad = sorted(p for p in _changed_paths(diff)
                     if os.path.basename(p) in vfiles and not DEP_DIR.search(p))
        if bad:
            findings.append(finding(
                task, "tests", WARN, "verifier-tampering",
                detail=(f"trajectory {tid} (score={r.get('score')}) modifies "
                        f"verifier/test path(s): {bad}. Reward tampering if it "
                        f"weakened the check; could be a legit fixture the spec asked "
                        f"for — judge it."),
                location=f"trajectory {tid}: {', '.join(bad)}",
                fix="Judge whether the edit weakens the test; if so, make the verifier "
                    "read-only / restore it before grading."))

        # ---- score-test-mismatch: score disagrees with the per-test outcome ----
        mism = None
        if passed and (any_check_failed or (isinstance(tfailed, int) and tfailed > 0)):
            mism = (f"score={r.get('score')} (PASS) but tests_failed={tfailed} / "
                    f"a check is 'fail' in test_statuses")
        elif (not passed) and all_checks_passed and (isinstance(tfailed, int) and tfailed == 0):
            mism = (f"score={r.get('score')} (FAIL) but every check passed "
                    f"(tests_passed={tpassed}, tests_failed=0)")
        if mism:
            findings.append(finding(
                task, "tests", WARN, "score-test-mismatch",
                detail=(f"trajectory {tid}: {mism}. The recorded score disagrees with "
                        f"the per-test map — a scoring/aggregation (harness) bug, not "
                        f"necessarily a bad test."),
                location=f"trajectory {tid}",
                fix="Reconcile final_score with the per-test pass/fail map in the grader."))

    for f in findings:
        f.setdefault("layer", "trajectory")
    emit(findings, os.path.join(args.out_dir, "findings_diff_signals.json"))

    n_noop = sum(1 for x in findings if x["title"] == "noop-pass")
    n_tamper = sum(1 for x in findings if x["title"] == "verifier-tampering")
    n_mism = sum(1 for x in findings if x["title"] == "score-test-mismatch")
    print(f"Diff signals: {n_diff} attempt(s) with diffs -> {len(findings)} candidate(s) "
          f"(noop-pass={n_noop}, verifier-tampering={n_tamper}, score-test-mismatch={n_mism})")
    print(f"  -> {args.out_dir}/findings_diff_signals.json")


if __name__ == "__main__":
    main()
