#!/usr/bin/env python3
"""Run every deterministic (static) QC gate over a tasks tree and aggregate.

This is the one-command entry point for the static half of Layer 1. It runs all
eleven static gates (structure, metadata, leakage, reward-hack, env-fairness,
portability, dockerfile, instructions, verifier-defenses, security, test-hygiene),
writes their findings JSON into an output directory, then produces the SSOT +
defect-distribution reports.

The semantic half of Layer 1 (reviewer + adversary) is run separately by
dispatching sub-agents — see QC_GUIDE.md and this skill's SKILL.md. Trajectory
(Layer 2) and behavioral (Layer 3) are separate sibling skills; all layers emit the
same finding schema and roll up through shared/aggregate.py + shared/gate.py.

Usage:
    python run_static_qc.py <tasks-dir> [--out-dir qc_out]
"""
import argparse
import os
import sys

# the cross-layer contract (aggregate + canonical schema) lives in shared/
sys.path.insert(0, os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "shared")))

import check_structure
import check_metadata
import check_leakage
import check_reward_hack
import check_env_fairness
import check_portability
import check_dockerfile
import check_instructions
import check_verifier_defenses
import check_security
import check_test_hygiene
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
        ("security", check_security, "findings_security.json"),
        ("test_hygiene", check_test_hygiene, "findings_test_hygiene.json"),
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
    sys.argv = ["aggregate", args.out_dir, "--out-dir", args.out_dir]
    aggregate.main()


if __name__ == "__main__":
    main()
