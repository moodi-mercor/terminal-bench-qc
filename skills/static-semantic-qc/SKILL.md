---
name: terminal-bench-qc
description: >-
  Quality-control Terminal-Bench OTS tasks before delivery and flag defects
  across a dataset. Use when asked to QC, review, audit, or find defects in
  Terminal-Bench / TB2 tasks, check for leakage or brittle verifiers, or measure
  defect rate and distribution across a task set. Runs nine deterministic static
  gates first (structure, metadata, Dockerfile reproducibility, instructions,
  leakage, reward-hack, env-fairness, portability, verifier-defense), then a
  per-task semantic review plus an adversarial reward-hack pass, plus
  dataset-level decontamination, and aggregates into an SSOT + defect-distribution
  report. Precision/recall are tuned against a labeled eval set (real public
  TerminalBench tasks as the clean baseline). Behavioral oracle/no-op validation
  is a separate delivery-stage gate and is out of scope here. Built from
  cross-client feedback (NVIDIA, MAI, GDM, Reflection).
---

# Terminal-Bench OTS — Quality Control (Layer 1: static + semantic)

Flag QC defects in Terminal-Bench OTS tasks — deterministically where possible —
and report **how many** defects exist and **their distribution**.

**This skill is Layer 1 — the static + semantic + dataset QC pass**, the cheapest
and first stage. It only *reads* tasks, so it scales to thousands. Two sibling
skills go deeper on the suspects it can't decide by reading:

- **Layer 2 — [`trajectory-audit`](../trajectory-audit/SKILL.md)**: confirms verifier
  fairness against *real eval rollouts* once a batch has run.
- **Layer 3 — [`behavioral-qc`](../behavioral-qc/SKILL.md)**: *runs* the task (oracle
  → 1.0, no-op → 0) to catch what only shows up at execution time.

All three emit the same finding schema and roll up through `../../shared/aggregate.py`
+ `../../shared/gate.py`: a `FAIL` from any layer is **sticky** (a later `PASS` can't
clear it), and the gate quarantines FAILed tasks so they don't advance mislabeled as
clean. See the repo [`README.md`](../../README.md) "defect gate" for the contract.

For what each check looks for and how to judge it, see **[`QC_GUIDE.md`](../../QC_GUIDE.md)** — the canonical rubric.

## Task structure (TB2 / harbor)

Understand this first — most leak/anti-cheat verdicts follow directly from it.

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

The agent's container is built **solely** from `environment/Dockerfile`. `tests/`
and `solution/` are mounted by harbor at verification time — they must never
appear in the Dockerfile. Anything that leaks them (or the answer) into the agent
image is a defect.

## The pipeline

Four parts, cheapest first — the same Parts 1–4 used in
[`QC_GUIDE.md`](../../QC_GUIDE.md). Every part scores tasks by **reading** them (the
runtime behavioral gate is separate; see above). All parts emit the same findings
schema (`../../shared/common.py`) and aggregate into one SSOT via `../../shared/aggregate.py`.

| Part | What it checks | How | Entry point |
|---|---|---|---|
| **1 · Static** | required files, metadata, Dockerfile reproducibility, instruction heuristics, leakage/anti-cheat, reward-hack, env-fairness, portability, verifier-defense — **9 deterministic gates** (below) | python scripts | `run_static_qc.py` |
| **2 · Semantic review** | instruction↔test alignment, brittle/phantom/weak tests, over-spec, golden-patch, realism | reviewer sub-agent, one per task | dispatch agents |
| **3 · Adversarial** | reward-hack red-team — can the verifier be gamed without doing the work? | adversary sub-agent, one per task | dispatch agents |
| **4 · Dataset** | decontamination vs public benchmarks, near-duplicates, diversity, difficulty | python scripts | `decontaminate.py` |

### Part 1 — the nine static gates (`run_static_qc.py`)

Cheapest first; each emits the shared findings schema and is precision-tuned
against the eval set.

