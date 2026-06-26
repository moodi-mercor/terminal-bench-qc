#!/usr/bin/env python3
"""Roll findings JSON from every gate into the SSOT outputs.

Reads all `*.json` finding arrays in a directory and produces:
  - review-ssot.csv          one row per task, per-area verdict + critical issues
  - defects.csv              one row per flagged finding: task, layer, area, severity,
                             defect, location, REASON (why it failed), fix
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

from common import PASS, WARN, FAIL, AREAS, worst, layer_of

# columns shown in the CSV, one per finding area, grouped by QC layer:
#   Layer 1 static (deterministic): structure, metadata, dockerfile, anti_cheat, dataset
#   Layer 1 semantic (sub-agent):   instructions, tests, solution
#   Layer 2 trajectory findings fold into `tests`; Layer 3 behavioral -> `behavioral`.
# This aggregator is the cross-layer merge point: a task's per-area verdict is the
# WORST finding in that area, and overall is the worst area — so a FAIL from ANY
# layer is sticky and a later layer's PASS can never downgrade it (see shared/gate.py).
COLS = ["structure", "metadata", "dockerfile", "anti_cheat", "dataset",
        "instructions", "tests", "solution", "behavioral"]


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


def reconcile(findings):
    """Apply sub-agent verification verdicts to static findings.

    A review sub-agent can emit a meta finding `{"title":"verify-refuted",
    "ref":"<static-title>","task":...}` to mark a static flag a false positive,
    or `verify-confirm` to confirm one. Refuted static findings are dropped before
    verdicts are computed (precision win); the meta findings themselves never roll
    into a verdict. Returns (findings_for_verdict, n_refuted_dropped).
    """
    refuted = {(f["task"], f["ref"]) for f in findings
               if f.get("title") == "verify-refuted" and f.get("ref")}
    # A read-only SKEPTIC confirms/refutes each adversarial cheat-vector candidate
    # (the precision filter for Part 3). Its verdicts decide the cheat-vector's fate.
    cv_confirmed = {f["task"] for f in findings if f.get("title") == "cheat-vector-confirmed"}
    cv_refuted = {f["task"] for f in findings if f.get("title") == "cheat-vector-refuted"}
    # MECHANICAL gate: a verifier with a detected anti-cheat defense (mutated rerun /
    # recompute / source-grep / re-exec) provably resists the hardcode/fake-artifact
    # cheats, so cheat-vector candidates against it are suppressed deterministically —
    # no agent in the loop, so it can't cry wolf (check_verifier_defenses.py).
    defended = {f["task"] for f in findings if f.get("title") == "verifier-defended"}
    # A subset of defenses actually defeats `agent-writable-verifier`: recompute /
    # mutated-rerun / source-grep are independent of the copied script, so the agent
    # cannot forge them. `re-exec-agent` is NOT one of them — re-running the agent's
    # program is precisely the hole when that program IS the copied-in grader. So the
    # agent-writable down-grade keys on these HARD defenses only (the broader
    # `defended` set still suppresses the static analyst's semantic-cheat-vector).
    HARD = ("recompute-or-hash", "mutated-rerun", "source-grep-guard")
    hard_defended = {f["task"] for f in findings if f.get("title") == "verifier-defended"
                     and any(d in (f.get("detail") or "") for d in HARD)}
    META = ("verify-refuted", "verify-confirm", "cheat-vector-confirmed", "cheat-vector-refuted")
    out, dropped = [], 0
    for f in findings:
        if f.get("title") in META:
            continue
        if (f["task"], f.get("title")) in refuted:
            dropped += 1
            continue
        # MECHANICAL precision gate: `agent-writable-verifier` (the agent can
        # overwrite a copied-in grading script) is only a working cheat if the
        # overwrite actually forces a pass. When the same verifier has an
        # independent anti-cheat defense (recompute / mutated-rerun / source-grep —
        # check_verifier_defenses `verifier-defended`), the agent cannot forge that
        # defense, so the FAIL is a candidate, not a proof: down-grade to WARN for
        # the runtime gate to confirm. Validated on the cognition delivery — every
        # runtime-CONFIRMED hack was `verifier-undefended`; the defended ones
        # (daemon-cert-pipeline, mpi-thread-thrashing, stale-ddp-ensemble-state)
        # were all refuted. Zero recall loss, removes the dominant FP class.
        if (f.get("title") == "agent-writable-verifier" and f.get("severity") == FAIL
                and f["task"] in hard_defended):
            f = {**f, "severity": WARN,
                 "detail": (f.get("detail", "") + " [reconcile: verifier has an "
                            "independent anti-cheat defense (verifier-defended) the agent "
                            "cannot forge — down-graded FAIL->WARN; confirm at runtime.]")}
            out.append(f)
            continue
        # An ANALYTICAL adversarial cheat-vector is a CANDIDATE, not a verdict:
        # raw, it over-flagged (precision 0.22 as FAIL) — reading alone can't tell a
        # real exploit from a theoretical one. Its fate is decided by the skeptic:
        #   confirmed -> FAIL ; refuted -> dropped ; unreviewed -> WARN candidate.
        if f.get("title") == "semantic-cheat-vector":
            if f["task"] in cv_refuted or f["task"] in defended:
                dropped += 1  # refuted by skeptic, or the verifier has a real defense
                continue
            f = {**f, "severity": FAIL if f["task"] in cv_confirmed else WARN}
        out.append(f)
    return out, dropped


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


def _flat(s):
    """Collapse newlines/whitespace so a finding's text stays on one CSV cell."""
    return " ".join((s or "").split())


