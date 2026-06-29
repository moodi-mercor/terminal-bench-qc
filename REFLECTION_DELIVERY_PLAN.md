# Reflection — Terminal Bench Delivery Plan

**Updated:** 2026-06-28 · **Owner:** mahmoodmapara@mercor.com
**Spec:** [INT] Reflection — Mercor Terminal Specification (Harbor format)

## Target — read this first

The spec's **near-term goal is 100 *perfect* samples** (pass *all* QC criteria), then eval on
GPT-5.4 / Opus-4.8. **1,000 is the ramp target**, reached via the ramp plan (not a single delivery).

| Milestone | What | Owner |
|---|---|---|
| 1. Revise 100 samples | run QC skill on the current 100, remove/rework broken | Moodi |
| 2. Backfill from inventory | QC a net-new 500 pulled from RLS **by diversity**, iterate to **100 perfect** | Moodi |
| 3. Eval the 100 | avg@8 on GPT-5.4 + Opus-4.8 (Terminus-2) via RLS | Keya |
| 4. Ramp plan | tasks/week × yield% to scale toward 1,000+ | Aditya |

**Ramp math (from spec):** 1 container · 45 min/task · 180 tasks/wk · **50% yield → ~90 perfect/wk**.
→ 1,000 perfect ≈ **~11 container-weeks** (scale by adding containers).

## Sourcing funnel (our QC as the gate)

The spec literally says "run QC skill against the samples" — **our QC pipeline is the gating tool.**

| Gate | Count |
|---|---|
| Canonical corpus | 13,413 |
| − hard defects (reward-hack / brittle / broken oracle) | −8,388 |
| **QC-clean pool** | **5,025** |
| QC-clean with difficulty pre-filter data | 4,497 |

**Difficulty pre-filter** (our eval-batch pass-rates — opus-4.7/gpt-5.5/gemini/kimi; a *proxy*, spec allows cheap pre-filter):

| Band | Count | Use |
|---|---|---|
| Trivial (>80% pass) | 1,595 | exclude |
| Moderate (50–80%) | 674 | likely excluded (avg@8 ≤ 0.5) |
| **Hard ≤50% pass** | **545** | candidate pool |
| Frontier (0% pass, oracle valid) | 1,683 | candidate (verify oracle) |

→ **~2,228 tasks** survive QC + the hard pre-filter — ample supply to source 100 perfect (and ramp toward 1,000).

## Hard requirements from the spec

**Difficulty (authoritative):** every task **avg@8 ≤ 0.5** measured with **Opus 4.8 OR GPT-5.4** (128k tokens,
max/xhigh effort) + **Terminus-2** agent, 8 attempts. Our batch pass-rates are only a pre-filter; authoritative
difficulty = the avg@8 eval (`batch_d52db25…`, GPT-5.4/Opus-4.8) — re-run for the final set.

**Diversity (dataset-level, all must hold):**
- Pairwise cosine **< 0.90** (all-MiniLM-L6-v2) across `instruction.md`, `solve.sh`, `test_output.py`.
- **Category:** none >20%, none <5%; within a category no **subcategory** >20%; cover subcategories where feasible.
- **Task-objective** labels: each present in **≥10%** of tasks. **Artifact-type** labels: each in **≥5%**.
- Taxonomy: 14 categories (Software Eng, Debugging, Build/Release, Systems/Infra, Data ETL, DB, Data Science,
  ML/AI, Model Training, Security, Scientific Computing, Math/Formal, Hardware/Embedded, File/Media, Games, Regulated/Business).

**Quality (per task — our QC maps 1:1):** Harbor layout · deterministic functional verifier writing
`/logs/verifier/reward.txt` (1/0, not pre-created, graceful-fail) · anti-cheat (no leaked tests/solution/ground-truth,
no mutable verifier, ground-truth re-copied, no PATH-intercept/reward-write bypass) · oracle passes / no-op fails /
deterministic · approved **digest-pinned base image** (10 allowed) · Dockerfile rules (single apt block, multi-stage,
`.dockerignore`, no `apt upgrade`, no broad chmod, no heredoc source, bake verifier deps) · instruction ≤1,500 tokens,
outcome-based, absolute paths, no solution hints.

## What we already have vs. need

- ✅ **QC pipeline** detects nearly all the quality/anti-cheat criteria (our reward-hack + brittle-verifier + leak detectors = the spec's anti-cheat/functional-verification/no-leakage rules).
- ✅ **Difficulty pre-filter** from existing rollouts (no new compute).
- ✅ **Clean candidate pool** (~2,228 hard + QC-clean).
- ⏳ **Diversity tagging** — need category/subcategory/objective/artifact labels per task + the cosine-similarity check (all-MiniLM-L6-v2).
- ⏳ **Authoritative avg@8** — re-run the chosen set on Opus-4.8/GPT-5.4 + Terminus-2.
- ⏳ **Harbor hygiene fixes** — base image, metadata, `.dockerignore` (bulk-fixable, not disqualifying).

## Immediate next steps

1. Identify the current **100 samples** + run the QC pipeline over them; cull/rework the broken.
2. Backfill gaps from the **~2,228 clean-hard pool**, selecting **by diversity** (category 5–20%, objective ≥10%, artifact ≥5%, cosine <0.90).
3. Add a **diversity-tagging pass** (taxonomy labels) + the cosine-similarity dedup check.
4. Hand the locked set to **Keya** for the GPT-5.4/Opus-4.8 avg@8 eval.
5. Draft the **ramp plan** numbers with Aditya (yield-adjusted weekly throughput).

## Reference

- Difficulty pre-filter source: `batch_c5e617e…` (3-model) + `batch_4cfaae…` (kimi). Authoritative avg@8: `batch_d52db25…` (GPT-5.4/Opus-4.8).
- QC pool: `qc_*` custom_fields on world `world_2c7cdb23737845ad83a9acfa1aa8c25b`.
- Prior Reflection QC: ext-reflection-tbench (100 tasks, qc-touchups).
- See [QC_PROJECT_OVERVIEW.md](QC_PROJECT_OVERVIEW.md) for the QC pipeline.
