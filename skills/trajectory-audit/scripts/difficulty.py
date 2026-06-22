#!/usr/bin/env python3
"""Stage 2b — EMPIRICAL difficulty (avg@8) from the batch scores. NO model in the loop.

Reflection's difficulty bar is **avg@8 ≤ 0.5** on a frontier model (Opus 4.8 / GPT-5.4)
run with Terminus-2. The static Layer-1 gate (`check_metadata.py:avg-at-8-too-easy`)
only *reads* the `avg_at_8` recorded in `task.toml` — it trusts the number. This stage
*measures* it from the real rollouts: for a (task, model) pair, the mean `final_score`
over its attempts **is** the empirical avg@N. So it catches the case the static gate
can't: a task whose recorded difficulty is fabricated or stale.

Two signals (deterministic — like triage, no model in the loop):

  - difficulty-too-easy   the frontier model's empirical pass rate > 0.5. FAIL when
                          measured on an approved model (Opus 4.8 / GPT-5.4) with ≥ 8
                          attempts (the methodology's exact bar); WARN otherwise
                          (fewer attempts or a non-approved model — weaker evidence).
  - avg-at-8-mismatch     (needs --tasks-dir) the recorded `avg_at_8` disagrees with the
                          empirical rate by more than the tolerance — the metadata is
                          stale/wrong. WARN.

Usage:
  python difficulty.py attempts.jsonl --out-dir audit_out
  python difficulty.py attempts.jsonl --out-dir audit_out --tasks-dir <task-trees>
"""
import argparse
import json
import os
import re
from collections import defaultdict

from common import (finding, emit, WARN, FAIL, PASS,
                    discover_tasks, task_paths, load_toml, get)

AVG_AT_8_MAX = 0.5
MIN_ATTEMPTS_FAIL = 8     # the methodology measures over 8 attempts
MIN_ATTEMPTS = 2          # need a few before a rate means anything
MISMATCH_TOL = 0.25       # recorded-vs-empirical gap that counts as a mismatch
# the approved difficulty models (normalized); see Reflection's Difficulty tab
APPROVED_MODELS = {"gpt 5 4", "opus 4 8", "claude opus 4 8",
                   "claude opus 4 8 20", "gpt5 4"}


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def _approved(model):
    n = _norm(model)
    return any(n == a or n.startswith(a) for a in APPROVED_MODELS)


def load(path):
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def score(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def recorded_avg(tasks_dir):
    """task_name -> recorded avg_at_8 (float) from each task.toml, if a dir is given."""
    out = {}
    if not tasks_dir:
        return out
    for name, root in discover_tasks(tasks_dir):
        d = load_toml(task_paths(root)["task.toml"])
        v = get(d, "metadata.avg_at_8")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[name] = float(v)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("attempts", help="JSONL from pull_batch.py")
    ap.add_argument("--out-dir", default="audit_out")
    ap.add_argument("--tasks-dir", default="", help="task trees to read recorded avg_at_8 from")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    rows = [r for r in load(args.attempts) if r.get("status") == "completed"]
    # task -> model -> [scores]
    by = defaultdict(lambda: defaultdict(list))
    for r in rows:
        s = score(r.get("score"))
        if s is None:
            continue
        by[r.get("task_name") or r.get("task_id")][r.get("model") or "unknown"].append(s)

    rec = recorded_avg(args.tasks_dir)
    findings = []
    report = ["# Trajectory-audit — empirical difficulty (avg@N)\n",
              f"- tasks scored: **{len(by)}**",
              (f"- recorded avg_at_8 available for: **{len(rec)}**" if args.tasks_dir
               else "- recorded avg_at_8: _not compared (no --tasks-dir)_"), ""]
    report.append("| task | model | n | empirical avg@N | recorded | verdict |")
    report.append("|---|---|---|---|---|---|")

    for task in sorted(by):
        models = by[task]
        # choose the difficulty-bearing group: prefer an approved frontier model with
        # the most attempts; else the model with the most attempts overall.
        approved = {m: v for m, v in models.items() if _approved(m)}
        pool = approved or models
        model = max(pool, key=lambda m: len(pool[m]))
        scores = pool[model]
        n = len(scores)
        rate = sum(scores) / n if n else 0.0
        rec_v = rec.get(task)
        verdict = "ok"

        if n >= MIN_ATTEMPTS and rate > AVG_AT_8_MAX:
            hard_bar = _approved(model) and n >= MIN_ATTEMPTS_FAIL
            sev = FAIL if hard_bar else WARN
            verdict = "TOO EASY (FAIL)" if hard_bar else "too easy (WARN)"
            findings.append(finding(
                task, "metadata", sev, "difficulty-too-easy",
                detail=(f"empirical avg@{n} = {rate:.2f} on `{model}` (> {AVG_AT_8_MAX}); "
                        + ("measured on an approved frontier model with ≥8 attempts — the "
                           "task is too easy and fails the difficulty bar."
                           if hard_bar else
                           "weaker evidence (non-approved model or <8 attempts) — confirm "
                           "with an Opus-4.8/GPT-5.4 × Terminus-2 × 8 run.")),
                location=f"batch attempts ({n} on {model})",
                fix="Make the task harder or replace it; re-benchmark avg@8 on the frontier model.",
                layer="trajectory"))

        if rec_v is not None and n >= MIN_ATTEMPTS and abs(rec_v - rate) > MISMATCH_TOL:
            findings.append(finding(
                task, "metadata", WARN, "avg-at-8-mismatch",
                detail=(f"recorded avg_at_8 = {rec_v:.2f} but empirical avg@{n} = {rate:.2f} on "
                        f"`{model}` (gap {abs(rec_v - rate):.2f} > {MISMATCH_TOL}) — the recorded "
                        "difficulty is stale or wrong."
                        + (" Recorded claims it's hard but it actually passes often."
                           if rec_v <= AVG_AT_8_MAX < rate else "")),
                location="task.toml [metadata] avg_at_8",
                fix="Re-benchmark and update avg_at_8 to the measured value.",
                layer="trajectory"))
            verdict = (verdict + " + MISMATCH") if verdict != "ok" else "MISMATCH"

        report.append(f"| {task} | {model} | {n} | {rate:.2f} | "
                      f"{rec_v if rec_v is not None else '—'} | {verdict} |")

    if not findings:
        findings.append(finding("__difficulty__", "metadata", PASS, "difficulty-ok",
                                detail=f"no task exceeds avg@N {AVG_AT_8_MAX} across {len(by)} tasks.",
                                layer="trajectory"))

    emit(findings, os.path.join(args.out_dir, "findings_difficulty.json"))
    with open(os.path.join(args.out_dir, "difficulty.md"), "w") as f:
        f.write("\n".join(report) + "\n")

    fails = sum(1 for x in findings if x["severity"] == FAIL)
    warns = sum(1 for x in findings if x["severity"] == WARN)
    print(f"Difficulty: {len(by)} tasks -> {len(findings)} finding(s) "
          f"({fails} FAIL too-easy, {warns} WARN)")
    print(f"  -> {args.out_dir}/findings_difficulty.json")
    print(f"  -> {args.out_dir}/difficulty.md")


if __name__ == "__main__":
    main()
