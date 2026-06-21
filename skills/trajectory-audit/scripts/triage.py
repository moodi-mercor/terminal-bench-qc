#!/usr/bin/env python3
"""Stage 2 — deterministic triage over pulled attempts. NO model in the loop.

This is where most of the value comes for free: the batch already ran, so the
scores (and, with --with-tests, the per-test pass/fail maps) are facts we just
read. Triage turns them into a ranked candidate list so the expensive per-
trajectory judge (Stage 3) only looks where a defect is plausible.

Signals (each emits a candidate finding — WARN, never an auto-verdict):

  - split-score task        some attempts pass, some fail (0 < pass_rate < 1).
                            The verifier and the task disagree across runs ->
                            prime spot for a brittle (false-neg) or weak
                            (false-pos) verifier. Judge the diffs.
  - all-fail task           pass_rate == 0 across >=2 attempts. Nobody passes:
                            suspect a broken oracle, impossible task, or env/
                            setup bug. (Could also be genuinely hard.)
  - verifier-suspect-test   ONE test fails in >= THRESHOLD of attempts, across
                            >= 2 models (needs --with-tests). This is the
                            "failure pattern points you" lever: a check almost
                            everything trips is likely too strict or env-
                            dependent. Go read that test.

Everything here is a CANDIDATE. Stage 3 (the judge sub-agent) confirms whether a
split is a real false-negative/false-positive and whether a high-fail test is
brittle vs just hard.

Usage:
  python triage.py attempts.jsonl --out-dir audit_out
  python triage.py detail.jsonl   --out-dir audit_out   # richer, if --with-tests was used
"""
import argparse
import json
import os
from collections import defaultdict

from common import finding, emit, WARN, PASS

# a "test fails across almost every attempt and model" → read it
HIGH_FAIL_THRESHOLD = 0.80
MIN_ATTEMPTS = 2          # need a few attempts before a pattern means anything


def load(path):
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def is_pass(score):
    try:
        return float(score) >= 1.0
    except (TypeError, ValueError):
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("attempts", help="JSONL from pull_batch.py")
    ap.add_argument("--out-dir", default="audit_out")
    ap.add_argument("--threshold", type=float, default=HIGH_FAIL_THRESHOLD)
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    rows = [r for r in load(args.attempts) if r.get("status") == "completed"]
    by_task = defaultdict(list)
    for r in rows:
        by_task[r.get("task_name") or r.get("task_id")].append(r)

    findings = []
    report = ["# Trajectory-audit triage\n",
              f"- attempts (completed): **{len(rows)}**",
              f"- tasks: **{len(by_task)}**\n"]
    candidates = []   # (priority, task, line) for the report

    for task, attempts in sorted(by_task.items()):
        n = len(attempts)
        models = sorted({a.get("model") for a in attempts if a.get("model")})
        passes = sum(1 for a in attempts if is_pass(a.get("score")))
        rate = passes / n if n else 0.0

        # ---- task-level pass-rate signals ----
        if n >= MIN_ATTEMPTS and 0 < passes < n:
            findings.append(finding(
                task, "tests", WARN, "split-score-task",
                detail=(f"{passes}/{n} attempts pass across models {models}. "
                        f"Verifier/task disagree across runs — judge the passing vs "
                        f"failing diffs for a brittle (false-neg) or weak (false-pos) verifier."),
                location=f"batch attempts ({n})",
                fix="Have the Stage-3 judge compare a passing and a failing diff against the spec."))
            candidates.append((1, task, f"- **{task}** — split {passes}/{n} pass, models={models}"))
        elif n >= MIN_ATTEMPTS and passes == 0:
            findings.append(finding(
                task, "tests", WARN, "all-fail-task",
                detail=(f"0/{n} attempts pass across models {models}. Suspect a broken "
                        f"oracle, impossible task, or env/setup bug — or genuinely hard."),
                location=f"batch attempts ({n})",
                fix="Run the oracle/no-op behavioral gate; if the oracle also fails, the task is broken."))
            candidates.append((0, task, f"- **{task}** — ALL FAIL 0/{n}, models={models}"))

        # ---- per-test failure pattern (needs --with-tests detail) ----
        test_fail = defaultdict(int)
        test_total = defaultdict(int)
        test_fail_models = defaultdict(set)
        have_tests = False
        for a in attempts:
            ts = a.get("test_statuses")
            if not ts:
                continue
            have_tests = True
            for check, status in ts.items():
                test_total[check] += 1
                if str(status).lower() != "pass":
                    test_fail[check] += 1
                    if a.get("model"):
                        test_fail_models[check].add(a["model"])
        if have_tests:
            for check, tot in sorted(test_total.items()):
                fr = test_fail[check] / tot if tot else 0
                if tot >= MIN_ATTEMPTS and fr >= args.threshold and len(test_fail_models[check]) >= 2:
                    findings.append(finding(
                        task, "tests", WARN, "verifier-suspect-test",
                        detail=(f"{check} fails {test_fail[check]}/{tot} attempts "
                                f"({fr:.0%}) across {len(test_fail_models[check])} models "
                                f"{sorted(test_fail_models[check])}. A check almost everything "
                                f"trips is likely too strict or environment-dependent — read it."),
                        location=f"tests/test_outputs.py::{check}",
                        fix="Read the check; confirm it rejects correct solutions, then make it outcome-based."))
                    candidates.append((0, task, f"- **{task}** — `{check}` fails {fr:.0%} across {len(test_fail_models[check])} models"))

    # ---- report ----
    findings_sorted = candidates
    report.append("## Candidates to judge (highest priority first)\n")
    if findings_sorted:
        for _, _, line in sorted(findings_sorted):
            report.append(line)
    else:
        report.append("_No split-score / all-fail / high-fail-test candidates found._")
    report.append("")

    for f in findings:  # cross-layer provenance: these are Layer 2 findings
        f.setdefault("layer", "trajectory")
    emit(findings, os.path.join(args.out_dir, "findings_trajectory.json"))
    with open(os.path.join(args.out_dir, "triage.md"), "w") as f:
        f.write("\n".join(report) + "\n")

    n_split = sum(1 for x in findings if x["title"] == "split-score-task")
    n_allfail = sum(1 for x in findings if x["title"] == "all-fail-task")
    n_test = sum(1 for x in findings if x["title"] == "verifier-suspect-test")
    print(f"Triage: {len(by_task)} tasks -> {len(findings)} candidate finding(s) "
          f"(split={n_split}, all-fail={n_allfail}, suspect-test={n_test})")
    print(f"  -> {args.out_dir}/findings_trajectory.json")
    print(f"  -> {args.out_dir}/triage.md")


if __name__ == "__main__":
    main()
