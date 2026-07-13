#!/usr/bin/env python3
"""The standard cross-layer defect gate — quarantine FAILs, promote the rest.

Every QC layer writes findings into ONE cumulative directory using the shared
schema (`common.py`):
  - Layer 1  static + semantic   (skills/static-semantic-qc)
  - Layer 2  trajectory          (skills/trajectory-audit)
  - Layer 3  behavioral          (skills/behavioral-qc)

`aggregate.py` merges that pool worst-verdict-wins, so a FAIL from ANY layer is
sticky and a later layer's PASS can never downgrade it. This gate reads the merged
verdict and partitions the task set so a defect caught in one layer never flows
downstream mislabeled as clean:

  quarantine.txt — tasks whose overall verdict is FAIL, tagged with the layer + the
                   check that caught them. They are pulled; they do NOT advance.
  promote.txt    — the surviving tasks (PASS, and WARN unless --quarantine-warn).
                   This is the input the NEXT layer runs on — which also saves cost,
                   since the expensive layers only see tasks that are still clean.

Run it after each layer lands its findings. Chain the next layer on the promoted set:

    python gate.py qc_out
    # Layer 3 behavioral, only on what survived 1+2:
    python ../skills/behavioral-qc/scripts/check_behavioral.py <tasks> \\
        --only "$(paste -sd, qc_out/promote.txt)" --execute

Usage:
    python gate.py <findings-dir> [--out-dir <dir>] [--quarantine-warn]
"""
import argparse
import json
import os
from collections import defaultdict

from common import FAIL, WARN, layer_of
import aggregate


def partition(findings_dir, quarantine_warn=False, require_complete=False,
              require_adversary=True, require_behavioral=True):
    """Return (quarantine, promote, by_layer) from a cumulative findings dir.

    quarantine: list of (task, [layers], [defect titles]) for blocked tasks.
    promote:    sorted list of task names that advance to the next layer.
    by_layer:   {layer: count} of quarantined tasks, for the summary.

    With require_complete, a task missing any evidence-backed QC dimension is
    quarantined as `qc-incomplete` (same completeness gate as aggregate.py).
    """
    findings = aggregate.load_findings(findings_dir)
    findings, _ = aggregate.reconcile(findings)
    if require_complete:
        bmap = {}
        bsig = os.path.join(findings_dir, "behavioral_signals.json")
        if os.path.isfile(bsig):
            try:
                bmap = json.load(open(bsig))
            except (OSError, ValueError):
                bmap = {}
        findings, _ = aggregate.inject_coverage(
            findings, bmap, require_adversary=require_adversary,
            require_behavioral=require_behavioral)
    tasks = aggregate.per_task(findings)
    rows = aggregate.verdicts(tasks)

    block = {FAIL, WARN} if quarantine_warn else {FAIL}
    quarantine, promote = [], []
    for task in sorted(rows):
        if rows[task]["overall"] in block:
            flagged = [f for area in tasks[task].values() for f in area
                       if f["severity"] in block and f["severity"] != "PASS"]
            layers = sorted({layer_of(f) for f in flagged})
            titles = sorted({f.get("title", "") for f in flagged if f.get("title")})
            quarantine.append((task, layers, titles))
        else:
            promote.append(task)

    by_layer = defaultdict(int)
    for _, layers, _ in quarantine:
        for lyr in layers:
            by_layer[lyr] += 1
    return quarantine, promote, by_layer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("findings_dir")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--quarantine-warn", action="store_true",
                    help="also quarantine WARN tasks (default: only FAIL blocks; WARN promotes)")
    ap.add_argument("--require-complete", action="store_true",
                    help="quarantine any task missing an evidence-backed QC dimension "
                         "(qc-incomplete) — six reviewer dims + adversary cheat-vector + the "
                         "two behavioral dims (oracle/no-op). Matches aggregate.py.")
    ap.add_argument("--no-require-adversary", action="store_true",
                    help="with --require-complete, exempt the adversary cheat-vector dimension.")
    ap.add_argument("--no-require-behavioral", action="store_true",
                    help="with --require-complete, exempt the two behavioral dimensions.")
    args = ap.parse_args()
    out_dir = args.out_dir or args.findings_dir
    os.makedirs(out_dir, exist_ok=True)

    quarantine, promote, by_layer = partition(
        args.findings_dir, args.quarantine_warn,
        require_complete=args.require_complete,
        require_adversary=not args.no_require_adversary,
        require_behavioral=not args.no_require_behavioral)

    qpath = os.path.join(out_dir, "quarantine.txt")
    with open(qpath, "w") as f:
        f.write("# task\tcaught-by-layer\tdefects (blocked — does not advance)\n")
        for task, layers, titles in quarantine:
            f.write(f"{task}\t{','.join(layers)}\t{'; '.join(titles)}\n")

    ppath = os.path.join(out_dir, "promote.txt")
    with open(ppath, "w") as f:
        f.write("\n".join(promote) + ("\n" if promote else ""))

    total = len(quarantine) + len(promote)
    print(f"[gate] {total} task(s): {len(quarantine)} quarantined, "
          f"{len(promote)} promoted -> {ppath}")
    if quarantine:
        print("  defects caught by layer: " +
              ", ".join(f"{k}={v}" for k, v in sorted(by_layer.items())))
        print(f"  quarantined task list -> {qpath}")


if __name__ == "__main__":
    main()
