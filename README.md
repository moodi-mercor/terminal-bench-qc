# terminal-bench-qc

Quality-control Terminal-Bench OTS (TB2 / harbor) tasks before delivery, and report the defect rate and distribution across a task set.

## Overview

This is a QC skill for Terminal-Bench **OTS** tasks in the **TB2 / harbor** format. It flags defects deterministically where possible, escalates to semantic (sub-agent) review where judgement is needed, runs dataset-level decontamination, and aggregates everything into a single source of truth plus a defect-distribution report — the answer to "how many defects, and what's the distribution?".

The checks are built from cross-client validation feedback (NVIDIA, MAI, GDM, Reflection) and the first and second 5k validation reports. The non-sensitive operating criteria are distilled into [`QC_GUIDE.md`](QC_GUIDE.md); the skill entry point is [`SKILL.md`](SKILL.md).

The pipeline is ordered cheapest-first so it scales: deterministic static gates run with no task execution, then optional per-task semantic review, then a once-per-delivery dataset pass.

> **Behavioral validation is out of scope here.** The runtime oracle/no-op gate (reference solution → reward 1.0, untouched container → 0.0) runs at the **delivery stage** on the client's target infra (harbor + Modal). This skill does not duplicate it; it is the static + semantic + dataset pass that runs cheaply on large task sets and feeds the delivery gate. If oracle/no-op results are available, they can be dropped into the findings dir and will aggregate with the rest.

## How it works

| Layer | What it checks | Runs | How |
|---|---|---|---|
| **Static** | Structure, metadata, leakage/anti-cheat, reward-hack screen, environment fairness, portability — deterministic, no task run | Cheaply, at scale | `scripts/run_static_qc.py` (6 gates) |
| **Semantic** | Instruction↔test alignment, brittle/phantom/weak tests, over-specification, golden-patch correctness, realism — sub-agent judgement | Per task | Deep-dive routine in `QC_GUIDE.md` (dispatch one sub-agent per task) |
| **Dataset** | Public-benchmark contamination + near-duplicate / template reuse | Once per delivery | `scripts/decontaminate.py` |
| **Behavioral** | Oracle/no-op reward gate | Out of scope here — **delivery stage**, on client infra (harbor + Modal) | — |

All layers emit the same findings schema (`scripts/common.py`), so they aggregate into one SSOT.

The static layer runs six gates, cheapest first: `check_structure.py`, `check_metadata.py`, `check_leakage.py`, `check_reward_hack.py`, `check_env_fairness.py`, `check_portability.py`.

### TB2 / harbor task shape

The agent's container is built **only** from `environment/Dockerfile`; `tests/` and `solution/` are mounted by harbor at verify time and must never appear in the image. That constraint decides most leakage / anti-cheat verdicts.

```
<task>/
  task.toml                  # metadata: difficulty, tags, time, timeouts, resources
  instruction.md             # the prompt shown to the agent
  environment/Dockerfile     # the ONLY thing the agent sees (+ what it COPYs)
  environment/...            # data / setup scripts
  tests/test.sh              # verifier entry  } mounted at VERIFY time only —
  tests/test_outputs.py      # pytest verifier } never in the agent image
  solution/solve.sh          # oracle reference } oracle-only
```

## Repo layout

Committed files (`tasks_cache/`, `qc_out/`, `eval/runs/`, `.env`, and `references/` are gitignored):

```
terminal-bench-qc/
├── SKILL.md                       # skill entry point: how to run the QC pipeline
├── QC_GUIDE.md                    # self-contained rubric: every check, FP rules, defect-class titles
├── README.md                      # this file
├── .env.example                   # template for .env (RLS_KEY)
├── .gitignore
├── data/
│   └── decontam_corpus.jsonl      # public Terminal-Bench corpus (reference for decontaminate.py)
└── scripts/
    ├── common.py                  # shared: TOML reader, task discovery, findings schema, severity
    ├── studio_pull.py             # pull OTS tasks from RL Studio (needs RLS_KEY)
    ├── run_static_qc.py           # one-command entry point: runs all 6 static gates, then aggregates
    ├── check_structure.py         # gate: required files present & non-empty; Dockerfile non-trivial
    ├── check_metadata.py          # gate: task.toml lint — fields, time/difficulty sanity, timeouts, resources, tags
    ├── check_leakage.py           # gate: tests/solution copied into image; baked answers the verifier reads; hint files
    ├── check_reward_hack.py       # gate: vacuous tests, swallowed verifier, unconditional/agent-writable reward
    ├── check_env_fairness.py      # gate: leftover generators, uncleaned setup scripts, exposed git history, runtime network
    ├── check_portability.py       # gate: solve.sh/test defects (backgrounded daemons, PEP 668 pip, systemd, etc.)
    ├── decontaminate.py           # dataset: contamination vs public corpus + in-set near-duplicates
    ├── aggregate.py               # roll all findings JSON into review-ssot.csv/.md + defect-distribution.md
    ├── score_qc.py                # precision/recall vs manual labels (drive recall to 100% first)
    └── labels.template.csv        # template for manual ground-truth labels (score_qc.py)
```

