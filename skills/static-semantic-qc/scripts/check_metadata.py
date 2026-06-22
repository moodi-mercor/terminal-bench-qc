#!/usr/bin/env python3
"""Layer 0 — task.toml metadata lint (deterministic, read-only).

Catches the metadata defect classes called out in the QC gate reference and the
terminal-bench-review checklist: missing/empty required fields, time-estimate
sanity (incl. the seconds-as-minutes smell), difficulty/time alignment, timeout
sanity, resource adequacy, and generic/over-broad category or tags.

**Schema-tolerant.** Two task.toml shapes are in the wild and both validate here:

  - TB2 / OTS shape:
      [metadata]  difficulty / category / subcategory / tags /
                  expert_time_estimate_min / junior_time_estimate_min
  - Reflection shape (the Harbor spec):
      [metadata]  category / subcategory / task_objective[] / artifact_type[] /
                  expert_time_estimate_hours / model_tested / agent_tested / avg_at_8
      [environment]  build_timeout_sec (+ cpus/memory_mb/storage_mb/gpus/allow_internet)

A task is treated as Reflection-shaped when it carries any Reflection-only field
(task_objective / artifact_type / model_tested / agent_tested / avg_at_8 /
expert_time_estimate_hours / build_timeout_sec). Reflection tasks are NOT flagged
for missing difficulty/tags/junior-time, and TB2 tasks are NOT flagged for missing
Reflection fields — so neither schema gets false positives for the other's keys.

Common to both: timeouts, env resources, internet-flag contradiction, the
difficulty bar (`avg_at_8 ≤ 0.5`, when recorded), and template-placeholder smells.

Usage:
    python check_metadata.py <tasks-dir> [--out findings_metadata.json]

Emits findings with area="metadata".
"""
import argparse
import os
import re

from common import (FAIL, WARN, PASS, finding, emit, read_text,
                    discover_tasks, task_paths, load_toml, get, is_reflection_schema)

# heavy workloads that routinely need >4 GB (customer hit OOM at memory_mb=4096)
HEAVY_WORKLOAD = re.compile(r"\b(spark|pyspark|neo4j|elasticsearch|hadoop|milvus|"
                            r"clickhouse|cassandra|-Xmx[0-9]*[gG])\b", re.I)

VALID_DIFFICULTY = {"easy", "medium", "hard"}
GENERIC_CATEGORIES = {"programming", "general", "code", "task", "misc", "other", ""}
BROAD_TAGS = {"general", "code", "task", "programming", "misc", "other", "cli", "linux"}

# TB2 reference ranges, in MINUTES: (expert_lo, expert_hi, junior_lo, junior_hi)
TIME_RANGES = {
    "easy":   (5, 60, 20, 120),
    "medium": (5, 180, 10, 480),
    "hard":   (300, 480, 600, 19200),
}

# Reflection difficulty bar: the hard model must solve the task <= half the time.
AVG_AT_8_MAX = 0.5
# Reflection difficulty methodology: Terminus-2 agent + Opus-4.8 / GPT-5.4 model.
APPROVED_MODELS = {"gpt 5 4", "opus 4 8", "claude opus 4 8", "claude opus 4 8 20"}
APPROVED_AGENTS = {"terminus 2"}


def _norm(s):
    """Lowercase, collapse non-alphanumerics to single spaces (label matching)."""
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


# Reflection task-objective taxonomy (each label must cover >=10% of a delivery).
TASK_OBJECTIVES = {_norm(x) for x in (
    "implement", "fix", "configure", "analyze", "transform", "validate",
    "optimize", "migrate", "refactor", "test", "debug", "build or package",
    "deploy or operate", "recover or repair artifact", "generate",
    "compare or select", "secure or harden", "automate workflow")}