# Triage priority, by defect CLASS (independent of FAIL/WARN):
#   P0  the verifier can be PASSED without doing the work, or the answer/verifier is
#       readable/writable by the agent (confirmed-hackable / leak) — fix first.
#   P1  brittle / weak verifier — rejects correct work or verifies weakly.
#   P2  hygiene / build-isolation / metadata — clean up, low exploit risk.
P0_TITLES = {
    "agent-writable-verifier", "truth-baked-verifier-reads", "tests-bake-verifier-reads",
    "reference-solve-reads-truth", "unconditional-reward", "dockerfile-copies-solution",
    "dockerfile-copies-tests", "test-imports-solution", "reward-pre-created",
    "secret-baked-in-image", "semantic-cheat-vector", "cheat-confirmed",
    "pycache-baked-in-image",
}
P1_TITLES = {
    "source-match-verification", "verifier-undefended", "wall-clock-dependent-verifier",
    "filename-encodes-answer", "verifier-self-consistent", "literal-only-verifier",
    "degenerate-integrity-guard", "brittle-string-match", "existence-only-check",
    "no-assertion-test", "vacuous-test", "swallowed-assertion", "weak-assertion",
    "skipped-scored-test", "empty-parametrize", "golden-patch-mismatch", "flaky-test",
    "test-sh-set-e-reward-abort", "test-sh-swallows-failure", "agent-writable-reward-signal",
    "verifier-reads-config-spec", "truth-named-baked", "test-runtime-install",
    "reward-path-nonstandard", "tests-bake-unread", "untested-requirement",
    "pycache-residue-after-script-removal",
}
P2_TITLES = {
    "chmod-not-a-guard", "verifier-helper-in-environment", "dockerfile-copies-env-tests",
    "dockerfile-copies-hint-file", "missing-dockerignore", "broad-chmod",
    "verifier-reads-instruction-input",
}


def priority_of(f):
    """Map a finding to P0/P1/P2 by class; fall back on severity for unlisted titles
    (FAIL -> P0, WARN -> P2). P-findings (PASS) get '' (not a defect)."""
    t = f.get("title", "")
    if t in P0_TITLES:
        return "P0"
    if t in P1_TITLES:
        return "P1"
    if t in P2_TITLES:
        return "P2"
    # Unlisted findings fall back by severity + area. A FAIL is P0 only when it is an
    # exploit/leak class (anti_cheat / tests / dataset); a blocking-but-not-hackable
    # FAIL (missing file, bad metadata, bad Dockerfile) is P1, not P0.
    if f.get("severity") == FAIL:
        return "P0" if f.get("area") in ("anti_cheat", "tests", "dataset") else "P1"
    if f.get("severity") == WARN:
        return "P2"
    return ""


# Remediation class of a defect — drives the FIXABLE sub-bucket (and effort estimate).
#   relocate   : move grader/truth out of agent space into tests/ (mechanical, minutes)
#   strengthen : the verifier is weak/brittle — add recompute/mutated-rerun/functional check
#   align      : instruction <-> verifier mismatch (1-line, but prefer strengthening tests)
RELOCATE = {"agent-writable-verifier", "truth-baked-verifier-reads", "tests-bake-verifier-reads",
            "verifier-helper-in-environment", "chmod-not-a-guard", "reward-pre-created",
            "dockerfile-copies-tests", "dockerfile-copies-solution", "reference-solve-reads-truth",
            "secret-baked-in-image", "dockerfile-copies-env-tests",
            "pycache-baked-in-image", "pycache-residue-after-script-removal"}
