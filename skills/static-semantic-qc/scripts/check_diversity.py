#!/usr/bin/env python3
"""Dataset-level diversity constraints (deterministic, read-only).

REFLECTION-DELIVERY OPT-IN. The distribution limits below are Reflection's dataset
contract, not part of general OTS QC — run this only when prepping a Reflection
delivery. It is a standalone script, never part of the default run_static_qc gates.

Reflection's "Diversity" tab is a DATASET-level contract, not per-task — so this runs
once over a whole delivery and checks the distributional constraints against the
taxonomy labels recorded in each task.toml:

  - Category mix     no category > 20% of tasks; none < 5% (when the set is large
                     enough to assess); within a category, no subcategory > 20%.
  - Task objective   each task_objective label assigned should cover ≥ 10% of tasks.
  - Artifact type    each artifact_type label assigned should cover ≥ 5% of tasks.
  - Difficulty       avg_at_8 distribution (share that meets the ≤ 0.5 bar).

Findings are attributed to the synthetic task `__dataset__` (one SSOT row for the
delivery as a whole) and a human-readable `diversity-report.md` is written alongside.
Pairwise instruction / solve.sh / test_outputs.py SIMILARITY (< 0.90) lives in the
sibling `decontaminate.py`.

Small samples can't be assessed for under-representation, so the <5% / coverage-floor
checks only fire at or above `--min-tasks` (default 20); over-representation (> 20%)
is always reported. Over-representation is WARN; under-representation is WARN.

Usage:
    python check_diversity.py <tasks-dir> [--out findings_diversity.json] \
        [--report diversity-report.md] [--min-tasks 20]

Emits findings with area="dataset".
"""
import argparse
import os
import re
from collections import Counter, defaultdict

from common import FAIL, WARN, PASS, finding, emit, discover_tasks, task_paths, load_toml, get
# The REQUIRED label taxonomies (single source in check_metadata). We assess coverage
# against the FULL required set, not just labels that happen to appear in the batch, so
# a required objective/artifact the delivery never uses is caught (not silently absent).
from check_metadata import TASK_OBJECTIVES, ARTIFACT_TYPES

DATASET = "__dataset__"
CAT_MAX = 0.20      # no category > 20% of tasks
CAT_MIN = 0.05      # no category < 5% of tasks
SUBCAT_MAX = 0.20   # no subcategory > 20% of tasks
OBJ_MIN = 0.10      # each task_objective label >= 10% of tasks
ART_MIN = 0.05      # each artifact_type label >= 5% of tasks


