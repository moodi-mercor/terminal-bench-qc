#!/usr/bin/env python3
"""Run every deterministic (static) QC gate over a tasks tree and aggregate.

This is the one-command entry point for Layers 0-1. It runs the structure,
metadata, leakage, reward-hack, and env-fairness detectors, writes their findings
JSON into an output directory, then produces the SSOT + defect-distribution reports.

Semantic review (Layer 2) is run separately by dispatching sub-agents — see
references/semantic-review-prompt.md and the SKILL. (Behavioral oracle/no-op is
a delivery-stage gate, out of scope for this skill.)

Usage:
    python run_static_qc.py <tasks-dir> [--out-dir qc_out]
"""
import argparse
import os

import check_structure
import check_metadata
import check_leakage
import check_reward_hack
import check_env_fairness
import check_portability
import check_dockerfile
import check_instructions
import check_verifier_defenses
import aggregate
from common import discover_tasks, emit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks")
    ap.add_argument("--out-dir", default="qc_out")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    tasks = discover_tasks(args.tasks)
    print(f"Discovered {len(tasks)} task(s) under {args.tasks}")
    if not tasks:
        print("No tasks found (looked for directories containing task.toml).")
        return

    gates = [
        ("structure", check_structure, "findings_structure.json"),
        ("metadata", check_metadata, "findings_metadata.json"),
        ("leakage", check_leakage, "findings_leakage.json"),
        ("reward_hack", check_reward_hack, "findings_reward_hack.json"),
        ("env_fairness", check_env_fairness, "findings_env_fairness.json"),
        ("portability", check_portability, "findings_portability.json"),
        ("dockerfile", check_dockerfile, "findings_dockerfile.json"),
        ("instructions", check_instructions, "findings_instructions.json"),
        ("verifier_defenses", check_verifier_defenses, "findings_verifier_defenses.json"),
    ]
    for label, mod, fname in gates:
        findings = []
        for name, root in tasks:
            findings.extend(mod.check_task(name, root))
        out = os.path.join(args.out_dir, fname)
        emit(findings, out)
        n_fail = sum(1 for f in findings if f["severity"] == "FAIL")
        print(f"  [{label}] {len(findings)} findings, {n_fail} FAIL -> {fname}")

    print("Aggregating...")
    import sys
    sys.argv = ["aggregate", args.out_dir, "--out-dir", args.out_dir]
    aggregate.main()


if __name__ == "__main__":
    main()
