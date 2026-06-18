#!/usr/bin/env python3
"""Layer 0 — task.toml metadata lint (deterministic, read-only).

Catches the metadata defect classes called out in the QC gate reference and the
terminal-bench-review checklist: missing/empty required fields, time-estimate
sanity (incl. the seconds-as-minutes smell), difficulty/time alignment, timeout
sanity, resource adequacy, and generic/over-broad category or tags.

Handles the real TB2 OTS shape:
  [metadata]  difficulty / category / subcategory / operation_type / tags /
              expert_time_estimate_min / junior_time_estimate_min
  [verifier]  timeout_sec
  [agent]     timeout_sec
  [environment]  cpus / memory_mb / storage_mb / gpus / allow_internet

Usage:
    python check_metadata.py <tasks-dir> [--out findings_metadata.json]

Emits findings with area="metadata".
"""
import argparse
import os

from common import (FAIL, WARN, PASS, finding, emit,
                    discover_tasks, task_paths, load_toml, get)

VALID_DIFFICULTY = {"easy", "medium", "hard"}
GENERIC_CATEGORIES = {"programming", "general", "code", "task", "misc", "other", ""}
BROAD_TAGS = {"general", "code", "task", "programming", "misc", "other", "cli", "linux"}

# TB2 reference ranges, in MINUTES: (expert_lo, expert_hi, junior_lo, junior_hi)
TIME_RANGES = {
    "easy":   (5, 60, 20, 120),
    "medium": (5, 180, 10, 480),
    "hard":   (300, 480, 600, 19200),
}


def _num(v):
    return v if isinstance(v, (int, float)) else None


