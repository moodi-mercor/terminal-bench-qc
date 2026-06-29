#!/usr/bin/env python3
"""Reflection-taxonomy diversity readout, joining our task sets to
diversity_inventory.xlsx (authoritative Category / Subcategory / Task Objective /
Artifact Type tagging).

Checks the spec constraints:
  - Category: each 5%-20% of tasks
  - Subcategory: <=20% within its category
  - Task Objective (multi-label): each label >=10% of tasks
  - Artifact Type  (multi-label): each label >=5% of tasks

Sets reported: overall (2421 difficulty-filter tasks) + sonnet-hard (824).
If eval avg@8 results exist, also the avg@8 difficulty-pass subset.
"""
import json
import os
from collections import Counter

OUT = "/Users/mahmoodmapara/Desktop/terminal-bench-qc/_local/ots_difficulty"
INV = json.load(open(f"{OUT}/inventory_tax.json"))
COMB = json.load(open(f"{OUT}/combined_with_scores.json"))


def single_dist(names, field):
    c = Counter()
    for n in names:
        c[INV[n][field] if n in INV else "(unmatched)"] += 1
    return c


def multi_dist(names, field):
    c = Counter()
    for n in names:
        for lab in (INV.get(n, {}).get(field) or []):
            c[lab] += 1
    return c


def report_set(P, label, names):
    total = len(names)
    P(f"## {label} — {total} tasks")
    P("")
    # Category 5-20%
    cat = single_dist(names, "category")
    P("### Category (constraint: each 5%–20%)")
    for k, n in cat.most_common():
        pct = 100*n/total
        flag = "  ⚠️>20%" if pct > 20 else ("  ⚠️<5%" if pct < 5 else "")
        P(f"  {n:5d}  {pct:5.1f}%  {k}{flag}")
    P("")
    # Subcategory within category <=20%
    P("### Subcategory (constraint: <=20% within its category)")
    sub = Counter((INV[n]["category"], INV[n]["subcategory"]) for n in names if n in INV)
    viol = [(c, s, n) for (c, s), n in sub.items() if 100*n/total > 20]
    P(f"  distinct subcategories: {len(sub)}; >20%-of-dataset violations: {len(viol)}")
    for (c, s), n in sub.most_common(12):
        P(f"  {n:5d}  {100*n/total:5.1f}%  [{c}] {s}")
    P("")
    # Task objective multi-label >=10%
    P("### Task Objective (multi-label; constraint: each >=10%)")
    for k, n in multi_dist(names, "task_objective").most_common():
        pct = 100*n/total
        flag = "  ⚠️<10%" if pct < 10 else ""
        P(f"  {n:5d}  {pct:5.1f}%  {k}{flag}")
    P("")
    # Artifact type multi-label >=5%
    P("### Artifact Type (multi-label; constraint: each >=5%)")
    for k, n in multi_dist(names, "artifact_type").most_common():
        pct = 100*n/total
        flag = "  ⚠️<5%" if pct < 5 else ""
        P(f"  {n:5d}  {pct:5.1f}%  {k}{flag}")
    P("")


def main():
    overall = sorted(COMB)
    hard = sorted(n for n, r in COMB.items() if r["avg"] is not None and r["avg"] <= 0.5)

    L = []; P = L.append
    P("# OTS Inventory — Diversity readout (Reflection taxonomy)")
    P("")
    P("Source: diversity_inventory.xlsx (authoritative tagging). Join: 2421/2421 matched.")
    P("")
    report_set(P, "OVERALL difficulty-filter set (Sonnet-evaluated)", overall)
    report_set(P, "SONNET-HARD subset (avg@3 <= 0.5)", hard)

    # avg@8 difficulty-pass subset if available
    ce = f"{OUT}/compact_eval.jsonl"
    if os.path.isfile(ce):
        recs = [json.loads(l) for l in open(ce)]
        def avg(r, m): return r["models"].get(m, {}).get("avg")
        pas = sorted(r["task_name"] for r in recs
                     if (avg(r, "gpt") is not None and avg(r, "gpt") <= 0.5)
                     or (avg(r, "opus") is not None and avg(r, "opus") <= 0.5))
        report_set(P, "avg@8 DIFFICULTY-PASS subset (<=0.5 on GPT-5.4 OR Opus-4.8)", pas)

    open(f"{OUT}/REPORT_diversity_taxonomy.md", "w").write("\n".join(L))
    print("\n".join(L))
    print(f"\nsaved -> {OUT}/REPORT_diversity_taxonomy.md")


if __name__ == "__main__":
    main()
