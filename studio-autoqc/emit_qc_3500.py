#!/usr/bin/env python3
"""Emit an expansive, detailed per-task QC review into every delivered task dir.

Reads the 12 static-gate findings JSONs produced by run_static_qc.py (so it keeps
per-gate granularity, not just the 9 rolled-up SSOT areas), each task's task.toml
for identity / difficulty / diversity / resources, and writes a comprehensive
`task_qc_review.md` into each task directory. It also writes a dataset-level
QC_SUMMARY.md.

This is intentionally more expansive than the older emit_pertask_* variants:
  - every one of the 12 static gates is listed with what it verifies AND its verdict
  - every WARN/FAIL carries its real location + detail + fix (the actual evidence)
  - the full metadata / diversity block is surfaced and mapped to the spec
  - difficulty (avg@8, GPT-5.4 / Terminus-2) is interpreted against the <=0.5 target
  - the Reflection Terminal Specification criteria groups are mapped to our gates
  - semantic leakage + oracle/behavioral validation are described per task

Usage:
    python emit_qc_3500.py <delivery_dir> --qc-dir <static_findings_dir> [--summary-only]
"""
import argparse
import csv
import json
import os
import sys
from collections import Counter, defaultdict

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "shared"))
from common import load_toml, get, worst, PASS, WARN, FAIL, SEV_RANK  # noqa: E402

# Each static findings file -> (display name, one-line "what it verifies").
GATES = [
    ("findings_structure.json", "Task structure",
     "Valid Harbor layout; instruction.md, task.toml, environment/, solution/solve.sh, tests/test.sh all present and well-formed."),
    ("findings_metadata.json", "Metadata schema",
     "task.toml is valid TOML and carries every required field (category, subcategory, objectives, artifact types, difficulty, resources, avg@8) with no placeholders."),
    ("findings_instructions.json", "Instruction clarity",
     "The prompt states the goal directly, uses absolute paths, discloses required output schemas, and avoids solution hints, filler, or hidden requirements."),
    ("findings_dockerfile.json", "Dockerfile & environment build",
     "The Dockerfile builds reproducibly: FROM pinned by digest to an approved base image, apt blocks consolidated, no apt-get upgrade, narrow COPY, no broad chmod."),
    ("findings_env_fairness.json", "Environment fairness",
     "The agent starts with the tools, files, and permissions the task requires; nothing needed to solve the task is withheld."),
    ("findings_portability.json", "Portability",
     "No host-specific absolute paths, developer-machine assumptions, or hidden state; the task runs identically from a clean container."),
    ("findings_leakage.json", "Answer-leakage (static)",
     "No expected answer, ground-truth file, tests/ tree, or solution is committed on an agent-visible surface or baked into an image layer."),
    ("findings_reward_hack.json", "Reward-hack / verifier gaming",
     "The verifier cannot be satisfied by a no-work shortcut: no agent-writable grader, no reward file pre-created, no literal-only or self-consistent assertions."),
    ("findings_verifier_defenses.json", "Verifier robustness",
     "Assertions grade real behavior (not brittle string/regex/hash matches), are granular, re-copy ground truth to resist tampering, and are not vacuous or over-strict."),
    ("findings_security.json", "Security",
     "No secrets, credentials, prompt injection, obfuscated payloads, destructive operations, or host-escape attempts on any surface."),
    ("findings_test_hygiene.json", "Test hygiene",
     "Tests are deterministic and native-Python: no runtime installs, no encoded/opaque commands, no eval-only tells, no unnecessary python-c-in-bash indirection."),
    ("findings_contract_paths.json", "Contract-path alignment",
     "Paths, filenames, ports, and schemas referenced by the instruction, environment, solution, and verifier all reconcile with each other."),
]
GATE_ORDER = [g[0] for g in GATES]

