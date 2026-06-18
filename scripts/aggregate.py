#!/usr/bin/env python3
"""Roll findings JSON from every gate into the SSOT outputs.

Reads all `*.json` finding arrays in a directory and produces:
  - review-ssot.csv          one row per task, per-area verdict + critical issues
  - review-ssot.md           per-task detailed findings (locations + fixes)
  - defect-distribution.md   dataset-level counts: defect rate, by area, by class

The defect-distribution report directly answers the action-item questions
("how many defects out of the dataset? what is the distribution?").

Usage:
    python aggregate.py <findings-dir> [--out-dir <dir>]
"""
import argparse
import csv
import glob
import json
import os
from collections import Counter, defaultdict

from common import PASS, WARN, FAIL, SEV_RANK, AREAS, worst

# columns shown in the CSV, grouped by QC part:
#   static (deterministic): structure, metadata, dockerfile, anti_cheat, dataset
#   semantic (sub-agent):   instructions, tests, solution
# (behavioral oracle/no-op is a separate delivery-stage gate, not run by this
#  skill; if external behavioral findings are dropped in, add "behavioral" here.)
COLS = ["structure", "metadata", "dockerfile", "anti_cheat", "dataset",
        "instructions", "tests", "solution"]


def load_findings(d):
    out = []
    for fp in sorted(glob.glob(os.path.join(d, "*.json"))):
        try:
            data = json.load(open(fp))
        except Exception as e:
            print(f"  ! skipping {fp}: {e}")
            continue
        if isinstance(data, dict):
            data = data.get("findings", [])
        for f in data:
            if isinstance(f, dict) and f.get("task") and f.get("area"):
                f.setdefault("severity", PASS)
                f.setdefault("title", "")
                out.append(f)
    return out


def per_task(findings):
    tasks = defaultdict(lambda: defaultdict(list))
    for f in findings:
        tasks[f["task"]][f["area"]].append(f)
    return tasks


def verdicts(tasks):
    rows = {}
    for task, areas in tasks.items():
        row = {}
        for col in COLS:
            sevs = [f["severity"] for f in areas.get(col, [])]
            row[col] = worst(sevs) if sevs else ""
        overall = worst([v for v in row.values() if v])
        crit = []
        for col in COLS:
            for f in areas.get(col, []):
                if f["severity"] == FAIL:
                    crit.append(f.get("title") or f.get("detail", "")[:60])
        row["overall"] = overall or PASS
        row["critical_issues"] = "; ".join(sorted(set(crit)))
        rows[task] = row
    return rows


def write_csv(rows, path):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["task"] + COLS + ["overall", "critical_issues"])
        for task in sorted(rows):
            r = rows[task]
            w.writerow([task] + [r[c] for c in COLS] +
                       [r["overall"], r["critical_issues"]])


def write_details(tasks, rows, path):
    lines = ["# Terminal-Bench QC — Detailed Findings", ""]
    for task in sorted(tasks):
        r = rows[task]
        lines.append(f"## Task: {task}")
        lines.append(f"**Overall: {r['overall']}**")
        lines.append("")
        for col in COLS:
            fs = [f for f in tasks[task].get(col, []) if f["severity"] != PASS]
            verdict = r[col] or "—"
            if not fs:
                if r[col]:
                    lines.append(f"### {col} — {verdict}")
                    lines.append("No issues.")
                    lines.append("")
                continue
            lines.append(f"### {col} — {verdict}")
            for f in fs:
                loc = f" (`{f['location']}`)" if f.get("location") else ""
                lines.append(f"- **[{f['severity']}] {f.get('title','')}**{loc}: "
                             f"{f.get('detail','')}")
                if f.get("fix"):
                    lines.append(f"  - _Fix:_ {f['fix']}")
            lines.append("")
        lines.append("---")
        lines.append("")
    with open(path, "w") as f:
        f.write("\n".join(lines))


def write_distribution(findings, rows, path):
    n_tasks = len(rows)
    fail_tasks = [t for t, r in rows.items() if r["overall"] == FAIL]
    warn_tasks = [t for t, r in rows.items() if r["overall"] == WARN]
    pass_tasks = [t for t, r in rows.items() if r["overall"] == PASS]

    # defect = FAIL-level finding; count by (area) and (area,title)
    by_area = Counter()
    by_class = Counter()
    warn_by_area = Counter()
    for f in findings:
        if f["severity"] == FAIL:
            by_area[f["area"]] += 1
            by_class[(f["area"], f.get("title", ""))] += 1
        elif f["severity"] == WARN:
            warn_by_area[f["area"]] += 1

    L = ["# Terminal-Bench QC — Defect Distribution", ""]
    L.append(f"- **Tasks reviewed:** {n_tasks}")
    if n_tasks:
        L.append(f"- **FAIL (defective):** {len(fail_tasks)} "
                 f"({100*len(fail_tasks)/n_tasks:.1f}%)")
        L.append(f"- **WARN (minor):** {len(warn_tasks)} "
                 f"({100*len(warn_tasks)/n_tasks:.1f}%)")
        L.append(f"- **PASS (clean):** {len(pass_tasks)} "
                 f"({100*len(pass_tasks)/n_tasks:.1f}%)")
    L.append("")

    L.append("## FAIL-level defects by area")
    L.append("")
    L.append("| Area | Defects |")
    L.append("|---|---|")
    for area in AREAS:
        if by_area.get(area):
            L.append(f"| {area} | {by_area[area]} |")
    L.append(f"| **total** | **{sum(by_area.values())}** |")
    L.append("")

    L.append("## FAIL-level defects by class (area / title)")
    L.append("")
    L.append("| Area | Defect class | Count |")
    L.append("|---|---|---|")
    for (area, title), cnt in by_class.most_common():
        L.append(f"| {area} | {title} | {cnt} |")
    L.append("")

    L.append("## WARN-level findings by area")
    L.append("")
    L.append("| Area | Warnings |")
    L.append("|---|---|")
    for area in AREAS:
        if warn_by_area.get(area):
            L.append(f"| {area} | {warn_by_area[area]} |")
    L.append("")

    if fail_tasks:
        L.append("## Defective tasks")
        L.append("")
        for t in sorted(fail_tasks):
            L.append(f"- `{t}` — {rows[t]['critical_issues']}")
        L.append("")

    with open(path, "w") as f:
        f.write("\n".join(L))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("findings_dir")
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()
    out_dir = args.out_dir or args.findings_dir
    os.makedirs(out_dir, exist_ok=True)

    findings = load_findings(args.findings_dir)
    tasks = per_task(findings)
    rows = verdicts(tasks)

    write_csv(rows, os.path.join(out_dir, "review-ssot.csv"))
    write_details(tasks, rows, os.path.join(out_dir, "review-ssot.md"))
    write_distribution(findings, rows, os.path.join(out_dir, "defect-distribution.md"))

    n_fail = sum(1 for r in rows.values() if r["overall"] == FAIL)
    print(f"[aggregate] {len(rows)} tasks, {n_fail} FAIL -> "
          f"{out_dir}/review-ssot.csv, review-ssot.md, defect-distribution.md")


if __name__ == "__main__":
    main()
