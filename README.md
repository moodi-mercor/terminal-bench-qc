# terminal-bench-qc

Find quality defects in Terminal-Bench OTS tasks **before they ship**, and report how many there are and what kinds.

**In plain terms:** a "task" is a coding problem bundled with a Dockerfile, an instruction, tests, and a reference solution. This tool reads a folder of tasks and flags the broken or cheatable ones — so bad tasks don't reach the client. Skill entry point: [`SKILL.md`](SKILL.md); the full rubric: [`QC_GUIDE.md`](QC_GUIDE.md).

## How it works

A task is flagged by a stack of checks that run **cheapest-first**, so it scales to
thousands of tasks while reserving the expensive checks for where they're needed. Each
layer catches what the cheaper one structurally cannot:

| Layer | Stance / question | What it catches | How |
|---|---|---|---|
| **1. Static** | *what shape is this?* | missing files, bad metadata, leaked answers, fake/vacuous tests, fragile `solve.sh` | `run_static_qc.py` — **9 deterministic gates**, no task run (free) |
| **2. Semantic reviewer** | *is this task correct?* | unfair/brittle tests, instruction↔test mismatch, weak golden solution; also clears Part-1 false positives | one sub-agent per task (rubric in `QC_GUIDE.md`) |
| **3. Adversary** | *can I cheat it?* | reward hacks — pass the tests without doing the work (hardcode, fake artifact, force the reward) | a *separate* sub-agent per task, opposite stance from #2 |
| **4. Dataset** | *is this original?* | overlap with public benchmarks, near-duplicates | `decontaminate.py` |
| **Behavioral** *(opt-in)* | *does it actually run right?* | weak verifiers a **no-op passes**, and **broken oracles** (the reference solution fails its own tests) | `check_behavioral.py --execute` — builds + runs the task: oracle must score 1, no-op must score 0 |

Every check returns **PASS / WARN / FAIL**; a task's verdict is its worst check. Layers
1–4 only *read* files; **behavioral is the only layer that runs the task**, so it's the
only sure catch for "a no-op passes" / "the oracle fails" — which is why it's a distinct,
opt-in step (it needs Docker and executes code).

> **Static flags are *candidates*, not verdicts** — a flagged "leak" is confirmed (or
> refuted) by the Part-2 reviewer, e.g. by checking the file is really agent-readable and
> read as the answer. Drive **recall to 100% first**, then improve precision.

> **A "clean" task only earns that label from the layers you actually ran on it.** A
> control waved through on Part-1 static alone is *unverified*, not *clean*: in the eval's
> clean-label audit, 2 public-TerminalBench controls that had only seen static turned out
> to be defective (a no-op-passes verifier and a broken oracle) — defects that by
> construction only Parts 2–3 and behavioral can catch. See `eval/README.md`
> "Clean-label audit." Run the same stack on controls that you run on candidates.

## Setup

Python 3.9+ (detectors are stdlib-only; `studio_pull.py` uses `requests`). Pulling tasks needs an RL Studio key:

```bash
cp .env.example .env      # then set RLS_KEY=...   (.env is gitignored)
```

## Run

```bash
# 1. pull tasks (or point the pipeline at a folder you already have)
python scripts/studio_pull.py --n 50 --out tasks_cache       # --list / --names also work

# 2. static QC — 9 gates + reports (run this first, always)
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

## The 9 static gates

| Gate | Flags |
|---|---|
| `check_structure` | required files missing or empty |
| `check_metadata` | bad `task.toml` — fields, time/difficulty sanity, timeouts, tags, resources |
| `check_leakage` | tests/solution copied into the image; answer files the verifier reads |
| `check_reward_hack` | tests that pass without work; gameable reward signals |
| `check_env_fairness` | leftover generators/setup scripts, exposed git history, runtime network |
| `check_portability` | `solve.sh` bugs — backgrounded daemons, PEP 668 pip, systemd assumptions, … |
| `check_dockerfile` | reproducibility smells — unpinned base/pip, `apt` no update, `ADD <url>`, `curl\|sh`, `ENTRYPOINT`, test deps in image (WARN) |
| `check_instructions` | leftover placeholders (TODO/FIXME/lorem); empty or too-short instruction |
| `check_verifier_defenses` | whether the verifier has an anti-cheat defense (mutated-rerun / recompute / source-grep / re-exec); a defended verifier suppresses adversary cheat-vectors |

## Measure precision / recall (`eval/`)

`eval/fixtures/` has known-pass and known-fail example tasks; `eval/golden_labels.csv` is the ground truth.

```bash
python scripts/run_static_qc.py eval/fixtures --out-dir /tmp/fx
python scripts/score_qc.py /tmp/fx/review-ssot.csv eval/golden_labels.csv
```

**Current results** — `TP=7  FP=0  FN=0  TN=5` → **precision 1.00, recall 1.00**:

| Fixture | QC verdict | Defect caught |
|---|---|---|
| `pass/clean-records-etl` | **PASS** | — (clean) |
| `pass/hidden-truth-verifier-only` | **PASS** | — (near-miss: truth in verify-time `tests/.truth/`, not baked) |
| `pass/webservice-healthcheck` | **PASS** | — (near-miss: server launched + waited correctly) |
| `fail/missing-solution` | **FAIL** | `missing-required-file` |
| `fail/bad-metadata` | **FAIL** | `missing-agent-timeout` (+ generic tags, seconds-as-minutes) |
| `fail/truth-baked` | **FAIL** | `truth-baked-verifier-reads` |
| `fail/copies-solution` | **FAIL** | `dockerfile-copies-solution` |
| `fail/reference-reads-truth` | **FAIL** | `reference-solve-reads-truth` |
| `fail/unconditional-reward` | **FAIL** | `unconditional-reward` |
| `fail/agent-writable-verifier` | **FAIL** | `agent-writable-verifier` (in-image grader the verifier invokes) |
| `warn/set-e-reward-abort` | **WARN** | `test-sh-set-e-reward-abort` (no-op aborts before reward write) |
| `warn/degenerate-integrity-guard` | **WARN** | `degenerate-integrity-guard` (baked `sha256sum -c` ref, agent root) |

`eval/golden_labels.csv` also labels real OTS examples — two known-defective tasks (`cloud-cost-anomaly-auditor`, `dra-calibration-integrity-pipeline`, both baked-answer leaks) plus several clean ones. Pull them with the key and score the same way:

```bash
python scripts/studio_pull.py --names @eval/ots_tasks.txt --out tasks_cache
```

Details in [`eval/README.md`](eval/README.md).

## Notes

- **Static flags are candidates, not verdicts.** Severity reflects likelihood; confirm a flagged leak actually survives the build and is exploitable before treating it as a real defect, and drive recall to 100% before tuning precision.
- **Decontamination is a lexical baseline.** `decontaminate.py` uses TF-IDF cosine over `data/decontam_corpus.jsonl`; swap the vectorizer for embeddings for an embedding-cosine version (same thresholds, same report).
- The full rubric — every check, the false-positive rules, and the per-task deep-dive prompt for the semantic layer — lives in [`QC_GUIDE.md`](QC_GUIDE.md).
