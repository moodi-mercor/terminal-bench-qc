---
name: terminal-bench-qc
description: >-
  Quality-control Terminal-Bench OTS tasks before delivery and flag defects
  across a dataset. Use when asked to QC, review, audit, or find defects in
  Terminal-Bench / TB2 tasks, run oracle/no-op validation, check for leakage or
  brittle verifiers, or measure defect rate and distribution across a task set.
  Runs deterministic static gates first (structure, metadata, leakage), then the
  behavioral gate (oracle=1 / no-op=0), then semantic review, and aggregates into
  an SSOT + defect-distribution report. Built from cross-client feedback (NVIDIA,
  MAI, GDM, Reflection).
---

# Terminal-Bench OTS — Quality Control

Flag QC defects in Terminal-Bench OTS tasks, deterministically where possible,
and report **how many** defects exist and **their distribution**. The pipeline
runs cheap deterministic checks first and escalates to behavioral and semantic
review — functional checks before semantic ones, as the action items require.

> **The single most important gate is behavioral:** run the reference solution
> through its own verifier (oracle → 1.0) and the verifier against an untouched
> container (no-op → 0.0), 3× with saved logs, on the client's target infra.
> Almost every client escalation traces to one of these not being run. Static
> checks alone have repeatedly missed the defects that shipped.

## Layered pipeline

| Layer | What | Deterministic? | Where it runs |
|---|---|---|---|
| **0 Structure/functional** | required files present & well-formed; `task.toml` metadata lint | yes | anywhere (`python`) |
| **1 Leakage/anti-cheat** | solution/test copied into image; truth baked into agent-visible paths | yes | anywhere (`python`) |
| **2 Behavioral** | oracle=1, no-op=0 (3× w/ logs); reward-isolation; adversarial exploit | runner is deterministic; needs `harbor`+Modal/Docker | delivery env |
| **3 Semantic** | instruction↔test alignment, brittleness, phantom tests, over-spec | sub-agent judgement | anywhere (dispatch agents) |
| **Dataset** | decontamination, cross-delivery overlap, diversity, difficulty | yes (with embeddings) | anywhere |

All layers emit the same findings schema (`scripts/common.py`), so they
aggregate into one SSOT regardless of where they ran.

## Task structure (TB2 / harbor)

```
<task>/
  task.toml                  # metadata: difficulty, tags, time, timeouts, resources
  instruction.md             # the prompt shown to the agent
  environment/Dockerfile     # the ONLY thing the agent sees (+ what it COPYs)
  environment/...            # data / setup scripts
  tests/test.sh              # verifier entry point  } mounted at VERIFY time only —
  tests/test_outputs.py      # pytest verifier       } never in the agent image
  solution/solve.sh          # oracle reference       } oracle-only
```
The agent's container is built solely from `environment/Dockerfile`. `tests/` and
`solution/` are mounted by harbor at verification time — they must never appear in
the Dockerfile. This constraint decides most leak/anti-cheat verdicts.

## How to run

### 1. Get tasks
```bash
# pull a sample of OTS tasks from RL Studio (needs RLS_KEY in .env)
python scripts/studio_pull.py --n 50 --out tasks_cache
# or point the pipeline at an existing tasks/ folder
```

### 2. Static QC (Layers 0-1) — run this first, always
```bash
python scripts/run_static_qc.py tasks_cache --out-dir qc_out
```
Writes per-gate findings JSON + `review-ssot.csv`, `review-ssot.md`,
`defect-distribution.md` into `qc_out/`.

### 3. Behavioral QC (Layer 2) — the decisive gate
Two backends, same findings + verdict rules:
```bash
# (a) where harbor + Modal/Docker exist (delivery env):
python scripts/behavioral_gates.py tasks_cache --env modal --runs 3 \
    --log-dir qc_out/behavioral_logs --out qc_out/findings_behavioral.json

# (b) where only Docker exists (no harbor): a minimal harbor-equivalent.
#     Needs a Docker daemon — e.g. `colima start --vm-type=vz --vz-rosetta`.
python scripts/local_harbor.py tasks_cache --runs 3 \
    --log-dir qc_out/behavioral_logs --out qc_out/findings_behavioral.json
```
`local_harbor.py` reconstructs the run contract from the task files (build →
apply `solution/solve.sh` → overlay `tests/` → run `tests/test.sh` → read
reward), because `harbor` is internal and not pip-installable. Then
reward-isolation and the adversarial exploit pass — see
`references/behavioral-runbook.md`.