## Setup

```bash
git clone <repo-url> terminal-bench-qc
cd terminal-bench-qc
```

Requires **Python 3.9+** (the detectors are stdlib-only; `studio_pull.py` uses `requests`).

Tasks are pulled from RL Studio, which needs an `RLS_KEY`. Copy the template and fill it in — `.env` is gitignored:

```bash
cp .env.example .env
# then edit .env and set RLS_KEY=...
```

## Quickstart

```bash
# 1. Pull a sample of OTS tasks from RL Studio (reads RLS_KEY from .env)
python scripts/studio_pull.py --n 50 --out tasks_cache
#    or list what's available first:        python scripts/studio_pull.py --list
#    or pull specific tasks by name:        python scripts/studio_pull.py --names taskA,taskB --out tasks_cache
#    (already have a tasks/ folder? skip this and point the pipeline at it)

# 2. Static QC — run this first, always (6 deterministic gates, then aggregate)
python scripts/run_static_qc.py tasks_cache --out-dir qc_out

# 3. (optional) Semantic QC — dispatch one sub-agent per task using the
#    deep-dive routine + ready-to-run prompt in QC_GUIDE.md. Collect each
#    agent's returned findings JSON into qc_out/.

# 4. (optional) Dataset-level decontamination + near-duplicate scan
python scripts/decontaminate.py tasks_cache --out qc_out/findings_dataset.json

# 5. Re-aggregate after adding semantic / dataset / behavioral findings
python scripts/aggregate.py qc_out --out-dir qc_out

# 6. (optional) Score QC predictions against manual labels
python scripts/score_qc.py qc_out/review-ssot.csv labels.csv
```

Each finding is **PASS** / **WARN** / **FAIL**. A task's verdict for an area is its worst finding; its overall verdict is its worst area. Static flags are **candidates, not verdicts** — confirm a flagged leak actually survives the build and is exploitable before treating it as a real defect. Drive **recall to 100% first**, then improve precision.

## Outputs

`run_static_qc.py` and `aggregate.py` write into `qc_out/`:

- **`review-ssot.csv`** — one row per task, per-area verdicts + critical issues.
- **`review-ssot.md`** — per-task findings with locations and fixes.
- **`defect-distribution.md`** — defect rate + counts by area and by defect class. This is the answer to "how many defects / what distribution".

Defect classes use stable titles (see the list in `QC_GUIDE.md`) so the histogram groups cleanly.

## The golden eval set (`eval/`)

`eval/` holds a golden labeled set (`eval/golden_labels.csv`) — known-pass and known-fail tasks — for measuring QC precision and recall: pull the golden tasks, run static (+ semantic) QC, then score the output against the golden labels with `score_qc.py`. Read the FALSE NEGATIVES first and tighten the detectors / semantic prompt until recall hits 100%, then report the converged precision.

> The golden eval set is **in progress**. Until it lands, run the same precision/recall loop with your own `labels.csv` — copy `scripts/labels.template.csv` (`task,is_defect[,notes]`) and label the tasks you reviewed. `eval/runs/` is gitignored.

## Scope & limitations

- **Behavioral is delivery-stage.** The oracle/no-op reward gate runs on the client's target infra (harbor + Modal), not here. Broken solve paths and vacuous-but-runtime defects are caught there — this skill flags the statically-decidable half and feeds that gate.
- **Static flags are candidates, not verdicts.** Severity reflects likelihood, not certainty; confirm leak survival (build + `ls`) and exploitability, and down-rank anything the semantic pass refutes.
- **Decontamination is a lexical baseline.** `decontaminate.py` uses TF-IDF cosine over the public corpus in `data/decontam_corpus.jsonl`. Swap the vectorizer for sentence embeddings to get the embedding-cosine methodology some clients request — same thresholds, same report format. The public corpus is Terminal-Bench v1 layout and is a decontamination reference, not an input to the static detectors.
- **`references/` is local-only.** The fuller internal docs (client-specific feedback, the delivery-gate doc, the deep terminal-bench-review rubric, golden example tasks) live in a `references/` folder that is intentionally gitignored and **not** in this repo. `QC_GUIDE.md` distills the non-sensitive operating criteria from it.

## Provenance

Built from cross-client validation feedback — NVIDIA, MAI, GDM, and Reflection — plus the first and second 5k validation reports. The recurring defect classes those reviews surfaced (baked-answer leaks, tests/solution copied into the image, backgrounded-daemon pipe hangs, PEP 668 pip failures, public-benchmark contamination, cross-delivery overlap, brittle/phantom verifiers) are exactly what the gates in `scripts/` and the semantic rubric in `QC_GUIDE.md` target.