| Gate | Catches |
|---|---|
| `check_structure` | missing/empty required files; no-`FROM`/trivial Dockerfile |
| `check_metadata` | missing/garbage metadata; seconds-as-minutes time smell; over-broad category/tags; resource-cap violations; internet-flag vs instruction contradiction |
| `check_dockerfile` | reproducibility smells: unpinned base image, `apt` without update, unpinned `pip`, `ADD <url>`, `curl\|sh` (all WARN) |
| `check_instructions` | leftover placeholders (TODO/FIXME/lorem); too-short/empty instruction |
| `check_leakage` | `solution/` or `tests/` COPY'd into the image; truth baked to an agent-visible path the verifier reads; reference `solve.sh` that reads the answer instead of producing it; hint files |
| `check_reward_hack` | vacuous/no-assertion/existence-only tests; swallowed assertions; `pytest \|\| true`; unconditional/agent-writable reward; verifier importing the solution; **agent-writable in-image grader the verifier invokes**; skipped/empty-parametrized scored tests; **`set -e`-aborts-before-reward** |
| `check_env_fairness` | leftover generators/setup scripts; git-history exposure; verifier hitting the network |
| `check_portability` | solve/test robustness: backgrounded-daemon-no-redirect, PEP-668 pip, server-not-started, broad `pkill`, systemd/entrypoint assumptions |
| `check_verifier_defenses` | verifier with no anti-cheat defense (mutated-rerun / recompute / source-grep / re-exec). A PASS `verifier-defended` deterministically suppresses adversary cheat-vectors against it; `verifier-undefended` (WARN) flags a literal-only verifier as gameable; **a degenerate in-image integrity guard (`sha256sum -c`/`cmp` vs a baked ref, agent root) no longer counts as a defense** |

## How to run

### 1. Get tasks
```bash
# pull a sample of OTS tasks from RL Studio (needs RLS_KEY in .env)
python scripts/studio_pull.py --n 50 --out tasks_cache
# or point the pipeline at an existing tasks/ folder
```