### 4. Semantic QC (Layer 3) — dispatch one sub-agent per task
Follow `references/semantic-review-prompt.md`. Hand each agent the task dir + its
static findings; collect the returned JSON into `qc_out/`.

### 5. Aggregate everything
```bash
python scripts/aggregate.py qc_out --out-dir qc_out
```
Re-run after adding behavioral/semantic findings so the SSOT and distribution
include every layer.

## Verdict rules

Each finding is **PASS** / **WARN** / **FAIL**. Per area, the verdict is the
worst finding; a task's overall verdict is the worst area.

- **FAIL** — must fix before delivery: missing required file, missing metadata,
  oracle < 1.0, no-op > 0.0, solution/test leak the agent can read, untested hard
  requirement, phantom/over-constrained verifier, hardcoded solution.
- **WARN** — fix but non-blocking: over-broad tag, single spelling error,
  unpinned test dep, time estimate slightly out of range, resource above client cap.
- **PASS** — clean or trivially cosmetic.

Static flags are **candidates, not verdicts** — confirm leak survival (build +
`ls`) and exploitability before treating a static FAIL as a real defect, and
down-rank flags the semantic pass refutes. Drive **recall to 100% first** (catch
every real defect), then improve precision.

## Outputs

- `review-ssot.csv` — one row per task, per-area verdicts + critical issues.
- `review-ssot.md` — per-task findings with locations and fixes.
- `defect-distribution.md` — defect rate + counts by area and by defect class.
  This is the answer to "how many defects / what distribution".

## Dataset-level checks (run once across a delivery)

```bash
python scripts/decontaminate.py tasks_cache --out qc_out/findings_dataset.json
```

- **Decontamination** vs public benchmarks — `decontaminate.py` scores each task
  instruction against the public Terminal-Bench corpus
  (`references/golden/decontam_corpus.jsonl`, 244 tasks from TB core v0.1.x +
  v0.2.x) by TF-IDF cosine; high
  similarity ⇒ possible contamination / trivially searchable. Swap the vectorizer
  for embeddings to get the embedding-cosine methodology NVIDIA/Reflection
  request — same thresholds, same report.
- **Near-duplicate / template reuse** — the same script flags high pairwise
  similarity *within* the set (GDM found 69 cross-delivery overlaps; Reflection
  flagged template concentration).
- **Diversity levers** — category/language/type, instruction length/constraints,
  environment size/deps — balanced, not concentrated in one shape.
- **Difficulty distribution** reported; bar enforced where contracted (e.g.
  pass@8 ≤ 0.5 on the harder model; TB3 ≤ 0.30).

> **Public TB format note:** the public corpus is Terminal-Bench v1 (`task.yaml`,
> root `Dockerfile`, `run-tests.sh`, `solution.sh`) — a different layout from the
> TB2/harbor OTS tasks the detectors parse. Public tasks under
> `references/golden/public-tb-core/` are **design references + the decontam
> corpus**, not inputs to the static detectors.

## Precision / recall loop (action item 2)

1. Pull ~50 tasks; run static + behavioral + semantic QC.
2. Manually label each task (`labels.csv`: `task,is_defect[,notes]`).
3. `python scripts/score_qc.py qc_out/review-ssot.csv labels.csv` — read the
   FALSE NEGATIVES first; tighten detectors/semantic prompt until recall = 100%.
4. Expand to 100 → 200 tasks; report the converged precision + recall before
   applying across the full dataset.

## References

- `references/qc-checklist.md` — the 7-dimension review contract (metadata,
  dockerfile, instructions, tests, solution, anti-cheat, brittleness) + verdict
  roll-up this skill implements.
- `references/qc-gate-reference.md` — the standing pre-delivery gate, per-client
  rationale (start here for the "why").
- `references/client-feedback.md` — consolidated NVIDIA/MAI/GDM/Reflection feedback.
- `references/terminal-bench-review-SKILL.md` + `-CHECKLIST.md` — the deep
  semantic rubric, FP rules, deep-trace patterns (Layer 3 authority).
- `references/behavioral-runbook.md` — Layer 2 protocol (oracle/no-op,
  reward-isolation, adversarial, run discipline).
- `references/semantic-review-prompt.md` — Layer 3 sub-agent dispatch.
- `references/studio-data-access.md` — RL Studio API for pulling OTS tasks.
- `references/golden/` — public TB/TB2 tasks: golden examples + decontam corpus.
