#!/usr/bin/env python3
"""Delivery / diversity report — the distribution view clients expect at handoff.

NVIDIA asks for difficulty bucketing + task-category distribution + language
distribution at delivery; Reflection asks for the static, model-independent
diversity levers. This reads a tasks tree (and, if present, the QC SSOT) and emits
a single markdown report with those distributions plus a defect summary.

Pure metadata parsing — no task execution, milliseconds per task.

Usage:
    python delivery_report.py <tasks-dir> [--ssot qc_out/review-ssot.csv] \\
        [--out qc_out/delivery-report.md]

Emits a markdown report (and prints the path).
"""
import argparse
import csv
import os
from collections import Counter

from common import discover_tasks, task_paths, load_toml, get, read_text


def _bucket_len(n):
    if n < 400:
        return "short (<400 chars)"
    if n < 1200:
        return "medium (400-1200)"
    return "long (>1200)"


def _bar(count, total, width=24):
    fill = int(round(width * count / total)) if total else 0
    return "█" * fill + "·" * (width - fill)


def _dist_table(title, counter, total):
    lines = [f"### {title}", "", "| value | count | share | |", "|---|---|---|---|"]
    for val, cnt in counter.most_common():
        lines.append(f"| {val or '(unset)'} | {cnt} | {100*cnt/total:.0f}% | `{_bar(cnt,total)}` |")
    lines.append("")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks")
    ap.add_argument("--ssot", default=None, help="review-ssot.csv to summarise defects")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    tasks = discover_tasks(args.tasks)
    n = len(tasks)
    difficulty, category, langtag = Counter(), Counter(), Counter()
    instr_len, deps = Counter(), Counter()
    for name, root in tasks:
        d = load_toml(task_paths(root)["task.toml"])
        difficulty[str(get(d, "metadata.difficulty") or "").lower()] += 1
        category[str(get(d, "metadata.category") or "")] += 1
        tags = get(d, "metadata.tags") or []
        if not isinstance(tags, list):
            tags = [tags]
        for t in tags:
            langtag[str(t)] += 1
        instr_len[_bucket_len(len(read_text(task_paths(root)["instruction.md"])))] += 1
        df = read_text(task_paths(root)["Dockerfile"]).lower()
        for dep in ("python", "node", "go ", "rust", "java", "postgres", "redis",
                    "nginx", "spark", "cuda"):
            if dep.strip() in df:
                deps[dep.strip()] += 1

    L = [f"# Delivery report — {n} tasks", ""]
    L += _dist_table("Difficulty distribution", difficulty, n)
    L += _dist_table("Category distribution", category, n)
    L += _dist_table("Top tags / languages", Counter(dict(langtag.most_common(15))), n)
    L += _dist_table("Instruction length (diversity lever)", instr_len, n)
    if deps:
        L += _dist_table("Environment tech (from Dockerfile)", deps, n)

    # diversity flags
    L += ["### Diversity flags", ""]
    top_cat = category.most_common(1)[0] if category else ("", 0)
    if n and top_cat[1] / n > 0.4:
        L.append(f"- ⚠️ category **{top_cat[0]}** is {100*top_cat[1]/n:.0f}% of the set "
                 "— concentrated, low diversity.")
    if difficulty.get("hard", 0) == 0:
        L.append("- ⚠️ no `hard` tasks — difficulty bar may be too low.")
    if len(category) < max(3, n // 20):
        L.append(f"- ⚠️ only {len(category)} distinct categories across {n} tasks.")
    if len(L) and L[-1] == "":
        pass
    L.append("")

    # defect summary from the SSOT, if provided
    if args.ssot and os.path.isfile(args.ssot):
        rows = list(csv.DictReader(open(args.ssot)))
        verd = Counter(r.get("overall", "") for r in rows)
        tot = len(rows) or 1
        L += ["### QC verdict summary", "",
              f"- FAIL: {verd.get('FAIL',0)} ({100*verd.get('FAIL',0)/tot:.0f}%)",
              f"- WARN: {verd.get('WARN',0)} ({100*verd.get('WARN',0)/tot:.0f}%)",
              f"- PASS: {verd.get('PASS',0)} ({100*verd.get('PASS',0)/tot:.0f}%)", ""]

    out = args.out or os.path.join(os.path.dirname(args.ssot) if args.ssot else ".",
                                   "delivery-report.md")
    with open(out, "w") as f:
        f.write("\n".join(L))
    print(f"[delivery_report] {n} tasks -> {out}")


if __name__ == "__main__":
    main()