STRENGTHEN = {"literal-only-verifier", "source-match-verification", "verifier-undefended",
              "filename-encodes-answer", "wall-clock-dependent-verifier", "verifier-self-consistent",
              "existence-only-check", "no-assertion-test", "vacuous-test", "swallowed-assertion",
              "weak-assertion", "unconditional-reward", "test-sh-swallows-failure",
              "test-sh-set-e-reward-abort", "empty-parametrize", "skipped-scored-test",
              "agent-writable-reward-signal", "tests-bake-unread", "truth-named-baked"}
ALIGN = {"brittle-string-match", "untested-requirement"}
# DEFINITE brittle/weak-verifier defects (not advisory) — these alone justify FIXABLE.
# Everything else at P1 (verifier-reads-config-spec, truth-named-baked, tests-bake-unread,
# agent-writable-reward-signal, reward-path-nonstandard, test-runtime-install, ...) is
# advisory: it routes to REVIEW for Layer-2/behavioral confirmation, not a confirmed fix.
CONCRETE_BRITTLE = {"literal-only-verifier", "source-match-verification",
                    "wall-clock-dependent-verifier", "filename-encodes-answer",
                    "verifier-self-consistent", "golden-patch-mismatch", "brittle-string-match",
                    "vacuous-test", "no-assertion-test", "existence-only-check",
                    "swallowed-assertion", "empty-parametrize", "skipped-scored-test",
                    "unconditional-reward", "test-sh-swallows-failure",
                    "test-sh-set-e-reward-abort"}
# semantic/behavioral signals that the task itself is broken (TOTAL, not fixable by edit)
ORACLE_FAIL = {"golden-patch-mismatch", "oracle-fails", "reference-fails-own-tests"}


def bucketize(task_findings, behavioral=None):
    """Assign a task to a triage bucket + Studio-ready qc:* tags.

    Static alone yields SHIP vs a CANDIDATE fixable bucket (by defect class); the
    TOTAL buckets (broken-oracle / gameable / unviable) are decided by the runtime
    signals in `behavioral` = {oracle:1/0, noop:1/0, cheat:1/0, builds:bool} (any may
    be absent). Returns {bucket, remediation, priority, confidence, needs_behavioral, tags}."""
    flagged = [f for f in task_findings if f.get("severity") in (FAIL, WARN)]
    titles = {f.get("title") for f in flagged}
    # `verifier-undefended` is the weakest signal — "no anti-cheat defense DETECTED",
    # which fires on most (incl. perfectly clean functional) verifiers. It must not by
    # itself make a task FIXABLE-vs-SHIP or set the bucket priority; it stays a P1 row
    # in defects.csv but is advisory-only here.
    SOFT = {"verifier-undefended"}
    real = [f for f in flagged if f.get("title") not in SOFT]
    pri_rank = {"P0": 0, "P1": 1, "P2": 2}
    pris = [p for p in (priority_of(f) for f in real) if p]
    priority = min(pris, key=lambda p: pri_rank.get(p, 9)) if pris else ""
    b = behavioral or {}
    confirmed = bool(behavioral)

    bucket = remediation = ""
    # --- TOTAL (need runtime, except the explicit oracle-fail finding classes) ---
    if b.get("builds") is False:
        bucket, remediation = "total", "unviable"
    elif b.get("oracle") == 0 or (titles & ORACLE_FAIL):
        bucket, remediation = "total", "broken-oracle"
    elif b.get("noop") == 1 or b.get("cheat") == 1:
        bucket, remediation = "total", "gameable"
    else:
        # FIXABLE needs a CONCRETE actionable defect — a leak (P0), a hard FAIL, or a
        # definite brittle/weak verifier. A task whose only flags are soft advisory
        # WARNs (config-spec, truth-named-baked, reward-signal, metadata/dockerfile
        # hygiene, ...) is NOT a confirmed fix — it goes to REVIEW for Layer-2/
        # behavioral confirmation. Otherwise the WARN-heavy OTS baseline makes ~90% of
        # tasks look "fixable", which is useless.
        has_fail = any(f.get("severity") == FAIL for f in real)
        concrete = titles & CONCRETE_BRITTLE
        if has_fail or priority == "P0" or concrete:
            bucket = "fixable"
            if titles & RELOCATE:
                remediation = "relocate"
            elif concrete or (titles & STRENGTHEN):
                remediation = "strengthen"
            elif titles & ALIGN:
                remediation = "align"
            else:  # a hard FAIL with no verifier-class — name it by area
                area = next((f.get("area") for f in real if f.get("severity") == FAIL), "")
                remediation = {"metadata": "metadata", "structure": "add-file",
                               "dockerfile": "dockerfile"}.get(area, "review")
        elif priority == "P1":
            bucket = "review"   # advisory-only — confirm with Layer-2 / behavioral
        else:
            bucket = "ship"     # clean or P2 hygiene only
    needs_behavioral = behavioral is None and bucket in ("fixable", "review")
    # human-facing rollup status (the QC-outcome vocabulary): passing / needs-fixing /
    # defective-hard (broken, total failure) / needs-review (advisory, unconfirmed).
    qc_status = {"ship": "passing", "fixable": "needs-fixing",
                 "total": "defective-hard", "review": "needs-review"}.get(bucket, "")
    tags = [f"qc:{bucket}", f"qc:{qc_status}"] if qc_status else [f"qc:{bucket}"]
    if remediation:
        tags.append(f"qc:{remediation}" if bucket == "total" else f"qc:fix-{remediation}")
    if priority:
        tags.append(f"qc:{priority.lower()}")
    tags.append("qc:confirmed" if confirmed else "qc:candidate")
    if needs_behavioral:
        tags.append("qc:needs-behavioral")
    return {"bucket": bucket, "status": qc_status, "remediation": remediation,
            "priority": priority, "confidence": "confirmed" if confirmed else "candidate",
            "needs_behavioral": needs_behavioral, "tags": tags}


