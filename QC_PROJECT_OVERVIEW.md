# Terminal Bench — QC Project Overview

**Updated:** 2026-06-28 · **Owner:** mahmoodmapara@mercor.com
**Goal:** QC all **13,433** Canonical tasks for reward-hacks + brittle/broken verifiers, label in RL Studio, make it continuous.

## Status

"Scanned" = ran the layer over the task. "Passed" = clean, no flag. (RLS-exact, world `world_2c7cdb…`.)

| Layer | Catches | Scanned | Result |
|---|---|---|---|
| **Static** (deterministic) | hacks, leaks, brittle verifiers | 13,413 | **5 passed clean** · 1,762 fixable · 11,646 review |
| **Behavioral** (oracle / no-op) | broken oracle / no-op pass | 13,413 | **2,489 broken oracles** · 0 no-op pass |
| **Semantic reviewer** (LLM) | spec↔test mismatch, weak coverage | 9,231 | ✅ **896 FAIL (9.7%)** — req↔test coverage, determinacy |
| **Trajectory FN** (LLM) | verifier wrongly FAILED correct work | 4,895 | ✅ **930 FAIL (19.1%)** — brittle/over-strict verifiers |
| **Trajectory FP** (LLM) | verifier wrongly PASSED bad work | 1,838 | ✅ **1 FAIL (0.1%)** — reward-hacking-via-verifier is rare |
| **Continuous AutoQC** | same checks on new/edited tasks | — | ✅ deployed |

> Static *scanned* all 13,413 but only **5 pass clean** — the rest carry ≥1 flag (1,762 with a concrete defect, 11,646 advisory/review). "All 13,413" = coverage, not a pass rate.

## Headline

- **Pass rate:** strict bar **1,870 (14%)** · hard bar **6,002 (45%)**. Peer QC ≈ 3k → consistent with strict.
- **Confirmed defects:** 2,489 broken oracles · 749 P0 leaks/hacks · 735/967 conftest-hackable · ~6,652 pycache leaks · ~830 projected verifier false-negatives.
- 87% carry a substantive flag → detection is *not* lenient; only the bucket labels are.

## AutoQC backfill — COMPLETE (2026-06-29)

All three runs finished overnight. Lesson banked: API caps at **10k requests/HOUR, all verbs (GET+POST), rolling** — must run ONE governed worker (`repair_autoqc.py`, <9k req/hr); concurrent polling self-starves the quota. Final: FN 930/4,895 (19.1%) · Reviewer 896/9,231 (9.7%) · FP 1/1,838 (0.1%). **Key:** over-strict verifiers (FN) are the real problem; reward-hack-via-verifier (FP) is rare.

| Run | Done | Target |
|---|---|---|
| Trajectory FN | 1,206 | 4,897 |
| Reviewer | 4,598 | 9,438 |
| Trajectory FP | 42 | 1,834 |

## Next

1. Finish backfill → final rates.
2. Write reviewer + FN/FP verdicts to RLS `qc_*` (+ `qc_top_defect`).
3. Recalibrate buckets to the strict bar.
4. Trajectory dashboards.
5. Remediate: conftest `--noconftest` template (~735) · pycache `PYTHONDONTWRITEBYTECODE=1` (~6,652) · broken oracles fix/cull (2,510).
6. `Fail QC` transitions on confirmed-broken.

See [REFLECTION_DELIVERY_PLAN.md](REFLECTION_DELIVERY_PLAN.md) for the 1,000-task delivery.

## Links & IDs

**Studio:** API `https://api.studio.mercor.com` · world `world_2c7cdb23737845ad83a9acfa1aa8c25b` · campaign `camp_4e196b1414a1499db54b43233104b0a7` · company `comp_2fa4115109d741cd94a3c409ed89e61f` · account `acct_85b680d4c5ba49a29f19c173672aebea`

**Eval batches:** [3-model](https://studio.mercor.com/admin/batch/batch_c5e617e48b0f41eaa13337976014e396) · [kimi gap](https://studio.mercor.com/admin/batch/batch_4cfaae4c2b734267bf2e1bca45df7ebe)

**AutoQC modules:** reviewer `qcspec_7bddfd703a12994dbc31fd1b` · static `qcspec_7e5dbd46cf6de18e0a08d2a6` · trajectory `qcspec_ece2ca798fd2580188abd82c` · adversary (excluded, noisy) `qcspec_e5cb0f9be6123abea7d720c4`

**Dashboards (RLS):** [Overview](https://studio.mercor.com/annotator/query-views/cqview_162cee51769040e9ae87f8376939c26e/) · [Severity](https://studio.mercor.com/annotator/query-views/cqview_4b6fab1739db42c9857cc3c0a90848bc/) · [Critical](https://studio.mercor.com/annotator/query-views/cqview_ae1174c424e344259cb6e2bf466e1f7d/) · [Fix queue](https://studio.mercor.com/annotator/query-views/cqview_7cacab27c8b34116b862c5ef980bd3ab/) · [Fix types](https://studio.mercor.com/annotator/query-views/cqview_bceed501f7dc43c7beebf8536d20d654/) · [Broken](https://studio.mercor.com/annotator/query-views/cqview_9e61788e50fe4ded80a1a2a487ddc1e0/)

## Tooling (repo `terminal-bench-qc`)

- Detectors: `skills/static-semantic-qc/scripts/` · trajectory: `skills/trajectory-audit/scripts/` · aggregation: `shared/aggregate.py`
- Runners (`studio-autoqc/`): `full_corpus_qc.py` · `run_autoqc_full.py` · `run_traj_autoqc.py` · `repair_autoqc.py` · `night_run.sh` · `studio_label.py`
- Local data (gitignored): `_local/{full_qc,behavioral_all,traj_audit,autoqc_full}/`, `.env` (keys — never commit)
