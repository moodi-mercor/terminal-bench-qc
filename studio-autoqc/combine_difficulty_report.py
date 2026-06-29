#!/usr/bin/env python3
"""Combine the old + new (rerun) Sonnet difficulty-filter exports into one
per-task table and emit a Difficulty + Diversity readout.

  - rerun (new batch) supersedes old for the 1,502 re-run task_names
  - Sonnet score = reward.txt result in trajectory_output.score (0..1)
  - Sonnet avg = mean of completed-run scores (avg@3)
  - Difficulty (Sonnet proxy): a task is HARD/keep if avg <= 0.5

NOTE: the *production* difficulty criterion is avg@8 over GPT-5.4 / Opus-4.8
(not yet run). This is the Sonnet pre-filter readout.

Usage: python combine_difficulty_report.py
"""
import json
import os
import statistics
from collections import Counter

OUT = "/Users/mahmoodmapara/Desktop/terminal-bench-qc/_local/ots_difficulty"
OLD = f"{OUT}/compact_old.jsonl"
NEW = f"{OUT}/compact_new.jsonl"
THRESH = 0.5
DIMS = [("category", "Category"), ("subcategory", "Subcategory"),
        ("operation_type", "Task Objective"), ("domain", "Domain"),
        ("language", "Language")]


def load(fp):
    d = {}
    for line in open(fp):
        line = line.strip()
        if line:
            r = json.loads(line)
            d[r["task_name"]] = r
    return d


def summarize(rec):
    scores = rec.get("scores") or []
    n_done = sum(1 for s in (rec.get("statuses") or []) if s == "completed")
    rec["n_completed"] = n_done
    rec["n_scored"] = len(scores)
    rec["avg"] = (sum(scores) / len(scores)) if scores else None
    return rec


def dist(records, key):
    c = Counter()
    for r in records:
        v = (r.get("custom_fields") or {}).get(key)
        if isinstance(v, list):
            v = v[0] if v else None
        c[v if v not in (None, "") else "(none)"] += 1
    return c


def fmt_dist(c, total, top=None):
    items = c.most_common()
    if top:
        items = items[:top]
    lines = []
    for k, n in items:
        lines.append(f"  {n:5d}  {100*n/total:5.1f}%  {k}")
    return "\n".join(lines)


def main():
    old = load(OLD)
    new = load(NEW)
    rerun = set(new)
    combined = {}
    for tn, r in old.items():
        if tn not in rerun:
            combined[tn] = {**summarize(r), "src": "old"}
    for tn, r in new.items():
        combined[tn] = {**summarize(r), "src": "new"}

    recs = list(combined.values())
    total = len(recs)

    scored = [r for r in recs if r["avg"] is not None]
    unscored = [r for r in recs if r["avg"] is None]
    hard = [r for r in scored if r["avg"] <= THRESH]
    easy = [r for r in scored if r["avg"] > THRESH]

    # avg@3 buckets
    bucket = Counter()
    for r in scored:
        bucket[round(r["avg"], 4)] += 1

    json.dump(combined, open(f"{OUT}/combined_with_scores.json", "w"), indent=2)
    # csv for convenience
    with open(f"{OUT}/combined_tasks.csv", "w") as f:
        f.write("task_name,src,n_completed,n_scored,sonnet_avg,sonnet_hard,"
                "category,subcategory,task_objective,domain,language,difficulty_label\n")
        for tn in sorted(combined):
            r = combined[tn]
            cf = r.get("custom_fields") or {}
            def g(k):
                v = cf.get(k)
                if isinstance(v, list):
                    v = v[0] if v else ""
                return str(v or "").replace(",", ";")
            avg = "" if r["avg"] is None else f"{r['avg']:.4f}"
            hd = "" if r["avg"] is None else ("hard" if r["avg"] <= THRESH else "easy")
            f.write(f"{tn},{r['src']},{r['n_completed']},{r['n_scored']},{avg},{hd},"
                    f"{g('category')},{g('subcategory')},{g('operation_type')},"
                    f"{g('domain')},{g('language')},{g('difficulty')}\n")

    L = []
    P = L.append
    P("# OTS Inventory — Sonnet Difficulty-Filter Readout (combined old + new rerun)")
    P("")
    P(f"Source: claude-sonnet-4-6, Terminus, avg@3, world=Canonical Tasks")
    P(f"  old batch  batch_bdd45...  {len(old)} tasks")
    P(f"  new rerun  batch_469e...   {len(new)} tasks (supersede old)")
    P(f"  combined unique tasks      **{total}**")
    P("")
    P("## Completeness")
    P(f"- Tasks with >=1 scored run : **{len(scored)}** ({100*len(scored)/total:.1f}%)")
    P(f"- Tasks with NO scored run  : **{len(unscored)}**")
    nfull = sum(1 for r in scored if r["n_scored"] == 3)
    P(f"- Tasks with full 3/3 runs  : {nfull}")
    P("")
    P("## Difficulty (Sonnet avg@3 proxy; HARD = avg <= 0.5)")
    P(f"- **HARD / keep**  (avg<=0.5): **{len(hard)}**  ({100*len(hard)/len(scored):.1f}% of scored)")
    P(f"- EASY / drop      (avg>0.5) : **{len(easy)}**  ({100*len(easy)/len(scored):.1f}% of scored)")
    if scored:
        avgs = [r["avg"] for r in scored]
        P(f"- mean avg={statistics.mean(avgs):.3f}  median={statistics.median(avgs):.3f}")
    P("")
    P("  avg@3 distribution:")
    for v in sorted(bucket):
        P(f"    avg={v:<6}  {bucket[v]:5d} tasks")
    P("")
    P("> NOTE: production difficulty = avg@8 over GPT-5.4 / Opus-4.8 (not yet run).")
    P("> This Sonnet avg@3 is the cheap pre-filter signal only.")
    P("")
    P("## Diversity distributions")
    P("")
    for key, label in DIMS:
        cA = dist(recs, key)
        cH = dist(hard, key)
        P(f"### {label}  ({len(cA)} distinct)")
        P("")
        P("**Overall (all {} tasks):**".format(total))
        P(fmt_dist(cA, total, top=25))
        P("")
        P("**Sonnet-HARD subset ({} tasks):**".format(len(hard)))
        P(fmt_dist(cH, max(len(hard), 1), top=25))
        P("")

    report = "\n".join(L)
    open(f"{OUT}/REPORT_difficulty_diversity.md", "w").write(report)
    print(report[:2600])
    print(f"\n... saved -> {OUT}/REPORT_difficulty_diversity.md")
    print(f"          -> {OUT}/combined_tasks.csv")
    print(f"          -> {OUT}/combined_with_scores.json")


if __name__ == "__main__":
    main()