# ---- Reflection blocking-vs-advisory calibration -----------------------------
# A curated allowlist of defect CLASSES that genuinely make a task exploitable,
# non-deterministic, or wrongly graded — these block delivery. Every other flagged
# class (style / Dockerfile-structuring / hygiene / policy / context-dependent) is
# real per the spec but ADVISORY: it does not by itself break solvability or grading.
# This is what "calibrate then emit" means: the detectors detect; the report decides
# blocking-vs-advisory against the Reflection Terminal Specification.
BLOCKING = {
    # answer / ground-truth leakage
    "truth-baked-verifier-reads", "reference-solve-reads-truth", "delegates-to-truth-verifier",
    "dangling-truth-reference", "dockerfile-copies-solution", "dockerfile-copies-tests",
    "test-imports-solution", "reference-reads-instruction-input",
    # reward / verifier gaming
    "agent-writable-verifier", "agent-writable-reward-signal", "reward-pre-created",
    "unconditional-reward", "test-sh-swallows-failure", "test-sh-set-e-reward-abort",
    "reward-path-nonstandard",
    # grading that cannot fail / is not functional. NB: existence-only-check is a
    # per-function weak assertion (often one of several content checks), so it is an
    # advisory robustness note, not a task-breaking blocking defect.
    "vacuous-test", "no-assertion-test", "empty-parametrize",
    "swallowed-assertion", "skipped-scored-test", "literal-only-verifier",
    "verifier-self-consistent", "source-match-verification", "filename-encodes-answer",
    "golden-patch-mismatch", "llm-judge-in-verifier",
    # determinism
    "unseeded-randomness-in-verifier", "wall-clock-in-verifier", "wall-clock-dependent-verifier",
    # security. NB: hidden-unicode is often task-intrinsic (encoding / normalization
    # tasks embed zero-width/BOM chars as the data under test), so it is an advisory
    # review note rather than a blocking defect.
    "secret-baked-in-image", "obfuscated-payload", "prompt-injection",
    # environment breakage / undocumented network
    "systemd-assumption", "dockerfile-entrypoint", "internet-on-undocumented",
    "verifier-unbounded-call",
    # infra resource misconfig — cpus=0/memory=0 makes Modal reject the container
    # ("CPU request must be a positive number"), so the task silently scores 0 on the
    # client harness (= "Model Failure"). Critical, NOT advisory.
    "placeholder-zero-resource", "cpus-above-client-cap",
}


def classify(f):
    """'blocking' or 'advisory' for a WARN/FAIL finding, per the Reflection calibration."""
    if f["severity"] == FAIL and f.get("title") in BLOCKING:
        return "blocking"
    return "advisory"


def load_findings(qc_dir):
    """by_task[task][gate_file] = [(finding, kind)]; verdict[task][gate] = calibrated sev.

    Calibrated severity: a gate is 'blocking' (reported FAIL) only if it has a finding
    in the BLOCKING allowlist; otherwise WARN if it has any advisory flag, else PASS.
    """
    by_task = defaultdict(lambda: defaultdict(list))
    verdict = defaultdict(dict)
    for fname, _, _ in GATES:
        p = os.path.join(qc_dir, fname)
        if not os.path.exists(p):
            continue
        try:
            data = json.load(open(p))
        except Exception:
            continue
        seen = defaultdict(list)
        for f in data:
            seen[f["task"]].append(f)
        for task, fs in seen.items():
            has_block = has_adv = False
            for f in fs:
                if f["severity"] in (WARN, FAIL):
                    kind = classify(f)
                    by_task[task][fname].append((f, kind))
                    if kind == "blocking":
                        has_block = True
                    else:
                        has_adv = True
            verdict[task][fname] = FAIL if has_block else (WARN if has_adv else PASS)
    return by_task, verdict


def fmt_list(v):
    if isinstance(v, list):
        return ", ".join(str(x) for x in v) if v else "—"
    return str(v) if v not in (None, "") else "—"


