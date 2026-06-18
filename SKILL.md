---
name: terminal-bench-qc
description: >-
  Quality-control Terminal-Bench OTS tasks before delivery and flag defects
  across a dataset. Use when asked to QC, review, audit, or find defects in
  Terminal-Bench / TB2 tasks, check for leakage or brittle verifiers, or measure
  defect rate and distribution across a task set. Runs deterministic static gates
  first (structure, metadata, leakage), then semantic review, plus dataset-level
  decontamination, and aggregates into an SSOT + defect-distribution report.
  Behavioral oracle/no-op validation is a separate delivery-stage gate and is out
  of scope here. Built from cross-client feedback (NVIDIA, MAI, GDM, Reflection).
---

# Terminal-Bench OTS — Quality Control

Flag QC defects in Terminal-Bench OTS tasks, deterministically where possible,
and report **how many** defects exist and **their distribution**. The pipeline
runs cheap deterministic checks first and escalates to semantic review —
functional checks before semantic ones, as the action items require.

> **Scope note — behavioral validation is out of scope here.** The oracle/no-op
> runtime gate (reference solution → 1.0, untouched container → 0.0) is run at the
> delivery stage on the client's target infra (harbor + Modal), so this skill does
> not duplicate it. This skill is the **static + semantic + dataset** QC pass that
> runs cheaply on large task sets and feeds the delivery gate.

## Layered pipeline

| Layer | What | Deterministic? | Where it runs |
|---|---|---|---|
| **0 Structure/functional** | required files present & well-formed; `task.toml` metadata lint | yes | anywhere (`python`) |
| **1 Leakage/anti-cheat** | solution/test copied into image; truth baked into agent-visible paths | yes | anywhere (`python`) |
| **2 Semantic** | instruction↔test alignment, brittleness, phantom tests, over-spec | sub-agent judgement | anywhere (dispatch agents) |
| **Dataset** | decontamination, cross-delivery overlap, diversity, difficulty | yes (with embeddings) | anywhere |

All layers emit the same findings schema (`scripts/common.py`), so they
aggregate into one SSOT. (Behavioral oracle/no-op runs at delivery; if those
results are available they can be dropped into `qc_out/` as findings and will
aggregate too.)

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

### 3. Semantic QC (Layer 2) — dispatch one sub-agent per task
Use the rubric + the **deep-dive routine (7 checks)** and ready-to-run prompt in
`QC_GUIDE.md`. Hand each agent the task dir + its static findings; collect the
returned JSON into `qc_out/`. The deep-dive adds trajectory/`evals` reading, an
adversarial cheat-spawn, golden-patch correctness (identify the algorithm first,
then compare), and a setup→runtime→cleanup fairness probe.

### 4. Aggregate everything
```bash
python scripts/aggregate.py qc_out --out-dir qc_out
```
Re-run after adding semantic (or any externally-supplied behavioral) findings so
the SSOT and distribution include every layer.

## Verdict rules

Each finding is **PASS** / **WARN** / **FAIL**. Per area, the verdict is the
worst finding; a task's overall verdict is the worst area.

- **FAIL** — must fix before delivery: missing required file, missing metadata,
  solution/test leak the agent can read, untested hard requirement,
  phantom/over-constrained verifier, hardcoded solution. (Broken solve path /
  vacuous tests are caught by the delivery-stage oracle/no-op gate.)
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
> TB2/harbor OTS tasks the detectors parse. The committed corpus lives at
> `data/decontam_corpus.jsonl`; it is a decontamination reference, not an input to
> the static detectors.

## Precision / recall loop (action item 2)

1. Pull ~50 tasks; run static + semantic QC.
2. Manually label each task (`labels.csv`: `task,is_defect[,notes]`).
3. `python scripts/score_qc.py qc_out/review-ssot.csv labels.csv` — read the
   FALSE NEGATIVES first; tighten detectors/semantic prompt until recall = 100%.
4. Expand to 100 → 200 tasks; report the converged precision + recall before
   applying across the full dataset.

## References

- **`QC_GUIDE.md`** — the self-contained QC rubric: what every check looks for,
  the semantic-review criteria + FP rules, verdict scale, and stable defect-class
  titles. This is all the skill needs to run.
- **`data/decontam_corpus.jsonl`** — the public Terminal-Bench corpus (244 tasks)
  used by `decontaminate.py`.

> The fuller internal references (client-specific feedback, the standing
> delivery-gate doc, the deep terminal-bench-review rubric, golden example tasks)
> live in a local-only `references/` folder that is intentionally **not** in this
> repo. `QC_GUIDE.md` distills the non-sensitive operating criteria from them.
