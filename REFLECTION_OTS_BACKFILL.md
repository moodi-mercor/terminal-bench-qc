# Reflection-35k — OTS Backfill for Diversity Floors

**Date:** 2026-07-01 · **Question:** can we add tasks from the OTS set I QC'd to fill Reflection's category-distribution floors, keeping difficulty + QC?
**Short answer:** **Yes for 5 of the 6 thin categories.** Hardware/Embedded is the only one with no OTS supply — it should be dropped/reclassified, not backfilled.

## The problem
Reflection requires **every category ≥5%** (and each task_objective ≥10%, artifact_type ≥5%). The current ~1,000-task delivery clears the <20% cap but **7 categories sit under the 5% floor**. That's the "floor issues" — the long tail (hardware 0.1%, data-querying 0.6%, debugging 1.0%) can't be reached from the existing pool.

## The OTS supply
Source: the terminal-bench Canonical corpus I QC'd (world `world_2c7cdb…`). Filtered to **QC-clean + hard**:
- QC-clean = `healthy-hard` bucket (passed the a-priori verifier-fairness audit).
- Hard = low model pass-rate; **avg@8✓** = already has a *measured* Opus-4.8/GPT-5.4 avg@8 ≤ 0.5.
- **Zero overlap** with the delivered 1,000 — all supply below is net-new.

| Thin Reflection category | Floor gap (to 5%) | OTS supply (QC-clean, hard) | of which avg@8-measured ≤0.5 | Verdict |
|---|---|---|---|---|
| Systems, Infra & Ops | ~+21 | **303** | 76 | ✅ plenty |
| Machine Learning & AI | ~+15 | **175** | 61 | ✅ plenty |
| File & Media Operations | ~+4 | **98** | 29 | ✅ plenty |
| Debugging & Repair | ~+40 | **82** | 21 | ✅ enough (run avg@8 on ~30 more) |
| Data Querying & Databases | ~+44 | **68** | 22 | 🟡 just enough — run avg@8 on the rest |
| Build, Dependency & Release | ~+19 | **38** | 5 | 🟡 tight — supply barely covers, only 5 measured |
| **Hardware / Embedded** | ~+49 | **0** | 0 | ❌ **cannot backfill — drop/reclassify** |

(Supply is from the 2,421-task difficulty-filtered inventory; the fuller QC-clean pool is 3,997, so real supply per category is likely higher once fully category-labeled.)

## Recommendation
1. **Backfill Systems/Infra, ML/AI, File/Media, Debugging, Data-Querying from OTS** — supply is sufficient. Prefer the `avg@8-measured ≤0.5` tasks first (they already clear the hardest gate).
2. **Build/Dep/Release**: supply is thin (38); pull all viable + run avg@8; if still short, accept a slightly higher floor or merge into a neighboring category.
3. **Hardware/Embedded**: no OTS supply and only 1 task in the delivery — **reclassify that 1 task and drop the category** so it doesn't sit below the floor. (Simplest floor fix.)

## Before any backfill task ships, it must clear the same bars as the delivery
- **Difficulty:** the non-measured candidates need the **Opus-4.8/GPT-5.4 + Terminus-2 avg@8 ≤ 0.5** run (same gate as remaining-issue #1). ~half already have it.
- **QC:** they passed the a-priori audit but must go through the same static/leak pass + the **PR #6 hardening** (conftest `--noconftest`, `.dockerignore`, no baked truth).
- **Dedup:** they come from the same synth pipeline — must pass Reflection's **cosine < 0.90** (all-MiniLM-L6-v2) across `instruction.md` / `solve.sh` / `test_outputs.py` vs the existing set. Highest risk with backfill; run the near-dup check.
- **Taxonomy:** assign the exact Reflection `diversity_category` / `task_objective` / `artifact_type` labels (the OTS→Reflection mapping here is approximate).

## Data
Local: `_local/healthy_hard_tasks.csv` (3,997 QC-clean+hard), `_local/ots_difficulty/combined_tasks.csv` (2,421 categorized), `_local/ots_difficulty/eval_avg8_tasks.csv` (640 with measured avg@8 ≤ 0.5).