def write_buckets_csv(tasks, path, behavioral_map=None):
    """One row per task: bucket / remediation / priority / confidence / qc:* tags.
    `tasks` is per_task(findings); `behavioral_map` optionally maps task -> runtime dict."""
    behavioral_map = behavioral_map or {}
    rows = []
    for task, areas in tasks.items():
        flat = [f for fs in areas.values() for f in fs]
        rows.append((task, bucketize(flat, behavioral_map.get(task))))
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["task", "bucket", "status", "remediation", "priority", "confidence", "tags"])
        for task, bk in sorted(rows):
            w.writerow([task, bk["bucket"], bk["status"], bk["remediation"], bk["priority"],
                        bk["confidence"], " ".join(bk["tags"])])
    return Counter(bk["bucket"] + (f"/{bk['remediation']}" if bk["remediation"] else "")
                   for _, bk in rows)


def write_defects_csv(findings, path):
    """One row per flagged finding: priority + the defect + WHY it failed + the fix +
    which layer caught it. The "what's wrong and why" export — defects only (FAIL and
    WARN), sorted P0 -> P2 then FAIL-first. PASS findings are omitted."""
    sev_order = {FAIL: 0, WARN: 1}
    pri_order = {"P0": 0, "P1": 1, "P2": 2, "": 3}
    flagged = sorted((f for f in findings if f.get("severity") in (FAIL, WARN)),
                     key=lambda f: (pri_order[priority_of(f)], sev_order[f["severity"]],
                                    f["task"], f.get("area", "")))
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["priority", "task", "layer", "area", "severity", "defect",
                    "location", "reason", "fix"])
        for f in flagged:
            w.writerow([priority_of(f), f["task"], layer_of(f), f.get("area", ""),
                        f["severity"], f.get("title", ""), f.get("location", ""),
                        _flat(f.get("detail", "")), _flat(f.get("fix", ""))])
    return len(flagged)


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
    findings, n_refuted = reconcile(findings)
    if n_refuted:
        print(f"  reconcile: dropped {n_refuted} static finding(s) refuted by review sub-agents")
    tasks = per_task(findings)
    rows = verdicts(tasks)

    write_csv(rows, os.path.join(out_dir, "review-ssot.csv"))
    write_details(tasks, rows, os.path.join(out_dir, "review-ssot.md"))
    write_distribution(findings, rows, os.path.join(out_dir, "defect-distribution.md"))
    n_defects = write_defects_csv(findings, os.path.join(out_dir, "defects.csv"))

    # Triage buckets + Studio qc:* tags. If a behavioral_signals.json is present
    # ({task: {oracle,noop,cheat,builds}}), the TOTAL buckets are confirmed from it.
    bmap = {}
    bsig = os.path.join(args.findings_dir, "behavioral_signals.json")
    if os.path.isfile(bsig):
        try:
            bmap = json.load(open(bsig))
        except (OSError, ValueError):
            bmap = {}
    hist = write_buckets_csv(tasks, os.path.join(out_dir, "tasks_buckets.csv"), bmap)

    n_fail = sum(1 for r in rows.values() if r["overall"] == FAIL)
    print(f"[aggregate] {len(rows)} tasks, {n_fail} FAIL, {n_defects} flagged findings -> "
          f"{out_dir}/review-ssot.csv, review-ssot.md, defect-distribution.md, defects.csv, tasks_buckets.csv")
    print(f"  buckets: {dict(sorted(hist.items(), key=lambda kv: -kv[1]))}"
          + ("" if bmap else "  (static candidates; run behavioral to confirm TOTAL)"))


if __name__ == "__main__":
    main()