### 2. Part 1 — static QC (run first)
```bash
python scripts/run_static_qc.py tasks_cache --out-dir qc_out
```
Writes per-gate findings JSON plus the SSOT and distribution reports into `qc_out/`
(see [Outputs](#outputs)).

### 3. Parts 2–3 — semantic QC (LLM judging)
The reviewer and adversary criteria + ready-to-run prompts are in
[`QC_GUIDE.md`](../../QC_GUIDE.md) (Parts 2–3). Each writes one JSON file to `qc_out/`:

- **Reviewer** → `sem_<task>.json` — the 5 semantic checks (instruction↔verifier
  alignment, coverage, hygiene, golden-patch, realism) plus false-positive
  verification of this task's static flags (`verify-refuted` / `verify-confirm`).
- **Adversary** → `adv_<task>.json` — a separate reward-hack red-team. A surviving
  hack is a `semantic-cheat-vector` **WARN candidate** (not a verdict), promoted to
  FAIL only after confirmation.

Two ways to run the judging:

- **Programmatic (default, scalable) — `scripts/judge.py`.** Calls the Anthropic
  Messages API once per task with the committed prompts and writes the finding JSON.
  **Use a Claude API key, not your account:** set `ANTHROPIC_API_KEY` (`sk-ant-...`)
  or `ANT_KEY` in `.env` — the reviewer/adversary calls are billed to that key. It
  defaults to the latest Claude (`claude-opus-4-8`) and reads the static findings so
  the reviewer can refute false positives:
  ```bash
  python scripts/judge.py <tasks> --out-dir qc_out --static-dir qc_out \
      --role reviewer            # add --role both to also run the adversary
  ```
- **Interactive** — dispatch one reviewer + one adversary sub-agent per task from a
  Claude Code session (same prompts), for hands-on review of a few tasks.

### 4. Aggregate everything
```bash
python ../../shared/aggregate.py qc_out --out-dir qc_out
```
Re-run once the semantic (and any externally-supplied behavioral) findings land in
`qc_out/`: refuted false positives are dropped (precision) and new semantic +
cheat-vector findings fold in (recall), so the SSOT and distribution cover every
part.

## Verdict rules

Each finding is **PASS** / **WARN** / **FAIL**. Per area, the verdict is the worst
finding; a task's overall verdict is the worst area. **FAIL** = must fix before
delivery (e.g. missing file, leak the agent can read, untested requirement,
phantom verifier, hardcoded solution); **WARN** = fix but non-blocking; **PASS** =
clean or trivially cosmetic. Full definitions and the per-defect breakdown are in
[`QC_GUIDE.md`](../../QC_GUIDE.md).

Static flags are **candidates, not verdicts** — confirm leak survival (build +
`ls`) and exploitability before treating a static FAIL as real, and down-rank
flags the semantic pass refutes. Drive **recall to 100% first** (catch every real
defect), then improve precision.

## Outputs

- `review-ssot.csv` — one row per task, per-area verdicts + critical issues.
- `review-ssot.md` — per-task findings with locations and fixes.
- `defect-distribution.md` — defect rate + counts by area and by defect class.
  This is the answer to "how many defects / what distribution".

## Dataset-level checks (run once across a delivery)

```bash
python scripts/decontaminate.py tasks_cache --out qc_out/findings_dataset.json
```

- **Decontamination vs public benchmarks** — scores each task instruction against
  the public-benchmark corpus (`data/decontam_corpus.jsonl`, **1,256 instructions**
  spanning the four benchmarks NVIDIA names: Terminal-Bench (244),
  SWE-bench_Verified (500), LiveCodeBench (287), Aider polyglot (225)) by TF-IDF
  cosine; high similarity ⇒ possible contamination / trivially searchable.
  `--method embed` swaps in sentence-embedding cosine (the methodology
  NVIDIA/Reflection ask for; needs `pip install sentence-transformers`). Rebuild
  the corpus with `build_decontam_corpus.py`.
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
> with `build_decontam_corpus.py` (SWE-bench via the HF datasets-server; LCB from a
> local `test.jsonl` LFS blob; Aider from a `git clone` of the polyglot repo).

## Precision / recall loop

A labeled eval set drives this, sourced from both ends so precision and recall are
both measurable (see `eval/README.md`):

- **defective** examples — client/audit-flagged OTS tasks (ground-truthed in
  `eval/run50_gt/`) plus a **blind cold-discovery pass** over fresh Studio tasks —
  drive **recall**;
- **clean** examples — real public TerminalBench, normalized TB v1→TB2 by
  `scripts/import_tb_tasks.py` (`eval/tb_clean_labels.csv`) — drive **precision**;
- **synthetic fixtures** (`eval/fixtures/`) — the deterministic regression floor
  (must stay precision = recall = 1.0).

```bash
bash eval/run_combined_eval.sh        # static-only, OTS + TB, one combined score
bash eval/run_expanded_eval.sh        # static + committed semantic findings, 200-row set
```

**Workflow:**
1. Read the **false negatives first** and tighten detectors / the semantic prompt
   until recall = 100%; then drive precision. Re-run after every detector change —
   the fixtures catch regressions immediately.
2. Most static false negatives are **semantic** defects (brittle/over-constrained
   verifiers, untested requirements, weak verifiers) — undecidable by reading file
   shapes. Add the Parts 2–3 `sem_*` / `adv_*` findings before scoring to measure
   the **combined** static+semantic recall, which is the real target.

**Scored views** (full breakdown in `eval/README.md`):

| Set / stage | Precision | Recall |
|---|---|---|
| Static only — 276 combined (50 OTS + 226 TB) | 0.50 | 0.30 |
| **Static + semantic — 200-row blind expanded (30 defect / 170 clean)** | **1.00** | **1.00\*** |
| + raw adversary (cheat-vectors as FAIL) | ≈0.2 | 1.00 |

\* The headline `1.0/1.0` is **labels-relative**: a clean-label audit of 30 of the
170 "clean" controls found **2 actually defective** (both public-TB tasks that had
only seen static), so honest recall is **≤0.94**. Static-only recall (0.30) is just
the static ceiling; most of its misses are semantic, which is exactly what Parts
2–3 catch. The **reviewer raised both** precision and recall with **0 false
positives** — trust its FAILs directly. The **raw adversary** (Part 3) hit recall
1.0 but flagged 49/50 tasks, so its cheat-vectors are **WARN candidates pending
confirmation** — `check_verifier_defenses` already suppresses ~81% of them, and a
skeptic or behavioral confirm promotes the rest. Report the converged combined
numbers before applying to the full dataset.

## Next layers (deeper QC on the suspects this layer can't decide)

Feed `qc_out/promote.txt` (from `python ../../shared/gate.py qc_out`) into the next
layer so the expensive checks only see still-clean tasks:

- **Layer 2 — trajectory ([`../trajectory-audit`](../trajectory-audit/SKILL.md))** —
  confirms verifier fairness against real eval rollouts once a batch has run.
- **Layer 3 — behavioral ([`../behavioral-qc`](../behavioral-qc/SKILL.md))** — the
  only check that EXECUTES the task (oracle → 1.0, no-op → 0). Opt-in / expensive;
  run it targeted on the promoted suspects, then re-aggregate + re-gate.

Both emit the same schema, so their findings fold into the same `qc_out/` SSOT and a
behavioral `FAIL` overrides this layer's `PASS` (sticky-FAIL gate).

- **Delivery report** — `python scripts/delivery_report.py <tasks> --ssot qc_out/review-ssot.csv`
  emits the difficulty / category / language distributions + diversity flags clients
  expect at handoff.

## References

- **[`QC_GUIDE.md`](../../QC_GUIDE.md)** — the QC rubric: what every check looks for, the
  semantic-review criteria + FP rules, verdict scale, the ready-to-run sub-agent
  prompts, and the stable defect-class titles. This is all the skill needs to run.

> The fuller internal references (client-specific feedback, the standing
> delivery-gate doc, the deep terminal-bench-review rubric, golden example tasks)
> live in a local-only `references/` folder that is intentionally **not** in this
> repo. `QC_GUIDE.md` distills the non-sensitive operating criteria from them.
