# terminal-bench-qc

Find quality defects in Terminal-Bench OTS tasks **before they ship**, and report
how many there are and what kinds.

**In plain terms:** a "task" is a coding problem bundled with a Dockerfile, an
instruction, tests, and a reference solution. This repo QCs a folder of tasks (and
the eval rollouts they produce) and flags the broken or cheatable ones — so bad tasks
don't reach the client.

## Three layers, cheapest first

QC is split into three skills, one per stage. Each catches what the cheaper one
structurally cannot, and each is a self-contained skill you can run on its own.

| Layer | Skill | Question | Catches | Runs |
|---|---|---|---|---|
| **1 · Static + Semantic** | [`skills/static-semantic-qc`](skills/static-semantic-qc/SKILL.md) | *what shape is this, and is it correct?* | missing files, bad metadata, leaked answers, vacuous tests, fragile `solve.sh` (static); unfair/brittle verifiers, instruction↔test mismatch, reward hacks (semantic) | reads files — 9 deterministic gates + reviewer/adversary sub-agents |
| **2 · Trajectory** | [`skills/trajectory-audit`](skills/trajectory-audit/SKILL.md) | *what happened when models actually ran it?* | verifiers too strict (fail correct work), too weak (pass cheats), confirmed against real rollouts | reads a completed Studio eval batch — triage + judge sub-agents |
| **3 · Behavioral** | [`skills/behavioral-qc`](skills/behavioral-qc/SKILL.md) | *does it actually run right?* | a **no-op passes**, a **broken oracle** (reference fails its own tests) | **runs the task** in Docker (opt-in, `--execute`) |

Plus a dataset pass in Layer 1 (decontamination vs public benchmarks, near-dups) and
`studio-autoqc/`, which deploys these layers into RL Studio as live AutoQC modules.

> A task only earns "clean" from the **layers you actually ran on it**. A control
> waved through on static alone is *unverified*, not clean. Run the same stack on
> controls that you run on candidates.

## The defect gate — caught once, never advances

Every layer writes findings in **one shared schema** ([`shared/common.py`](shared/common.py))
into one cumulative report. Two rules make the layers a real staged filter:

1. **Sticky FAIL.** [`shared/aggregate.py`](shared/aggregate.py) merges all findings
   **worst-verdict-wins** — a task's verdict per area is its worst finding, and its
   overall is its worst area. So a `FAIL` from *any* layer wins, and a later layer's
   `PASS` can **never** downgrade it back to clean.
2. **Quarantine vs promote.** [`shared/gate.py`](shared/gate.py) reads the merged
   verdict and partitions the set:
   - `quarantine.txt` — tasks that are `FAIL`, tagged with the **layer + check** that
     caught them. They're pulled; they do **not** advance.
   - `promote.txt` — the survivors, which become the input the **next layer** runs on
     (this is also what makes the expensive layers cheap: they only see still-clean
     tasks).

So a defect caught in Layer 1 is quarantined and never re-enters Layer 2/3 as clean,
and the final report shows *where* each defect was caught (cross-layer provenance via
the optional `layer` field).

## Layout

```
terminal-bench-qc/
├── README.md                 # this file — the 3-layer index + gate contract
├── QC_GUIDE.md               # the rubric: every check, FP rules, sub-agent prompts (Layer 1)
├── data/                     # decontamination corpus (1,256 public-benchmark instructions)
├── eval/                     # precision/recall harness — fixtures, labels, run_*.sh
├── shared/                   # the cross-layer contract
│   ├── common.py             #   canonical finding schema + severity model (single source)
│   ├── aggregate.py          #   worst-verdict merge → SSOT + defect-distribution report
│   ├── gate.py               #   quarantine FAILs / promote the rest to the next layer
│   └── score_qc.py           #   precision / recall vs labels
├── skills/
│   ├── static-semantic-qc/   # Layer 1
│   ├── trajectory-audit/     # Layer 2
│   └── behavioral-qc/        # Layer 3
└── studio-autoqc/            # deploy the layers into RL Studio AutoQC (operational)
```

`data/`, `eval/`, and the `_local/` working tree (gitignored: task caches, run
outputs, sensitive references) are shared across the layers.

## Run it end-to-end

Each skill's `SKILL.md` is the full guide; the typical chain is:

```bash
# Layer 1 — static (always first), into a shared qc_out/
python skills/static-semantic-qc/scripts/run_static_qc.py <tasks> --out-dir qc_out
#         + semantic reviewer/adversary sub-agents (prompts in QC_GUIDE.md) → qc_out/
python shared/aggregate.py qc_out                 # merge to the SSOT
python shared/gate.py      qc_out                 # → quarantine.txt + promote.txt

# Layer 3 — behavioral, only on what survived (opt-in, builds Docker):
python skills/behavioral-qc/scripts/check_behavioral.py <tasks> \
    --only "$(paste -sd, qc_out/promote.txt)" --execute
python shared/aggregate.py qc_out && python shared/gate.py qc_out

# Layer 2 — trajectory, when a completed eval batch exists (needs RLS_KEY):
python skills/trajectory-audit/scripts/pull_batch.py <batch_id> --out qc_out/attempts.jsonl
python skills/trajectory-audit/scripts/triage.py     qc_out/attempts.jsonl --out-dir qc_out
#         + judge sub-agents (prompt in that SKILL.md) → qc_out/
```

**Outputs** (in `qc_out/`): `review-ssot.csv` (one row per task, a verdict per area),
`review-ssot.md` (findings with file/line + fixes), `defect-distribution.md` (how many
defects, by type — the headline answer), `quarantine.txt` / `promote.txt` (the gate).

## Measure precision / recall (`eval/`)

`eval/fixtures/` are the deterministic regression floor; `eval/*.csv` are labeled real
tasks. The fixtures must stay **precision = recall = 1.0**:

```bash
python skills/static-semantic-qc/scripts/run_static_qc.py eval/fixtures --out-dir /tmp/fx
python shared/score_qc.py /tmp/fx/review-ssot.csv eval/golden_labels.csv   # TP=7 FP=0 FN=0 TN=5
bash eval/run_combined_eval.sh    # static-only over real OTS + public-TB
bash eval/run_expanded_eval.sh    # static + committed semantic findings, 200-row blind set
```

On the 200-row blind set the static+semantic layer currently reproduces ≈**0.94 precision
/ 0.97 recall** (labels-relative — a clean-label audit found 2 controls that had only
seen static were actually defective, which is exactly what Layers 2–3 exist to catch).
Static-only recall (~0.30) is just the static ceiling; its misses are semantic. Details
in [`eval/README.md`](eval/README.md).

## Setup

Python 3.9+ (detectors are stdlib-only; `studio_pull.py` / trajectory pull use
`requests`). Studio access needs a key:

```bash
cp .env.example .env      # then set RLS_KEY=...   (.env is gitignored)
```

The full rubric — every check, the false-positive rules, and the per-task sub-agent
prompts — lives in [`QC_GUIDE.md`](QC_GUIDE.md).
