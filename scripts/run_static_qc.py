#!/usr/bin/env python3
"""Run every deterministic (static) QC gate over a tasks tree and aggregate.

This is the one-command entry point for Layers 0-1. It runs the structure,
metadata, and leakage detectors, writes their findings JSON into an output
directory, then produces the SSOT + defect-distribution reports.

Behavioral gates (oracle/no-op/adversarial, Layer 2) and semantic review
(Layer 3) are run separately — see scripts/behavioral_gates.sh and the SKILL.

Usage:
    python run_static_qc.py <tasks-dir> [--out-dir qc_out]
"""
import argparse
import os

import check_structure
import check_metadata
import check_leakage
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
