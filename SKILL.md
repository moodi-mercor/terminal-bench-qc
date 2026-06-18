---
name: terminal-bench-qc
description: >-
  Quality-control Terminal-Bench OTS tasks before delivery and flag defects
  across a dataset. Use when asked to QC, review, audit, or find defects in
  Terminal-Bench / TB2 tasks, check for leakage or brittle verifiers, or measure
  defect rate and distribution across a task set. Runs eight deterministic static
  gates first (structure, metadata, Dockerfile reproducibility, instructions,
  leakage, reward-hack, env-fairness, portability), then a per-task semantic review
  plus an adversarial reward-hack pass, plus dataset-level decontamination, and
  aggregates into an SSOT + defect-distribution report. Precision/recall are tuned
  against a labeled eval set (real public TerminalBench tasks as the clean baseline).
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
| **0 Structure/functional** | required files present & well-formed; `task.toml` metadata lint; Dockerfile reproducibility; instruction heuristics | yes | anywhere (`python`) |
| **1 Leakage/anti-cheat** | solution/test copied into image; truth baked into agent-visible paths; vacuous/gameable verifiers; env fairness; solve/test portability | yes | anywhere (`python`) |
| **2 Semantic** | instruction↔test alignment, brittleness, phantom tests, over-spec, golden-patch, realism + adversarial reward-hack red-team | sub-agent judgement | anywhere (dispatch agents) |
| **Dataset** | decontamination, cross-delivery overlap, diversity, difficulty | yes (with embeddings) | anywhere |

All layers emit the same findings schema (`scripts/common.py`), so they
aggregate into one SSOT. (Behavioral oracle/no-op runs at delivery; if those
results are available they can be dropped into `qc_out/` as findings and will
aggregate too.)

### Static gate catalog (Layers 0–1, `run_static_qc.py`)

Eight deterministic gates, cheapest first. Each emits the shared findings schema;
all are precision-tuned against the eval set (see "Precision / recall loop").

| Gate | Catches |
|---|---|
| `check_structure` | missing/empty required files; no-`FROM`/trivial Dockerfile |
| `check_metadata` | missing/garbage metadata; seconds-as-minutes time smell; over-broad category/tags; resource-cap violations; internet-flag vs instruction contradiction |
| `check_dockerfile` | reproducibility smells: unpinned base image, `apt` without update, unpinned `pip`, `ADD <url>`, `curl\|sh` (all WARN) |
| `check_instructions` | leftover placeholders (TODO/FIXME/lorem), too-short/empty instruction |
| `check_leakage` | `solution/`·`tests/` COPY'd into the image; truth baked to an agent-visible path the verifier reads; reference `solve.sh` that reads the answer instead of producing it; hint files |
| `check_reward_hack` | vacuous/no-assertion/existence-only tests; swallowed assertions; `pytest \|\| true`; unconditional/agent-writable reward; verifier importing the solution; skipped/empty-parametrized scored tests |
| `check_env_fairness` | leftover generators/setup scripts; git-history exposure; verifier hitting the network |
| `check_portability` | solve/test robustness: backgrounded-daemon-no-redirect, PEP-668 pip, server-not-started, broad `pkill`, systemd/entrypoint assumptions |

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

### 3. Semantic QC (Layer 2) — fan out sub-agents per task
Run static first, then dispatch sub-agents **per task, in parallel batches** (see
"Sub-agent orchestration" in `QC_GUIDE.md`). Two roles run per task:
- **Reviewer + FP-verification** (`sem_<task>.json`) — one agent doing two jobs: the
  **semantic deep-dive (5 checks)** (instruction↔verifier alignment, comprehensive
  tests, hygiene, golden-patch correctness — name the algorithm then compare, and
  realism), and **false-positive verification of that task's static findings** (try
  to refute each FAIL/WARN; emit `verify-refuted` for a false positive or
  `verify-confirm` for a real one).
- **Adversary** (`adv_<task>.json`) — a **separate** reward-hack red-team: it
  role-plays the eval model and tries to pass `tests/` *without* implementing the
  intended solution. A surviving hack → `semantic-cheat-vector` **WARN (a candidate,
  not a verdict)**; a robust verifier → `cheat-vector-ok`. It must first rule out the
  verifier's defenses (anti-hardcoding greps, mutated inputs, re-computation), and a
  **confirmation step** (or the delivery behavioral run) promotes a confirmed
  candidate to FAIL — raw cheat-vectors do NOT drive verdicts (see the calibration
  note in `QC_GUIDE.md` Part 3; on eval the undisciplined version flagged 49/50 tasks).

Collect each agent's JSON into `qc_out/` and re-run `aggregate.py`: refuted false
positives are auto-dropped (precision) and new semantic + cheat-vector defects fold
in (recall). This scales review across many tasks, verifies static's own output, and
red-teams each verifier — e.g. in testing it dropped a refuted leak FP and caught a
gameable verifier static rated clean.

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
  instruction against the public-benchmark corpus
  (`data/decontam_corpus.jsonl`, **1,256 instructions** spanning the four benchmarks
  NVIDIA names: Terminal-Bench (244), SWE-bench_Verified (500), LiveCodeBench (287),
  Aider polyglot (225)) by TF-IDF cosine; high similarity ⇒ possible contamination /
  trivially searchable. Rebuild/refresh the corpus with `build_decontam_corpus.py`.
  Swap the vectorizer for embeddings to get the embedding-cosine methodology
  NVIDIA/Reflection request — same thresholds, same report.