# Reflection artifact-type taxonomy (each label must cover >=5% of a delivery).
ARTIFACT_TYPES = {_norm(x) for x in (
    "codebase", "single script or program", "test suite or benchmark",
    "build system or package metadata", "configuration file", "shell environment",
    "service or daemon", "container or virtual environment",
    "database or structured store", "dataset or tabular file", "text or log file",
    "document or report", "archive or compressed artifact",
    "binary executable or library", "media artifact", "model or checkpoint",
    "hardware or firmware artifact", "network endpoint or protocol artifact",
    "repository history or version-control state", "security artifact",
    "mathematical or scientific model", "generated output artifact")}


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def check_task(name, root):
    out = []
    p = task_paths(root)
    if not os.path.isfile(p["task.toml"]):
        return [finding(name, "metadata", FAIL, "task-toml-missing",
                        detail="task.toml is absent — metadata cannot be checked.",
                        location="task.toml",
                        fix="Add task.toml with the required [metadata]/[verifier]/[agent]/[environment] sections.")]
    d = load_toml(p["task.toml"])
    reflection = is_reflection_schema(d)

    difficulty = get(d, "metadata.difficulty")
    category = get(d, "metadata.category")
    subcategory = get(d, "metadata.subcategory")
    tags = get(d, "metadata.tags")
    objectives = _as_list(get(d, "metadata.task_objective"))
    artifacts = _as_list(get(d, "metadata.artifact_type"))
    # time: TB2 records minutes, Reflection records hours — normalize to minutes.
    expert_min = _num(get(d, "metadata.expert_time_estimate_min"))
    expert_hr = _num(get(d, "metadata.expert_time_estimate_hours"))
    expert = expert_min if expert_min is not None else (
        expert_hr * 60 if expert_hr is not None else None)
    junior = _num(get(d, "metadata.junior_time_estimate_min"))
    # timeouts: support both TB2 nested and flat legacy keys
    vto = _num(get(d, "verifier.timeout_sec")) or _num(get(d, "verifier_timeout"))
    ato = _num(get(d, "agent.timeout_sec")) or _num(get(d, "agent_timeout"))
    bto = _num(get(d, "environment.build_timeout_sec"))
    cpus = _num(get(d, "environment.cpus"))
    mem = _num(get(d, "environment.memory_mb"))
    storage = _num(get(d, "environment.storage_mb"))
    gpus = _num(get(d, "environment.gpus"))

    # ---- difficulty (TB2 schema only — Reflection replaces it with avg_at_8) ----
    if not reflection:
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

    # ---- category (both schemas) ----
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

    # ---- Reflection-shape label fields ----
    if reflection:
        if not subcategory:
            out.append(finding(name, "metadata", WARN, "missing-subcategory",
                               detail="metadata.subcategory is missing — Reflection requires "
                                      "a category AND subcategory from the taxonomy.",
                               location="task.toml [metadata]",
                               fix="Add the best-fit subcategory from the diversity taxonomy."))
        if not objectives:
            out.append(finding(name, "metadata", WARN, "missing-task-objective",
                               detail="metadata.task_objective is missing — Reflection requires "
                                      "≥1 task-objective label for diversity tracking.",
                               location="task.toml [metadata]",
                               fix="Add task_objective = [...] from the objective taxonomy "
                                   "(implement/fix/configure/analyze/…)."))
        else:
            unknown = [o for o in objectives if _norm(o) not in TASK_OBJECTIVES]
            if unknown:
                out.append(finding(name, "metadata", WARN, "unknown-task-objective",
                                   detail=f"task_objective value(s) {unknown} are not in the "
                                          "Reflection objective taxonomy.",
                                   location="task.toml [metadata]",
                                   fix="Use objective labels from the taxonomy "
                                       "(implement/fix/configure/analyze/transform/validate/…)."))
        if not artifacts:
            out.append(finding(name, "metadata", WARN, "missing-artifact-type",
                               detail="metadata.artifact_type is missing — Reflection requires "
                                      "≥1 artifact-type label for diversity tracking.",
                               location="task.toml [metadata]",
                               fix="Add artifact_type = [...] from the artifact taxonomy "
                                   "(codebase/configuration file/dataset or tabular file/…)."))
        else:
            unknown = [a for a in artifacts if _norm(a) not in ARTIFACT_TYPES]
            if unknown:
                out.append(finding(name, "metadata", WARN, "unknown-artifact-type",
                                   detail=f"artifact_type value(s) {unknown} are not in the "
                                          "Reflection artifact taxonomy.",
                                   location="task.toml [metadata]",
                                   fix="Use artifact labels from the taxonomy "
                                       "(codebase/single script or program/configuration file/…)."))

    # ---- tags (TB2 schema only) ----
    if not reflection:
        if not tags:
            out.append(finding(name, "metadata", FAIL, "missing-tags",
                               detail="metadata.tags is missing/empty.",
                               location="task.toml [metadata]",
                               fix="Add specific tags relating to the task."))
        else:
            tlist = tags if isinstance(tags, list) else [tags]
            broad = [t for t in tlist if str(t).strip().lower() in BROAD_TAGS]
            if broad and len(broad) == len(tlist):
                out.append(finding(name, "metadata", WARN, "broad-tags-only",
                                   detail=f"All tags are over-broad: {tlist}.",
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
                           detail="expert time estimate missing "
                                  "(expert_time_estimate_hours or _min).",
                           location="task.toml [metadata]",
                           fix="Add expert_time_estimate_hours (Reflection) or "
                               "expert_time_estimate_min (TB2)."))
    elif expert <= 0:
        out.append(finding(name, "metadata", FAIL, "nonpositive-expert-time",
                           detail=f"expert time estimate ({expert} min) must be > 0.",
                           location="task.toml [metadata]", fix="Set a positive value."))
    # TB2 also tracks a junior estimate + difficulty/time-range alignment.
    if not reflection:
        if junior is None:
            out.append(finding(name, "metadata", FAIL, "missing-junior-time",
                               detail="metadata.junior_time_estimate_min missing.",
                               location="task.toml [metadata]",
                               fix="Add junior_time_estimate_min (minutes)."))
        if expert is not None and junior is not None and junior < expert:
            out.append(finding(name, "metadata", WARN, "junior-lt-expert",
                               detail=f"junior_time ({junior}) < expert_time ({expert}).",
                               location="task.toml [metadata]",
                               fix="junior_time_estimate_min should be >= expert."))
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

    # ---- difficulty bar + benchmarking metadata (Reflection delivery only) ----
    # The avg@8 ≤ 0.5 bar is Reflection-specific, so it only applies to Reflection-
    # schema tasks (a task that records avg_at_8 is Reflection-shaped by definition).
    # General OTS QC never enforces it.
    avg = _num(get(d, "metadata.avg_at_8"))
    model_tested = get(d, "metadata.model_tested")
    agent_tested = get(d, "metadata.agent_tested")
    if reflection:
        if avg is not None and avg > AVG_AT_8_MAX:
            out.append(finding(name, "metadata", FAIL, "avg-at-8-too-easy",
                               detail=f"avg_at_8={avg} exceeds the difficulty bar "
                                      f"(must be ≤ {AVG_AT_8_MAX}); the frontier model solves it "
                                      "too often, so the task is too easy.",
                               location="task.toml [metadata]",
                               fix="Make the task harder, or replace it; re-benchmark avg@8 with "
                                   "Terminus-2 on Opus-4.8/GPT-5.4."))
        if avg is None:
            out.append(finding(name, "metadata", WARN, "missing-avg-at-8",
                               detail="metadata.avg_at_8 missing — Reflection requires a "
                                      "recorded average@8 from the difficulty benchmark.",
                               location="task.toml [metadata]",
                               fix="Benchmark with Terminus-2 on Opus-4.8/GPT-5.4 (8 attempts) "
                                   "and record avg_at_8."))
        if not model_tested:
            out.append(finding(name, "metadata", WARN, "missing-model-tested",
                               detail="metadata.model_tested missing — difficulty must be "
                                      "measured on Opus-4.8 or GPT-5.4.",
                               location="task.toml [metadata]",
                               fix="Set model_tested to the frontier model used (GPT-5.4 / Opus 4.8)."))
        elif _norm(model_tested) not in APPROVED_MODELS:
            out.append(finding(name, "metadata", WARN, "model-tested-not-approved",
                               detail=f"model_tested={model_tested!r} is not an approved "
                                      "difficulty model (Opus 4.8 / GPT-5.4).",
                               location="task.toml [metadata]",
                               fix="Benchmark difficulty on Opus 4.8 or GPT-5.4 and record it."))
        if not agent_tested:
            out.append(finding(name, "metadata", WARN, "missing-agent-tested",
                               detail="metadata.agent_tested missing — Reflection mandates the "
                                      "Terminus-2 agent for difficulty.",
                               location="task.toml [metadata]",
                               fix="Set agent_tested = \"Terminus-2\"."))
        elif _norm(agent_tested) not in APPROVED_AGENTS:
            out.append(finding(name, "metadata", WARN, "agent-tested-not-approved",
                               detail=f"agent_tested={agent_tested!r} is not Terminus-2 "
                                      "(the mandated difficulty agent).",
                               location="task.toml [metadata]",
                               fix="Use the Terminus-2 agent for the difficulty benchmark."))

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
    # Reflection [environment].build_timeout_sec
    if reflection:
        if bto is None:
            out.append(finding(name, "metadata", WARN, "missing-build-timeout",
                               detail="[environment] build_timeout_sec missing — Reflection's "
                                      "schema requires it.",
                               location="task.toml [environment]",
                               fix="Add build_timeout_sec sized to a clean image build."))
        elif bto <= 0:
            out.append(finding(name, "metadata", WARN, "nonpositive-build-timeout",
                               detail=f"build_timeout_sec={bto} is a placeholder/zero — set a "
                                      "real budget for the image build.",
                               location="task.toml [environment]",
                               fix="Set build_timeout_sec to the expected clean-build seconds."))

    # ---- resources ----
    if cpus is None and mem is None and gpus is None:
        out.append(finding(name, "metadata", WARN, "no-env-resources",
                           detail="No environment resources (cpus/memory_mb/gpus) declared.",
                           location="task.toml [environment]",
                           fix="Declare at least cpus and memory_mb."))
    # template placeholders left at zero (the Harbor template ships cpus=0/memory_mb=0/…)
    zero_res = [k for k, v in (("cpus", cpus), ("memory_mb", mem),
                               ("storage_mb", storage)) if v == 0]
    if zero_res:
        out.append(finding(name, "metadata", WARN, "placeholder-zero-resource",
                           detail=f"resource field(s) {zero_res} are 0 — looks like the "
                                  "template default was never set.",
                           location="task.toml [environment]",
                           fix="Set real resource limits (cpus/memory_mb/storage_mb)."))
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

    # heavy workload vs declared memory (customer OOM'd at 4096 on Spark/Neo4j/ES)
    if mem is None or mem <= 4096:
        blob = (read_text(p["Dockerfile"]) + "\n" + read_text(p["solve.sh"]) + "\n"
                + read_text(p["test.sh"]) + "\n" + read_text(p["test_outputs.py"]))
        m = HEAVY_WORKLOAD.search(blob)
        if m:
            out.append(finding(name, "metadata", WARN, "memory-vs-workload",
                               detail=f"task uses a heavy workload (`{m.group(1)}`) but "
                                      f"memory_mb={mem if mem is not None else 'unset'} "
                                      "(<=4096) — likely OOM (Spark/Neo4j/ES/JVM need 8-16 GB).",
                               location="task.toml [environment]",
                               fix="Raise memory_mb to what the workload needs, or use a smaller "
                                   "test workload/dataset."))

    # internet flag vs what the agent is actually told to do. allow_internet governs
    # the AGENT runtime (the verifier installs deps in its own sandbox), so key off
    # the INSTRUCTION: if it tells the agent to download/fetch/use a remote service
    # while internet is off, the task is likely unrunnable as specified.
    allow_net = get(d, "environment.allow_internet")
    if allow_net is False:
        instr = read_text(p["instruction.md"]).lower()
        NEEDS_NET = re.compile(r"(https?://|\bdownload\b|\bfetch\b.{0,20}\b(url|http|remote)|"
                               r"hugging\s?face|from the internet|\bpip install\b.*\b(from|http))", re.I)
        if NEEDS_NET.search(instr):
            out.append(finding(name, "metadata", WARN, "internet-flag-contradiction",
                               detail="allow_internet=false, but instruction.md tells the agent "
                                      "to download/fetch from the network — the task may be "
                                      "impossible to solve offline.",
                               location="task.toml [environment]",
                               fix="Set allow_internet=true if the task genuinely needs the "
                                   "network, or vendor the resource into the image."))

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