def check_task(name, root):
    out = []
    p = task_paths(root)
    if not os.path.isfile(p["task.toml"]):
        return [finding(name, "metadata", FAIL, "task-toml-missing",
                        detail="task.toml is absent — metadata cannot be checked.",
                        location="task.toml",
                        fix="Add task.toml with the required [metadata]/[verifier]/[agent]/[environment] sections.")]
    d = load_toml(p["task.toml"])

    difficulty = get(d, "metadata.difficulty")
    category = get(d, "metadata.category")
    tags = get(d, "metadata.tags")
    expert = _num(get(d, "metadata.expert_time_estimate_min"))
    junior = _num(get(d, "metadata.junior_time_estimate_min"))
    # timeouts: support both TB2 nested and flat legacy keys
    vto = _num(get(d, "verifier.timeout_sec")) or _num(get(d, "verifier_timeout"))
    ato = _num(get(d, "agent.timeout_sec")) or _num(get(d, "agent_timeout"))
    cpus = _num(get(d, "environment.cpus"))
    mem = _num(get(d, "environment.memory_mb"))
    gpus = _num(get(d, "environment.gpus"))

    # ---- required fields ----
    if not difficulty:
        out.append(finding(name, "metadata", FAIL, "missing-difficulty",
                           detail="metadata.difficulty is missing/empty.",
                           location="task.toml [metadata]",
                           fix="Set difficulty to easy/medium/hard."))
    elif str(difficulty).lower() not in VALID_DIFFICULTY:
        out.append(finding(name, "metadata", WARN, "unknown-difficulty",
                           detail=f"difficulty={difficulty!r} is not easy/medium/hard.",
                           location="task.toml [metadata]",
                           fix="Use one of easy/medium/hard."))

    if not category:
        out.append(finding(name, "metadata", FAIL, "missing-category",
                           detail="metadata.category is missing/empty.",
                           location="task.toml [metadata]",
                           fix="Set a specific category that matches the task."))
    elif str(category).strip().lower() in GENERIC_CATEGORIES:
        out.append(finding(name, "metadata", WARN, "generic-category",
                           detail=f"category={category!r} is too generic.",
                           location="task.toml [metadata]",
                           fix="Use a specific category (e.g. 'networking', 'data-pipeline')."))

    if not tags:
        out.append(finding(name, "metadata", FAIL, "missing-tags",
                           detail="metadata.tags is missing/empty.",
                           location="task.toml [metadata]",
                           fix="Add specific tags relating to the task."))
    else:
        if not isinstance(tags, list):
            tags = [tags]
        broad = [t for t in tags if str(t).strip().lower() in BROAD_TAGS]
        if broad and len(broad) == len(tags):
            out.append(finding(name, "metadata", WARN, "broad-tags-only",
                               detail=f"All tags are over-broad: {tags}.",
                               location="task.toml [metadata]",
                               fix="Replace with tags specific to the task content."))
        elif broad:
            out.append(finding(name, "metadata", WARN, "some-broad-tags",
                               detail=f"Over-broad tag(s): {broad}.",
                               location="task.toml [metadata]",
                               fix="Drop or specialise the over-broad tags."))

    # ---- time estimates ----
    if expert is None:
        out.append(finding(name, "metadata", FAIL, "missing-expert-time",
                           detail="metadata.expert_time_estimate_min missing.",
                           location="task.toml [metadata]",
                           fix="Add expert_time_estimate_min (minutes)."))
    if junior is None:
        out.append(finding(name, "metadata", FAIL, "missing-junior-time",
                           detail="metadata.junior_time_estimate_min missing.",
                           location="task.toml [metadata]",
                           fix="Add junior_time_estimate_min (minutes)."))
    if expert is not None and expert <= 0:
        out.append(finding(name, "metadata", FAIL, "nonpositive-expert-time",
                           detail=f"expert_time_estimate_min={expert} must be > 0.",
                           location="task.toml [metadata]", fix="Set a positive value."))
    if expert is not None and junior is not None and junior < expert:
        out.append(finding(name, "metadata", WARN, "junior-lt-expert",
                           detail=f"junior_time ({junior}) < expert_time ({expert}).",
                           location="task.toml [metadata]",
                           fix="junior_time_estimate_min should be >= expert."))
    # difficulty/time alignment + seconds-as-minutes smell
    if difficulty and str(difficulty).lower() in TIME_RANGES:
        elo, ehi, jlo, jhi = TIME_RANGES[str(difficulty).lower()]
        if expert is not None and expert > 0 and not (elo <= expert <= ehi):
            sev = WARN
            extra = ""
            if expert > ehi * 30:  # ~60x too high => recorded in seconds
                extra = (" Value is ~60x the expected range — likely recorded in "
                         "SECONDS, not minutes (divide by 60).")
                sev = FAIL
            out.append(finding(name, "metadata", sev, "expert-time-out-of-range",
                               detail=f"expert_time={expert} min outside the "
                                      f"{difficulty} range [{elo},{ehi}] min.{extra}",
                               location="task.toml [metadata]",
                               fix="Recalibrate to the difficulty range (minutes)."))
        if junior is not None and junior > 0 and not (jlo <= junior <= jhi):
            out.append(finding(name, "metadata", WARN, "junior-time-out-of-range",
                               detail=f"junior_time={junior} min outside the "
                                      f"{difficulty} range [{jlo},{jhi}] min.",
                               location="task.toml [metadata]",
                               fix="Recalibrate to the difficulty range (minutes)."))

    # ---- timeouts ----
    if vto is None:
        out.append(finding(name, "metadata", FAIL, "missing-verifier-timeout",
                           detail="verifier timeout_sec missing.",
                           location="task.toml [verifier]",
                           fix="Add [verifier] timeout_sec."))
    elif vto <= 0:
        out.append(finding(name, "metadata", FAIL, "nonpositive-verifier-timeout",
                           detail=f"verifier timeout_sec={vto} must be > 0.",
                           location="task.toml [verifier]", fix="Set a positive timeout."))
    if ato is None:
        out.append(finding(name, "metadata", FAIL, "missing-agent-timeout",
                           detail="agent timeout_sec missing.",
                           location="task.toml [agent]",
                           fix="Add [agent] timeout_sec."))
    elif ato <= 0:
        out.append(finding(name, "metadata", FAIL, "nonpositive-agent-timeout",
                           detail=f"agent timeout_sec={ato} must be > 0.",
                           location="task.toml [agent]", fix="Set a positive timeout."))
    if vto and ato and ato < vto:
        out.append(finding(name, "metadata", WARN, "agent-timeout-lt-verifier",
                           detail=f"agent timeout ({ato}s) < verifier timeout ({vto}s).",
                           location="task.toml",
                           fix="agent timeout should be >= verifier timeout."))

    # ---- resources ----
    if cpus is None and mem is None and gpus is None:
        out.append(finding(name, "metadata", WARN, "no-env-resources",
                           detail="No environment resources (cpus/memory_mb/gpus) declared.",
                           location="task.toml [environment]",
                           fix="Declare at least cpus and memory_mb."))
    # MAI infra enforces ~1 CPU / 4 GB; flag tasks that quietly need more
    if cpus is not None and cpus > 1:
        out.append(finding(name, "metadata", WARN, "cpus-above-client-cap",
                           detail=f"cpus={cpus}: clients (e.g. MAI) enforce ~1 CPU; "
                                  "task may pass on Modal but fail under client caps.",
                           location="task.toml [environment]",
                           fix="Confirm the task runs within ~1 CPU / 4 GB, or flag the requirement."))
    if mem is not None and mem > 4096:
        out.append(finding(name, "metadata", WARN, "memory-above-client-cap",
                           detail=f"memory_mb={mem}: exceeds the common ~4 GB client cap.",
                           location="task.toml [environment]",
                           fix="Confirm the task runs within ~4 GB or flag the requirement."))

    if not out:
        out.append(finding(name, "metadata", PASS, "metadata-ok"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks")
    ap.add_argument("--out", default="findings_metadata.json")
    args = ap.parse_args()
    findings = []
    tasks = discover_tasks(args.tasks)
    for name, root in tasks:
        findings.extend(check_task(name, root))
    n = emit(findings, args.out)
    fails = sum(1 for f in findings if f["severity"] == FAIL)
    print(f"[metadata] {len(tasks)} tasks, {n} findings, {fails} FAIL -> {args.out}")


if __name__ == "__main__":
    main()
