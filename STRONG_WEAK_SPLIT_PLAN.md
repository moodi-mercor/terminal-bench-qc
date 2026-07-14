# Plan: Opus-4.8 (strong) / GLM-5.2 (weak) task split

**Goal:** deliver Terminal-Bench tasks where Claude Opus 4.8 solves ≥1/5 trials (solvable)
and GLM-5.2 solves <3/5 trials (still hard for the weak model), excluding tasks that are
already easy for frontier models (>80% pass rate).

**Date:** 2026-07-05 · World `world_2c7cdb23737845ad83a9acfa1aa8c25b` ([OTS] Terminal Bench, 13,433 tasks)

---

## What we already have (no new compute needed)

| Asset | Where | Status |
|---|---|---:|
| QC-healthy task pool (defective buckets excluded) | `_local/opus_qc/good_tasks_opus.csv` | 8,169 tasks |
| Opus 4.8 historical evals (66,318 trajectories scanned) | `_local/opus_qc/opus_{pass,fail,none}.txt` | pass 5,845 / fail 1,379 / none 945 |
| Opus pass@8 batch over the 945 no-history healthy-hard tasks | `batch_f01e4418…` (dispatched 2026-07-01) | **check if complete → rerun `pull_opus_evals.py`** |
| Difficulty labels (healthy-easy / healthy-hard) from avg@8 GPT-5.4/Opus runs | `summary.json` buckets | done |
| Conftest reward-hack vuln bulk-patched (`--noconftest`) across 9,011 tasks | `bulk_patch_conftest.py` | done — but **pre-patch Opus passes may be hacks** |

Key derived numbers from `_local/opus_qc/summary.json`:
- healthy-hard ∧ opus_pass: **1,259** — immediately eligible on the strong side
- healthy-hard ∧ opus_none: **945** — pending batch_f01e4418 results
- healthy-hard ∧ opus_fail: **1,143** — excluded (Opus never solved), unless a bo5 top-up flips some
- healthy-easy (3,909 opus_pass + …): **excluded** as ">80% frontier pass rate"

## What's missing

1. **GLM-5.2 runs — the main new compute.** No GLM-5.2 trajectories exist. Studio has a
   GLM 5.1 orchestrator (`orch_14e61ca2`, Baseten) but its endpoint was flaky
   (see `dispatch_gap_batch.py` notes). Need to confirm a GLM-5.2 orchestrator exists
   in Studio; if not, ask platform to register the endpoint.
2. **Opus trial-count normalization.** Historical Opus evidence is pass@N for varying N,
   not a clean bo5. `opus_pass` (≥1 pass ever) is a reasonable proxy for bo5≥1; if the
   client insists on exact bo5, dispatch a fresh pass@5 Opus batch over the candidate pool.
3. **Hack-verification of Opus passes.** Passes recorded before the conftest patch may be
   reward hacks. Cross-check passing trajectories against `conftest_label.py` /
   `audit_gameable.py` labels; drop or re-run suspect tasks.

## Phases

### Phase 0 — Close out existing batches (0.5 day)
- Check `batch_f01e4418` status; when done, rerun `pull_opus_evals.py` → updates
  opus_pass/fail/none for the 945 no-history tasks.
- Result: complete Opus a/b classification over all 8,169 healthy tasks.

### Phase 1 — Candidate pool (0.5 day)
- Pool = healthy-hard ∧ opus_pass (post Phase 0). Estimate **~1,500–2,000 tasks**
  (1,259 + some fraction of the 945).
- Filter out tasks whose only Opus passes are conftest-suspect (pre-patch + gameable label).
- Optionally add healthy-unknown-difficulty ∧ opus_pass (677) after an avg@8 easy-screen.

### Phase 2 — GLM-5.2 pass@5 dispatch (2–4 days wall clock)
- Confirm/register GLM-5.2 orchestrator in Studio.
- New dispatcher (clone `dispatch_opus_none_pass8.py` → `dispatch_glm52_pass5.py`):
  pool × 5 runs ≈ **8,000–10,000 trajectories**. At the observed ~5 dispatches/min
  querier limit this is the long pole; budget 2–3 days of batch runtime + retry slack.
- Pull scores with the `pull_eval_scores_direct.py` pattern (scores live in
  `trajectory_output.score`, binary 0/1).

### Phase 3 — (only if exact bo5 required) Opus pass@5 top-up (parallel with Phase 2)
- Dispatch fresh Opus 4.8 pass@5 over the same pool so both sides are clean 5-trial runs
  on post-conftest-patch task versions. Doubles compute; skip if pass@N history is accepted.

### Phase 4 — Filter + QC the survivors (1 day)
- Keep: Opus ≥1/5 ∧ GLM-5.2 ≤2/5.
- Spot-audit passing trajectories on ~5% of survivors for reward-hacks
  (`audit_gameable.py`, trajectory verifier-audit skill).
- Expected yield: unknown until GLM runs land; if GLM-5.2 ≈ GLM-5.1/Terminus-tier,
  most healthy-hard tasks should stay under 3/5 — rough guess **60–85% of pool**,
  i.e. **~1,000–1,700 tasks**.

### Phase 5 — Sample batch + delivery (0.5 day)
- Export a **50–100 task validation batch** first via `export_full.py` /
  `export_proven.py` (task tree + both models' 5-trial scores per task).
- On client sign-off, export the full set.

## Timeline estimate (to quote back)

- Sample batch (50–100 tasks, both models × 5 trials, verified): **~1 week**
- Full set (~1–2k tasks): **~2–3 weeks**, dominated by GLM dispatch throughput.

## Open questions to resolve first
1. Is a GLM-5.2 endpoint available as a Studio orchestrator? (blocker for Phase 2)
2. Does the client accept historical Opus pass@N as bo5≥1 evidence, or require fresh 5-trial runs? (decides Phase 3)
3. Agent scaffold for GLM-5.2 — Terminus-2 (as in their eval setup) or Studio default?