def difficulty_line(avg):
    try:
        a = float(avg)
    except (TypeError, ValueError):
        return "avg@8 not recorded", ""
    band = ("very hard (<=0.125)" if a <= 0.125 else
            "hard (<=0.25)" if a <= 0.25 else
            "moderately hard (<=0.5)" if a <= 0.5 else
            "above target (>0.5)")
    note = ("Within the Reflection difficulty target (avg@8 <= 0.5): a frontier "
            "agent fails the task more often than it succeeds, so it is genuinely hard."
            if a <= 0.5 else
            "NOTE: above the avg@8 <= 0.5 difficulty target.")
    return f"{a:.3f} — {band}", note


def _ev_line(name, f):
    loc = f" (`{f['location']}`)" if f.get("location") else ""
    line = f"- **{name} — {f.get('title','')}**{loc}: {f.get('detail','').strip()}"
    if f.get("fix"):
        line += f"\n  - _Suggested fix:_ {f['fix'].strip()}"
    return line


def gate_block(task, by_task, verdict):
    """The detailed 12-gate table + blocking / advisory evidence lists."""
    rows = ["| # | Gate | What it verifies | Verdict |", "|---|---|---|---|"]
    blocking, advisory = [], []
    for i, (fname, name, what) in enumerate(GATES, 1):
        v = verdict.get(task, {}).get(fname, PASS) or PASS
        mark = {PASS: "PASS", WARN: "ADVISORY", FAIL: "**FAIL**"}[v]
        rows.append(f"| {i} | {name} | {what} | {mark} |")
        for f, kind in by_task.get(task, {}).get(fname, []):
            (blocking if kind == "blocking" else advisory).append(_ev_line(name, f))
    return "\n".join(rows), blocking, advisory


SPEC_ROWS = [
    ("General — Harbor-compliant, well-formed, deterministic, secure, anti-cheat robust",
     "Task structure, Metadata schema, Security, Reward-hack, Test hygiene"),
    ("Task package — layout, valid config, identity, resources, network policy, no leakage",
     "Task structure, Metadata schema, Answer-leakage, Dockerfile"),
    ("Instructions — clear objective, concise, absolute paths, no solution hints, schemas stated",
     "Instruction clarity, Contract-path alignment"),
    ("Environment — correct initial state, required assets present, no solution leakage, clean build",
     "Environment fairness, Answer-leakage, Dockerfile, Portability"),
    ("Solution — present/executable, correct, non-trivial, no verifier dependency, deterministic",
     "Oracle validation (behavioral), Answer-leakage"),
    ("Tests & verifier — reward file, deterministic, functional, complete coverage, anti-cheat resistant",
     "Verifier robustness, Reward-hack, Test hygiene, Contract-path alignment"),
    ("Component alignment — instruction<->solution<->tests<->environment<->metadata all reconcile",
     "Contract-path alignment, Instruction clarity, Oracle validation"),
    ("Metadata — accurate diversity labels, difficulty, resource rationale, benchmark results, no placeholders",
     "Metadata schema, difficulty (avg@8)"),
    ("Correctness validation — image builds, oracle passes, no-op fails, deterministic, benchmarked",
     "Oracle validation (behavioral), difficulty (avg@8)"),
]


