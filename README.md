# terminal-bench-qc

Find quality defects in Terminal-Bench OTS tasks **before they ship**, and report how many there are and what kinds.

**In plain terms:** a "task" is a coding problem bundled with a Dockerfile, an instruction, tests, and a reference solution. This tool reads a folder of tasks and flags the broken or cheatable ones — so bad tasks don't reach the client. Skill entry point: [`SKILL.md`](SKILL.md); the full rubric: [`QC_GUIDE.md`](QC_GUIDE.md).

## How it works

Checks run cheapest-first, so it scales to thousands of tasks:

| Layer | What it catches | How |
|---|---|---|
| **Static** | missing files, bad metadata, leaked answers, fake/vacuous tests, fragile `solve.sh` | `run_static_qc.py` — 6 deterministic gates, no task run |
| **Semantic** | unfair/brittle tests, instruction↔test mismatch, weak golden solution | one sub-agent per task (rubric in `QC_GUIDE.md`) |
| **Dataset** | overlap with public benchmarks, near-duplicates | `decontaminate.py` |

Every check returns **PASS / WARN / FAIL**; a task's verdict is its worst check.

> Static flags are *candidates*, not final verdicts — a flagged "leak" is confirmed by building the image and checking the file is really readable. Drive **recall to 100% first**, then improve precision.

## Setup

Python 3.9+ (detectors are stdlib-only; `studio_pull.py` uses `requests`). Pulling tasks needs an RL Studio key:

```bash
cp .env.example .env      # then set RLS_KEY=...   (.env is gitignored)
```

## Run

```bash
# 1. pull tasks (or point the pipeline at a folder you already have)
python scripts/studio_pull.py --n 50 --out tasks_cache       # --list / --names also work

# 2. static QC — 6 gates + reports (run this first, always)
python scripts/run_static_qc.py tasks_cache --out-dir qc_out

# 3. (optional) dataset decontamination + near-duplicate scan
python scripts/decontaminate.py tasks_cache --out qc_out/findings_dataset.json

# 4. (optional) semantic review — dispatch a sub-agent per task with the deep-dive
#    prompt in QC_GUIDE.md, drop each agent's JSON into qc_out/, then re-aggregate:
python scripts/aggregate.py qc_out --out-dir qc_out
```

**Outputs** (in `qc_out/`):
- `review-ssot.csv` — one row per task, a verdict per check.
- `review-ssot.md` — the findings, with file/line and how to fix.
- `defect-distribution.md` — how many defects, broken down by type *(the headline answer)*.

## Task shape (TB2 / harbor)

The agent only sees `environment/`. `tests/` and `solution/` are mounted at grading time and must **never** be baked into the image — that one rule decides most leak verdicts.

```
<task>/
  task.toml              # metadata: difficulty, timeouts, resources
  instruction.md         # the prompt the agent sees
  environment/Dockerfile # the only thing the agent sees
  tests/                 # verifier      — grading-time only
  solution/solve.sh      # reference answer — grading-time only
```

## The 6 static gates

| Gate | Flags |
|---|---|
| `check_structure` | required files missing or empty |
| `check_metadata` | bad `task.toml` — fields, time/difficulty sanity, timeouts, tags, resources |
| `check_leakage` | tests/solution copied into the image; answer files the verifier reads |
| `check_reward_hack` | tests that pass without work; gameable reward signals |
| `check_env_fairness` | leftover generators/setup scripts, exposed git history, runtime network |
| `check_portability` | `solve.sh` bugs — backgrounded daemons, PEP 668 pip, systemd assumptions, … |

## Measure precision / recall (`eval/`)

`eval/fixtures/` has known-pass and known-fail example tasks; `eval/golden_labels.csv` is the ground truth.

```bash
python scripts/run_static_qc.py eval/fixtures --out-dir /tmp/fx
python scripts/score_qc.py /tmp/fx/review-ssot.csv eval/golden_labels.csv
```

**Current results** — `TP=6  FP=0  FN=0  TN=1` → **precision 1.00, recall 1.00**:

| Fixture | QC verdict | Defect caught |
|---|---|---|
| `pass/clean-records-etl` | **PASS** | — (clean) |
| `fail/missing-solution` | **FAIL** | `missing-required-file` |
| `fail/bad-metadata` | **FAIL** | `missing-agent-timeout` (+ generic tags, seconds-as-minutes) |
| `fail/truth-baked` | **FAIL** | `truth-baked-verifier-reads` |
| `fail/copies-solution` | **FAIL** | `dockerfile-copies-solution` |
| `fail/reference-reads-truth` | **FAIL** | `reference-solve-reads-truth` |
| `fail/unconditional-reward` | **FAIL** | `unconditional-reward` |

`eval/golden_labels.csv` also labels real OTS examples — two known-defective tasks (`cloud-cost-anomaly-auditor`, `dra-calibration-integrity-pipeline`, both baked-answer leaks) plus several clean ones. Pull them with the key and score the same way:

```bash
python scripts/studio_pull.py --names @eval/ots_tasks.txt --out tasks_cache
```

Details in [`eval/README.md`](eval/README.md).

## Notes

- **Static flags are candidates, not verdicts.** Severity reflects likelihood; confirm a flagged leak actually survives the build and is exploitable before treating it as a real defect, and drive recall to 100% before tuning precision.
- **Decontamination is a lexical baseline.** `decontaminate.py` uses TF-IDF cosine over `data/decontam_corpus.jsonl`; swap the vectorizer for embeddings for an embedding-cosine version (same thresholds, same report).
- The full rubric — every check, the false-positive rules, and the per-task deep-dive prompt for the semantic layer — lives in [`QC_GUIDE.md`](QC_GUIDE.md).
