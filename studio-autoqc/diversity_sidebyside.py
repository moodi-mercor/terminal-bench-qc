#!/usr/bin/env python3
"""Diversity side-by-side: full OTS inventory vs difficulty-pass (640), for all
four Reflection axes. Outputs a clean markdown deliverable for the Diversity Tab.
"""
import json
from collections import Counter

OUT = "/Users/mahmoodmapara/Desktop/terminal-bench-qc/_local/ots_difficulty"
INV = json.load(open(f"{OUT}/inventory_tax.json"))          # 12,336 slug -> tax
EVAL = [json.loads(l) for l in open(f"{OUT}/compact_eval.jsonl")]


def avg(r, m):
    return r["models"].get(m, {}).get("avg")


passed = sorted(r["task_name"] for r in EVAL
                if (avg(r, "gpt") is not None and avg(r, "gpt") <= 0.5)
                or (avg(r, "opus") is not None and avg(r, "opus") <= 0.5))
full = sorted(INV)


def single(names, field):
    c = Counter(INV[n][field] for n in names if n in INV)
    return c


def multi(names, field):
    c = Counter()
    for n in names:
        for lab in (INV.get(n, {}).get(field) or []):
            c[lab] += 1
    return c


def table(P, title, cfull, cpass, nfull, npass, floor, cap=None):
    P(f"### {title}")
    P("")
    P("| Bucket | Overall (n={}) | Diff-pass (n={}) |".format(nfull, npass))
    P("|---|---|---|")
    keys = [k for k, _ in cpass.most_common()] + [k for k in cfull if k not in cpass]
    seen = set()
    for k in keys:
        if k in seen:
            continue
        seen.add(k)
        pf = 100*cfull.get(k, 0)/nfull
        pp = 100*cpass.get(k, 0)/npass
        def mark(p):
            f = ""
            if cap and p > cap:
                f = " ⚠️"
            elif p < floor:
                f = " ⚠️"
            return f"{p:.1f}%{f}"
        P(f"| {k} | {cfull.get(k,0)} ({mark(pf)}) | {cpass.get(k,0)} ({mark(pp)}) |")
    P("")


def main():
    nf, npa = len(full), len(passed)
    L = []; P = L.append
    P("# OTS Inventory — Diversity Readout (Reflection taxonomy)")
    P("")
    P(f"- **Overall dataset** = full tagged OTS inventory: **{nf}** tasks")
    P(f"- **Difficulty-pass** = avg@8 ≤ 0.5 on GPT-5.4 OR Opus-4.8: **{npa}** tasks")
    P("- ⚠️ marks a bucket outside the spec band (Category 5–20%; Subcat ≤20%; "
      "Task Objective ≥10%; Artifact ≥5%).")
    P("")
    table(P, "Category (target: each 5–20%)",
          single(full, "category"), single(passed, "category"), nf, npa, 5, 20)
    # subcategory: report top buckets only (149 distinct)
    sf = Counter(f"[{INV[n]['category']}] {INV[n]['subcategory']}" for n in full if n in INV)
    sp = Counter(f"[{INV[n]['category']}] {INV[n]['subcategory']}" for n in passed if n in INV)
    P("### Subcategory (target: ≤20% within dataset) — top 15 by diff-pass")
    P("")
    P(f"| Subcategory | Overall (n={nf}) | Diff-pass (n={npa}) |")
    P("|---|---|---|")
    for k, _ in sp.most_common(15):
        P(f"| {k} | {sf.get(k,0)} ({100*sf.get(k,0)/nf:.1f}%) | {sp.get(k,0)} ({100*sp.get(k,0)/npa:.1f}%) |")
    P(f"\n(distinct subcategories: overall {len(sf)}, diff-pass {len(sp)})")
    P("")
    table(P, "Task Objective (multi-label; target: each ≥10%)",
          multi(full, "task_objective"), multi(passed, "task_objective"), nf, npa, 10)
    table(P, "Artifact Type (multi-label; target: each ≥5%)",
          multi(full, "artifact_type"), multi(passed, "artifact_type"), nf, npa, 5)
    open(f"{OUT}/DIVERSITY_TAB.md", "w").write("\n".join(L))
    print("\n".join(L))
    print(f"\nsaved -> {OUT}/DIVERSITY_TAB.md")


if __name__ == "__main__":
    main()