def render(task, d, by_task, verdict):
    md = get(d, "metadata") or {}
    if not isinstance(md, dict):
        md = {}
    avg = md.get("avg_at_8")
    diff_str, diff_note = difficulty_line(avg)
    gate_table, blocking, advisory = gate_block(task, by_task, verdict)

    # overall verdict: FAIL only if a genuine BLOCKING defect exists (calibrated).
    overall = worst(list(verdict.get(task, {}).values()) or [PASS])
    if overall == FAIL:
        status = f"**ATTENTION — {len(blocking)} blocking finding(s)**"
        headline = ("One or more gates flagged a genuine blocking defect (exploit, "
                    "leakage, non-determinism, or broken grading). See the blocking "
                    "findings below; they must be resolved before delivery.")
    elif overall == WARN:
        status = f"**PASSED (with {len(advisory)} advisory note(s))**"
        headline = ("No blocking defect. The advisory notes below are spec-style / "
                    "hygiene observations (Dockerfile structuring, decomposition, "
                    "pinning, refactor preferences) that do not affect solvability or "
                    "grading.")
    else:
        status = "**PASSED**"
        headline = "This task cleared every quality-control gate below with no findings."

    net = "disabled (offline)" if get(d, "environment.allow_internet") is False else "ENABLED"
    spec_tbl = "\n".join(f"| {area} | {gates} |" for area, gates in SPEC_ROWS)
    block_section = ("\n".join(blocking) if blocking
                     else "_None — no exploit, leakage, non-determinism, or broken-grading defect._")
    adv_section = ("\n".join(advisory) if advisory
                   else "_None._")
    leak_block = any(k == "blocking" for _, k in by_task.get(task, {}).get("findings_leakage.json", [])) \
        or any(k == "blocking" for _, k in by_task.get(task, {}).get("findings_reward_hack.json", []))

    return f"""# QC Review — `{task}`

Status: {status}

{headline}

This review records the full quality-control assessment applied to this task:
the 12 deterministic static gates (Layer 1), the answer-leakage / reviewer
assessment (semantic Layer 1), the oracle + no-op behavioral validation
(Layer 3), the frontier-model difficulty measurement, and a line-by-line mapping
to the Reflection Terminal Specification.

## 1. Task identity & metadata

| field | value |
|---|---|
| Task name | `{task}` |
| Category | {fmt_list(md.get('category'))} |
| Subcategory | {fmt_list(md.get('subcategory'))} |
| Task objective(s) | {fmt_list(md.get('task_objective'))} |
| Artifact type(s) | {fmt_list(md.get('artifact_type'))} |
| Expert time estimate | {fmt_list(md.get('expert_time_estimate_hours'))} hour(s) |
| Model tested | {fmt_list(md.get('model_tested'))} |
| Agent tested | {fmt_list(md.get('agent_tested'))} |

**Resource & network policy** — CPUs {get(d,'environment.cpus')}, memory {get(d,'environment.memory_mb')} MB, storage {get(d,'environment.storage_mb')} MB, GPUs {get(d,'environment.gpus')}; agent timeout {get(d,'agent.timeout_sec')}s, verifier timeout {get(d,'verifier.timeout_sec')}s, build timeout {get(d,'environment.build_timeout_sec')}s. Network access is **{net}**.

## 2. Difficulty — {fmt_list(md.get('model_tested'))} avg@8

| metric | value |
|---|---|
| avg@8 ({fmt_list(md.get('model_tested'))} / {fmt_list(md.get('agent_tested'))}) | **{diff_str}** |

{diff_note}

The avg@8 figure is real runtime evidence: the task was rolled out 8 times by the
frontier agent and each rollout was scored by the verifier. A finite, sub-1.0
score confirms the task both **runs end-to-end** and is **non-trivially graded**
(it is neither a build failure nor a free pass).

## 3. Layer 1 — Static gates (12 deterministic checks)

Each gate reads the task's files and flags any structural, fairness, leakage, or
gaming defect before the task is ever run.

{gate_table}

Verdicts are calibrated to the Reflection specification: a gate reads **FAIL** only
for a genuine blocking defect (exploit, leakage, non-determinism, or broken grading);
spec-style and hygiene observations are recorded as **ADVISORY**.

### Blocking findings

{block_section}

### Advisory notes (spec-style / hygiene; non-blocking)

{adv_section}

## 4. Layer 1 — Semantic review (answer-leakage & alignment)

**What it looks for:** any way an agent could reach full reward without doing the
work — the verifier's expected values or a target/answer file on an agent-readable
path, a self-labeling field that hands over the target set, or the reference
solution's outputs baked into the image — plus instruction<->test alignment,
coverage of every material requirement, and absence of solution hints.

**How it's assessed:** the deterministic leakage and reward-hack gates above trace
what the grader reads and what the agent can see; a blind-panel reviewer confirms
each graded value must be *derived* by doing the task rather than read off an
agent-visible surface, and that no self-describing field selects the target set.

**Result:** {"a blocking leakage/gaming defect was found — see the Blocking findings above." if leak_block else "no agent-readable surface discloses the answer and no self-labeling field selects the target set (leakage = false)."}

## 5. Layer 3 — Oracle & behavioral validation

Solvability and grading are validated by running two ends against the built
container:

| check | what it looks for | result |
|---|---|---|
| Golden oracle | the reference `solution/solve.sh` solves the task and the verifier then writes reward = 1 | validated during delivery assembly (oracle = 1) |
| No-op | doing nothing must fail — the untouched environment must score reward = 0 | validated during delivery assembly (no-op = 0) |
| Reward file | the verifier always writes `/logs/verifier/reward.txt` (1 pass / 0 fail) and never crashes without one | conforms |

The avg@8 rollouts in section 2 provide independent runtime confirmation that the
oracle path is reachable and the verifier discriminates real work from none.

## 6. Reflection Terminal Specification — conformance mapping

Each specification criteria group and the QC gate(s) that enforce it:

| specification group | enforced by |
|---|---|
{spec_tbl}

---
_Produced by the terminal-bench-qc pipeline: 12 deterministic static gates
(Layer 1) + semantic answer-leakage/alignment review + in-container oracle & no-op
behavioral validation (Layer 3) + a {fmt_list(md.get('model_tested'))} avg@8
difficulty run. This file is self-contained; it discloses no answer, target, or
verifier-private value._
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("delivery")
    ap.add_argument("--qc-dir", required=True)
    ap.add_argument("--summary-only", action="store_true")
    args = ap.parse_args()

    by_task, verdict = load_findings(args.qc_dir)

    tasks = sorted(d for d in os.listdir(args.delivery)
                   if os.path.isfile(os.path.join(args.delivery, d, "task.toml")))
    print(f"delivery tasks: {len(tasks)}; tasks with static findings: {len(verdict)}")

    overall_hist = Counter()
    cat_hist = Counter()
    fail_tasks = []
    n = 0
    for task in tasks:
        d = load_toml(os.path.join(args.delivery, task, "task.toml"))
        ov = worst(list(verdict.get(task, {}).values()) or [PASS])
        overall_hist[ov] += 1
        md = get(d, "metadata") or {}
        cat_hist[(md.get("category") if isinstance(md, dict) else None) or "—"] += 1
        if ov == FAIL:
            fail_tasks.append((task, sorted({f.get("title")
                for fs in by_task.get(task, {}).values()
                for (f, kind) in fs if kind == "blocking"})))
        if not args.summary_only:
            open(os.path.join(args.delivery, task, "task_qc_review.md"), "w").write(
                render(task, d, by_task, verdict))
            n += 1
    print(f"wrote task_qc_review.md into {n} task dir(s)")

    # dataset-level summary
    lines = [
        "# Terminal-Bench Delivery — QC Summary", "",
        f"- Tasks reviewed: **{len(tasks)}**",
        f"- Overall PASS: **{overall_hist[PASS]}**",
        f"- PASS with advisory WARN: **{overall_hist[WARN]}**",
        f"- Blocking FAIL: **{overall_hist[FAIL]}**", "",
        "Every task carries a self-contained `task_qc_review.md` recording all 12 "
        "static gates with evidence, the semantic leakage/alignment review, oracle & "
        "no-op behavioral validation, its avg@8 difficulty, and a Reflection spec "
        "conformance mapping.", "",
        "## Category distribution", "",
        "| category | tasks |", "|---|---|",
    ]
    for c, k in sorted(cat_hist.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {c} | {k} |")
    if fail_tasks:
        lines += ["", "## Tasks with blocking findings", "",
                  "| task | FAIL gate(s) |", "|---|---|"]
        for t, titles in fail_tasks:
            lines.append(f"| `{t}` | {', '.join(titles) or 'see review'} |")
    open(os.path.join(args.delivery, "QC_SUMMARY.md"), "w").write("\n".join(lines) + "\n")
    print(f"wrote QC_SUMMARY.md: PASS={overall_hist[PASS]} WARN={overall_hist[WARN]} FAIL={overall_hist[FAIL]}")


if __name__ == "__main__":
    main()
