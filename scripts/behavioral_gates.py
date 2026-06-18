#!/usr/bin/env python3
"""Layer 2 — behavioral gate runner: oracle=1 and no-op=0, 3x with saved logs.

This is THE most important gate (per the QC gate reference): almost every client
escalation traces to oracle/no-op not being run, or not run consistently with
logs. It requires `harbor` + Modal/Docker, so it does NOT run on a bare laptop —
run it in the delivery environment. It is a real, parameterized runner, not a
description.

For each task it runs:
  - oracle Nx   -> every run must score 1.0 (solve path works, verifier is fair)
  - no-op  Nx   -> every run must score 0.0 (tests require the fix; no reward leak)
Inconsistent scores across runs => flaky (FAIL). Logs are saved per run.

Reward-file isolation and the adversarial exploit pass need a frontier agent and
are described in behavioral-runbook.md (run them after this gate is green).

Usage:
    python behavioral_gates.py <tasks-dir> --env modal --runs 3 \
        --log-dir behavioral_logs --out findings_behavioral.json
"""
import argparse
import os
import re
import subprocess

from common import FAIL, WARN, PASS, finding, emit, discover_tasks

SCORE_RE = re.compile(r"Score:\s*([0-9]*\.?[0-9]+)")


def run_harbor(task_dir, env, agent, log_path):
    cmd = ["harbor", "run", "-p", task_dir, "-e", env, "-a", agent]
    with open(log_path, "w") as lf:
        lf.write("$ " + " ".join(cmd) + "\n\n")
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, timeout=3600)
            lf.write(proc.stdout)
            out = proc.stdout
        except FileNotFoundError:
            return None, "harbor not found on PATH"
        except subprocess.TimeoutExpired:
            return None, "timeout"
    scores = SCORE_RE.findall(out)
    if not scores:
        return None, "no Score line parsed"
    return float(scores[-1]), None


def gate_task(name, root, env, runs, log_dir):
    out = []
    tdir = os.path.join(log_dir, name)
    os.makedirs(tdir, exist_ok=True)

    oracle_scores, nop_scores = [], []
    for i in range(runs):
        s, err = run_harbor(root, env, "oracle", os.path.join(tdir, f"oracle_{i+1}.log"))
        if err:
            out.append(finding(name, "behavioral", FAIL, "oracle-run-error",
                               detail=f"oracle run {i+1} failed: {err}.",
                               location=f"{tdir}/oracle_{i+1}.log",
                               fix="Investigate the harbor/build error; the task must build and run."))
        else:
            oracle_scores.append(s)
    for i in range(runs):
        s, err = run_harbor(root, env, "nop", os.path.join(tdir, f"nop_{i+1}.log"))
        if err:
            out.append(finding(name, "behavioral", FAIL, "nop-run-error",
                               detail=f"no-op run {i+1} failed: {err}.",
                               location=f"{tdir}/nop_{i+1}.log",
                               fix="Investigate the harbor/build error."))
        else:
            nop_scores.append(s)

    # oracle must be 1.0 on every run
    if oracle_scores:
        if all(abs(s - 1.0) < 1e-9 for s in oracle_scores):
            out.append(finding(name, "behavioral", PASS, "oracle-passes",
                               detail=f"oracle scored 1.0 on {len(oracle_scores)}/{runs} runs ({env})."))
        elif len(set(oracle_scores)) > 1:
            out.append(finding(name, "behavioral", FAIL, "oracle-flaky",
                               detail=f"oracle scores inconsistent across runs: {oracle_scores} ({env}).",
                               location=f"{tdir}/",
                               fix="Remove non-determinism (fixed sleeps, unseeded randomness, "
                                   "network, service startup races). See T-7 in the review skill."))
        else:
            out.append(finding(name, "behavioral", FAIL, "oracle-fails",
                               detail=f"oracle scored {oracle_scores[0]} (expected 1.0) ({env}). "
                                      "Broken solve path or a verifier that rejects the correct answer.",
                               location=f"{tdir}/oracle_1.log",
                               fix="Fix solution/solve.sh or the unfair verifier until oracle=1.0."))

    # no-op must be 0.0 on every run
    if nop_scores:
        if all(abs(s) < 1e-9 for s in nop_scores):
            out.append(finding(name, "behavioral", PASS, "nop-fails",
                               detail=f"no-op scored 0.0 on {len(nop_scores)}/{runs} runs ({env})."))
        else:
            out.append(finding(name, "behavioral", FAIL, "nop-passes",
                               detail=f"no-op scored {nop_scores} (expected 0.0) ({env}). "
                                      "Tests pass without the fix — non-verifying tests or a reward leak.",
                               location=f"{tdir}/nop_1.log",
                               fix="Tighten the vacuously-passing checks; ensure each test requires "
                                   "the agent's work. Add an assertion per leaked check, re-run oracle."))
    if not out:
        out.append(finding(name, "behavioral", WARN, "behavioral-not-run",
                           detail="No behavioral results captured.",
                           fix="Run with harbor available."))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks")
    ap.add_argument("--env", default="modal", choices=["modal", "docker"])
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--log-dir", default="behavioral_logs")
    ap.add_argument("--out", default="findings_behavioral.json")
    args = ap.parse_args()
    os.makedirs(args.log_dir, exist_ok=True)

    findings = []
    for name, root in discover_tasks(args.tasks):
        print(f"[behavioral] {name} ...")
        findings.extend(gate_task(name, root, args.env, args.runs, args.log_dir))
    n = emit(findings, args.out)
    fails = sum(1 for f in findings if f["severity"] == FAIL)
    print(f"[behavioral] {n} findings, {fails} FAIL -> {args.out} (logs in {args.log_dir}/)")


if __name__ == "__main__":
    main()