- **Near-duplicate / template reuse** — the same script flags high pairwise
  similarity *within* the set (GDM found 69 cross-delivery overlaps; Reflection
  flagged template concentration).
- **Diversity levers** — category/language/type, instruction length/constraints,
  environment size/deps — balanced, not concentrated in one shape.
- **Difficulty distribution** reported; bar enforced where contracted (e.g.
  pass@8 ≤ 0.5 on the harder model; TB3 ≤ 0.30).

> **Corpus note:** `data/decontam_corpus.jsonl` is a similarity reference (one
> `{name, source, instruction}` row per public-benchmark task), **not** an input to
> the static detectors — it holds only instruction text, not task trees. Regenerate
> it with `build_decontam_corpus.py` (SWE-bench via the HF datasets-server; LCB from
> a local `test.jsonl` LFS blob; Aider from a `git clone` of the polyglot repo).

## Precision / recall loop (action item 2)

A **276-task labeled eval set** drives this, sourced from both ends so precision and
recall are both measurable (see `eval/README.md`):
- **defective** examples from client/audit-flagged tasks — 50 real OTS tasks ground-
  truthed in `eval/run50_gt/` (`eval/run50_labels.csv`, 10 defective) — drive **recall**;
- **clean** examples from real public TerminalBench — 226 tasks normalized TB v1→TB2 by
  `scripts/import_tb_tasks.py` (`eval/tb_clean_labels.csv`) — drive **precision**;
- **synthetic fixtures** (`eval/fixtures/`) — the deterministic regression floor (must
  stay precision = recall = 1.0).

```bash
bash eval/run_combined_eval.sh        # static gates over OTS + TB, one combined score
```

1. Read the **FALSE NEGATIVES first** and tighten detectors / semantic prompt until
   recall = 100%; then drive precision. Re-run after every detector change — the
   fixtures catch regressions immediately.
2. Most static false negatives are **semantic** defects (brittle/over-constrained
   verifiers, untested requirements, weak verifiers) — undecidable by reading file
   shapes. Add the Layer 2 `sem_*` / `adv_*` findings to the findings dir before
   scoring to measure the **combined** static+semantic recall, which is the real target.
3. Measured on the 276-task set:

   | Layer | Precision | Recall |
   |---|---|---|
   | Static only | 0.50 | 0.30 |
   | **Static + semantic reviewer** | **0.625** | **0.50** |
   | + raw adversary (cheat-vectors as FAIL) | 0.19 | 1.00 |

   The **reviewer raised both** precision and recall with **0 false positives** — trust
   its FAILs directly. The **adversary** hit recall 1.0 but flagged 49/50 tasks, so its
   cheat-vectors are **WARN candidates pending confirmation**, not verdicts (Part 3
   calibration note). Report the converged combined numbers before applying to the full
   dataset.

## Beyond static + semantic (optional / delivery)

- **Behavioral gate (opt-in, confirm-to-run)** — `scripts/check_behavioral.py` is the
  only part that EXECUTES the task (oracle must score 1.0, no-op must score 0, optional
  `--reward-iso`). It is expensive (Docker per task), so by **default it runs nothing —
  it prints the plan**; you must add **`--execute`** to actually run, and should only do
  so **targeted** on flagged tasks or a sample. The authoritative full version is the
  client's delivery-stage gate.
  - plan only: `python scripts/check_behavioral.py tasks_cache --only <names>`
  - run it:   `python scripts/check_behavioral.py tasks_cache --only <names> --execute`
- **Embedding decontamination** — `decontaminate.py --method embed` swaps the TF-IDF
  vectorizer for sentence-embedding cosine (the methodology NVIDIA/Reflection ask for;
  needs `pip install sentence-transformers`).
- **Delivery report** — `python scripts/delivery_report.py <tasks> --ssot qc_out/review-ssot.csv`
  emits the difficulty / category / language distributions + diversity flags clients
  expect at handoff.

## References

- **`QC_GUIDE.md`** — the self-contained QC rubric: what every check looks for,
  the semantic-review criteria + FP rules, verdict scale, and stable defect-class
  titles. This is all the skill needs to run.
- **`data/decontam_corpus.jsonl`** — the public-benchmark corpus (1,256 instructions:
  Terminal-Bench + SWE-bench + LiveCodeBench + Aider) used by `decontaminate.py`;
  rebuilt by `scripts/build_decontam_corpus.py`.

> The fuller internal references (client-specific feedback, the standing
> delivery-gate doc, the deep terminal-bench-review rubric, golden example tasks)
> live in a local-only `references/` folder that is intentionally **not** in this
> repo. `QC_GUIDE.md` distills the non-sensitive operating criteria from them.