def _as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def collect(tasks):
    rows = []
    for name, root in tasks:
        p = task_paths(root)
        if not os.path.isfile(p["task.toml"]):
            continue
        d = load_toml(p["task.toml"])
        rows.append({
            "task": name,
            "category": _norm(get(d, "metadata.category")) if get(d, "metadata.category") else "",
            "subcategory": _norm(get(d, "metadata.subcategory")) if get(d, "metadata.subcategory") else "",
            "objectives": [_norm(x) for x in _as_list(get(d, "metadata.task_objective")) if str(x).strip()],
            "artifacts": [_norm(x) for x in _as_list(get(d, "metadata.artifact_type")) if str(x).strip()],
            "avg_at_8": get(d, "metadata.avg_at_8"),
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks")
    ap.add_argument("--out", default="findings_diversity.json")
    ap.add_argument("--report", default=None)
    ap.add_argument("--min-tasks", type=int, default=20)
    args = ap.parse_args()

    tasks = discover_tasks(args.tasks)
    rows = collect(tasks)
    n = len(rows)
    findings = []

    # task-name uniqueness (spec §82 "must not collide with any other task name"). This is
    # an identity requirement, not a diversity distribution — it is a hard FAIL, not a floor/cap.
    name_counts = Counter(name for name, _root in tasks)
    for dupe, c in name_counts.items():
        if c > 1:
            findings.append(finding(DATASET, "dataset", FAIL, "duplicate-task-name",
                                    detail=f"task name '{dupe}' is used by {c} tasks — names must be unique.",
                                    location="dataset",
                                    fix="Rename the colliding tasks so every task name is unique."))
    L = ["# Terminal-Bench QC — Diversity Report", "", f"- **Tasks:** {n}", ""]

    if n == 0:
        print("[diversity] no tasks found")
        emit(findings, args.out)
        return

    big_enough = n >= args.min_tasks
    if not big_enough:
        L.append(f"> Sample < {args.min_tasks} tasks — under-representation / coverage "
                 "floors are not assessed (only over-representation is reported).")
        L.append("")

    # ---- category / subcategory ----
    cats = Counter(r["category"] for r in rows if r["category"])
    no_cat = sum(1 for r in rows if not r["category"])
    L.append("## Category distribution")
    L.append("")
    L.append("| Category | Tasks | % |")
    L.append("|---|---|---|")
    for cat, c in cats.most_common():
        L.append(f"| {cat} | {c} | {100*c/n:.1f}% |")
    if no_cat:
        L.append(f"| _(unset)_ | {no_cat} | {100*no_cat/n:.1f}% |")
    L.append("")
    for cat, c in cats.items():
        frac = c / n
        if frac > CAT_MAX:
            findings.append(finding(DATASET, "dataset", WARN, "category-over-represented",
                                    detail=f"category '{cat}' is {100*frac:.1f}% of tasks "
                                           f"(> {int(CAT_MAX*100)}% cap).",
                                    location="dataset",
                                    fix="Rebalance: add tasks in other categories or move some out."))
        elif big_enough and frac < CAT_MIN:
            findings.append(finding(DATASET, "dataset", WARN, "category-under-represented",
                                    detail=f"category '{cat}' is {100*frac:.1f}% of tasks "
                                           f"(< {int(CAT_MIN*100)}% floor).",
                                    location="dataset",
                                    fix="Add more tasks in this category or fold it into another."))

    # subcategory share of TOTAL tasks
    subs = Counter((r["category"], r["subcategory"]) for r in rows if r["subcategory"])
    over_sub = [(cat, sub, c) for (cat, sub), c in subs.items() if c / n > SUBCAT_MAX]
    for cat, sub, c in over_sub:
        findings.append(finding(DATASET, "dataset", WARN, "subcategory-over-represented",
                                detail=f"subcategory '{sub}' (in '{cat}') is {100*c/n:.1f}% of "
                                       f"tasks (> {int(SUBCAT_MAX*100)}% cap).",
                                location="dataset",
                                fix="Diversify subcategories within the category."))

    # ---- task objective coverage ----
    obj_cov = Counter()
    for r in rows:
        for o in set(r["objectives"]):
            obj_cov[o] += 1
    L.append("## Task-objective coverage")
    L.append("")
    L.append("| Objective | Tasks | % |")
    L.append("|---|---|---|")
    for o, c in obj_cov.most_common():
        L.append(f"| {o} | {c} | {100*c/n:.1f}% |")
    L.append("")
    if big_enough:
        # assess the FULL required taxonomy: a required objective that never appears
        # (0 tasks) is a coverage gap just as much as one below the floor.
        missing = sorted(o for o in TASK_OBJECTIVES if obj_cov.get(o, 0) == 0)
        under = sorted(o for o in TASK_OBJECTIVES if 0 < obj_cov.get(o, 0) / n < OBJ_MIN)
        if missing:
            findings.append(finding(DATASET, "dataset", FAIL, "task-objective-missing",
                                    detail=f"{len(missing)} required task_objective label(s) never "
                                           f"appear in the delivery: {missing}. The diversity bar "
                                           "requires every objective to be represented.",
                                    location="dataset",
                                    fix="Produce tasks for each missing objective, or confirm with "
                                        "the client that the objective is out of scope."))
        if under:
            findings.append(finding(DATASET, "dataset", FAIL, "task-objective-under-represented",
                                    detail=f"task_objective label(s) below the {int(OBJ_MIN*100)}% "
                                           f"coverage floor: {under}.",
                                    location="dataset",
                                    fix="Each objective label must cover >=10% of tasks; add tasks "
                                        "for these labels."))

    # ---- artifact type coverage ----
    art_cov = Counter()
    for r in rows:
        for a in set(r["artifacts"]):
            art_cov[a] += 1
    L.append("## Artifact-type coverage")
    L.append("")
    L.append("| Artifact | Tasks | % |")
    L.append("|---|---|---|")
    for a, c in art_cov.most_common():
        L.append(f"| {a} | {c} | {100*c/n:.1f}% |")
    L.append("")
    if big_enough:
        missing = sorted(a for a in ARTIFACT_TYPES if art_cov.get(a, 0) == 0)
        under = sorted(a for a in ARTIFACT_TYPES if 0 < art_cov.get(a, 0) / n < ART_MIN)
        if missing:
            findings.append(finding(DATASET, "dataset", FAIL, "artifact-type-missing",
                                    detail=f"{len(missing)} required artifact_type label(s) never "
                                           f"appear in the delivery: {missing}. The diversity bar "
                                           "requires every artifact type to be represented.",
                                    location="dataset",
                                    fix="Produce tasks for each missing artifact type, or confirm "
                                        "with the client that it is out of scope."))
        if under:
            findings.append(finding(DATASET, "dataset", FAIL, "artifact-type-under-represented",
                                    detail=f"artifact_type label(s) below the {int(ART_MIN*100)}% "
                                           f"coverage floor: {under}.",
                                    location="dataset",
                                    fix="Each artifact label must cover >=5% of tasks; add tasks "
                                        "for these labels."))

    # ---- difficulty (avg_at_8) ----
    scored = [r["avg_at_8"] for r in rows if isinstance(r["avg_at_8"], (int, float))]
    L.append("## Difficulty (avg@8)")
    L.append("")
    if scored:
        meets = sum(1 for v in scored if v <= 0.5)
        L.append(f"- Tasks with recorded avg_at_8: {len(scored)}/{n}")
        L.append(f"- Meet the ≤ 0.5 bar: {meets}/{len(scored)} "
                 f"({100*meets/len(scored):.1f}%)")
        L.append(f"- Mean avg@8: {sum(scored)/len(scored):.3f}")
    else:
        L.append("- No avg_at_8 recorded on any task — difficulty not benchmarked.")
    L.append("")

    if not findings:
        findings.append(finding(DATASET, "dataset", PASS, "diversity-ok",
                                detail=f"diversity constraints satisfied across {n} tasks."))

    n_out = emit(findings, args.out)
    report = args.report or os.path.join(os.path.dirname(os.path.abspath(args.out)),
                                         "diversity-report.md")
    with open(report, "w") as f:
        f.write("\n".join(L))
    warns = sum(1 for f in findings if f["severity"] == WARN)
    print(f"[diversity] {n} tasks: {n_out} findings, {warns} WARN -> {args.out}; report -> {report}")


if __name__ == "__main__":
    main()
