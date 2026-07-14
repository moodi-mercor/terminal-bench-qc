# Reflection-35k-internal — Remaining Issues (post-PR #6)

**Date:** 2026-07-01 · **Baseline:** HEAD `41ff6d3` + [PR #6](https://github.com/mercor-code-envs/Reflection-35k-internal/pull/6)
PR #6 already closed: conftest-plant (111→0), one baked-oracle leak, missing `.dockerignore` (955→0), committed pyc cruft. This report is what's **still open**.

Priority key: **P0** = blocks delivery per spec · **P1** = spec-required, not mechanical · **P2** = hygiene/style.

---

## P0 — Blocking

### 1. Difficulty is not actually measured (`avg_at_8` is a placeholder)
- **Evidence:** all 1000 tasks have `avg_at_8 = 0.0` and `model_tested = "GPT-5.4"` — the spec's own template defaults. `0.0` trivially satisfies the ≤0.5 gate on paper, but a real avg@8 would vary; it hasn't been run.
- **Spec:** Difficulty tab — every task must be evaluated **8×** on Opus-4.8 **or** GPT-5.4 with **Terminus-2** (avg@8 ≤ 0.5). Metadata tab — "No default placeholders."
- **Fix:** run the avg@8 eval (cheap Sonnet/Haiku pre-filter, then Opus-4.8/GPT-5.4 + Terminus-2), drop tasks > 0.5, write the real score + the model actually used.
- **Scope:** 1000/1000.

### 2. `task_fc601249…` — grading corpus baked into the agent image
- **Evidence:** agent `environment/Dockerfile` runs `generator.py`, which writes `/tests/mutated_input/*` (the exact mutated grading inputs) **into the agent image**; the verifier then bundles/grades those same files. The agent can enumerate the test inputs and overfit/hardcode. (The oracle at `tests/.truth/oracle.py` does **not** leak.)
- **Spec:** Tests — "ground truth… must be re-copied into the environment to prevent modification"; Security — "No exposed ground truth."
- **Fix:** move the `/tests/mutated_input` generation out of the agent build and into `test.sh` at verify time (or ship it in the `tests/` bundle). Not auto-patched — entangles build/verify data flow, needs a test run.
- **Scope:** 1 task (only remaining instance of the class after PR #6).

---

## P1 — Spec-required

### 3. Base images not digest-pinned / not the approved refs
- **Evidence:** 0/1000 `FROM` lines carry a digest; all use mutable tags (`ubuntu:24.04`, `python:3.11-slim-bookworm`, …) from Docker Hub, not the spec's pinned `public.ecr.aws/docker/library/…@sha256:…`.
- **Spec:** Dockerfile structuring — "Always pin FROM by digest"; Base images allowlist.
- **Caveat:** the spec approves one digest per language family (e.g. Python → `3.13-slim-bookworm@sha256:…`). Tasks pinned to a different minor (3.10/3.11) can't blindly switch to 3.13 — needs a per-task check that the task still builds/passes on the approved image. Mechanical-ish but must be validated, not blind sed.
- **Scope:** 1000/1000.

### 4. Remaining leakage FAILs — re-validate before certifying
- **Counts (this run):** `truth-baked-verifier-reads` 61 · `reference-solve-reads-truth` 13 · `tests-bake-verifier-reads` 1 = **75 candidates**.
- **Status:** in the prior full ground-truth validation these classes came back **0 real** (hidden `/tests/.truth/` oracle + anti-hardcode checks; instruction-provided smoke fixtures). The counts shifted with the v2 restructuring (truth-baked 44→61), so they should be **spot re-validated**, not assumed clean.
- **Also:** the 4 `TAMPERABLE_MINOR` tasks from v1 (`task_2bba9b`, `task_b0dfea`, `task_d982971` still FAIL; `task_38a8c3` cleared) read an agent-writable baked file as truth for a secondary sub-check — re-copy that truth from `tests/` at verify time.

---

## P2 — Hygiene & style

| Issue | Count | Spec | Fix |
|---|---|---|---|
| `pycache-residue` (build runs+removes a py generator, no `PYTHONDONTWRITEBYTECODE`) | 137 | Dockerfile structuring | add `ENV PYTHONDONTWRITEBYTECODE=1` (safe, 1 line) — **offered as a follow-up batch** |
| verifier deps in agent image (`test-deps-in-image`) | 739 | Tests — "Separate verifier preferred" | move pytest/etc. to a separate verifier image |
| unpinned pip | 739 | "Pinned dependencies" | pin versions |
| broad `chmod -R` | 535 | "Avoid broad chmod" | scope chmod to specific files |
| `solve.sh` embedded heredocs | 980 | Solution — "Appropriate decomposition" | move source into COPY'd files |
| structured-output schema undocumented | 78 | Instructions — "Structured outputs specified" | document the schema in the prompt/env spec |
| instruction uses relative paths | 9 | Instructions — "Absolute paths" | make absolute |

---

## Dataset-level — Diversity

Category **cap** (<20%) is met (max 17.1%). Floors are **not**:
- **7 categories under the 5% floor:** file-and-media 4.6%, ml-and-ai 3.5%, build-dep 3.1%, systems-infra 2.9%, debugging 1.0%, data-querying 0.6%, hardware 0.1%.
- **task_objective < 10% floor:** compare-or-select, configure, debug, deploy-or-operate, fix, migrate, optimize, refactor, test.
- **artifact_type < 5% floor:** build-system-metadata, container-or-venv, hardware/firmware, media, repo-history, service-or-daemon, shell-env, test-suite, document-or-report, model-or-checkpoint (several).
- **Fix:** rebalance — either add tasks in the thin categories or fold the long tail into fewer categories to clear the ≥5% / ≥10% / ≥5% floors.

---

## Suggested order
1. Run the avg@8 eval (#1) — biggest gate, gates everything else.
2. Fix `task_fc601249` (#2) + re-validate the 75 leakage candidates (#4).
3. Base-image digest-pin with per-task build check (#3).
4. Optional easy batch: `PYTHONDONTWRITEBYTECODE=1` (#P2 pycache).
5. Diversity rebalance once the eval-culled final set is known.
