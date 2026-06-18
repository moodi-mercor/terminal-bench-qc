# Golden eval set

The labeled set used to measure QC **precision / recall** (action item 2). Precision
needs *clean* tasks (so over-flagging shows up) and recall needs *defective* tasks
(so misses show up), so the set is sourced from both ends:

> **The two real-world ends, per the data strategy:** *defective* examples come from
> tasks **flagged by clients / our own audits** (they must be caught → drive recall);
> *clean* examples come from **real public TerminalBench tasks** (presumed good →
> drive precision / measure the false-positive rate). `score_qc.py` uses the detector
> convention — positive = *defect* — so client/audit-flagged ⇒ `is_defect=1`, real TB
> ⇒ `is_defect=0`. Same buckets, labeled from the detector's point of view.

Three labeled sources:

1. **Synthetic fixtures** (`fixtures/` + `golden_labels.csv`, committed) — tiny TB2
   tasks, deterministic, no network. A clean **pass** task and one **fail** task per
   FAIL-class defect, each mapping to a real client-report pattern. The pass fixtures
   include deliberate **near-misses** (truth at a verify-time-only path, a server
   launched correctly) so they guard **precision**, not just recall. Always reproduce
   regardless of Studio/network state — the regression floor.
2. **Real OTS tasks** (`run50_labels.csv`, 50 tasks) — actual Studio tasks with
   per-task ground truth consolidated from the evidence-backed audits in `run50_gt/`
   (`audit_A..J.json`): **10 defective (`is_defect=1`)**, 40 clean/minor. The task
   trees are pulled with `studio_pull.py` (needs `RLS_KEY`); see `ots_tasks.txt` for
   the originally-curated subset. This is the **recall** set (real defects to catch).
3. **Real public TerminalBench tasks** (`tb_clean_labels.csv`, 226 tasks) — the
   `original-tasks/` corpus from github.com/laude-institute/terminal-bench, normalized
   from TB v1 → TB2 by `scripts/import_tb_tasks.py` and labeled `is_defect=0`. This is
   the **precision / clean baseline**: every FAIL here is a false positive to explain
   (or a genuine latent defect in public TB). See "Why normalize, not dual-format" below.

All ground truth uses the columns
`task,is_defect,expected_verdict,expected_title,kind,source,notes`.

## Run against the fixtures (offline, deterministic)

```bash
python scripts/run_static_qc.py eval/fixtures --out-dir /tmp/fx_qc
python scripts/score_qc.py /tmp/fx_qc/review-ssot.csv eval/golden_labels.csv
```
Expected: the 6 `fail/*` fixtures score FAIL, the 3 `pass/*` fixtures score PASS →
precision = recall = 1.0 on the overlap.

## Run against the real OTS examples (needs the key)

```bash
cp .env.example .env          # then put your RL Studio key in it: RLS_KEY=...
python scripts/studio_pull.py --names @eval/ots_tasks.txt --out tasks_cache
python scripts/run_static_qc.py tasks_cache --out-dir qc_out
python scripts/score_qc.py qc_out/review-ssot.csv eval/golden_labels.csv
```
`score_qc.py` only scores tasks present in both the QC output and the labels, so it
ignores tasks you didn't pull.

## Expanded 50-task eval (first/second 5k + OTS + clean controls)

`expanded_labels.csv` is the next recall/precision target from the action item
"grab ~50 tasks and iterate until recall is 100%." It mixes:

- synthetic fixtures for deterministic regression coverage;
- current manually audited OTS examples from `run50_labels.csv`;
- public Terminal-Bench clean controls for precision;
- high-confidence defect IDs from the first/second 5k validation markdowns
  (`server-defined-not-started`, `cmd-entrypoint-reliance`,
  `reference-solve-reads-truth`, and `broad-pkill`).

The broader `expanded_candidate_cases.csv` keeps interesting report cases that are
not yet in the scored labels because they need current-snapshot verification, are
delivery-behavioral only, or are likely client-sandbox infra rather than task QC
defects. Promote from the candidate catalog into `expanded_labels.csv` only after
reading the current task tree and confirming the defect still exists.

```bash
# Pull the real OTS/report tasks when available in Studio.
python scripts/studio_pull.py --names @eval/expanded_ots_tasks.txt --out tasks_cache_expanded

# Ensure the public TB precision controls exist.
python scripts/import_tb_tasks.py

# Score every present piece against the expanded labels.
bash eval/run_expanded_eval.sh
```

## Expanded combined run (precision + recall)

```bash
# one-time: clone the public TB corpus and normalize it (TB v1 -> TB2)
git clone --depth 1 --filter=blob:none --sparse \
  https://github.com/laude-institute/terminal-bench.git references/tb-public-src
(cd references/tb-public-src && git sparse-checkout disable)   # tasks live in original-tasks/
python scripts/import_tb_tasks.py            # -> tasks_cache_tb/ + eval/tb_clean_labels.csv

# then (with tasks_cache pulled too) score OTS + TB together
bash eval/run_combined_eval.sh
```

`run_combined_eval.sh` runs the static gates over each set, concatenates the SSOTs and
labels, and scores once. Precision is only meaningful with both ends present; the TB
clean set alone reports the false-positive *rate*.

### Current static-only numbers (276 scored: 50 OTS + 226 TB)

```
TP=3  FP=3  FN=7  TN=263   precision = 0.50   recall = 0.30
```

Read these as the **static layer's** ceiling, not the skill's:

- **The 7 false negatives are (mostly) semantic defects** — brittle/over-constrained
  verifiers, untested requirements, weak verifiers, instruction ambiguity. These are
  undecidable by reading file *shapes*; they are what the **Layer 2 semantic +
  adversarial sub-agents** exist to catch. Static recall on this set is expected to be
  low; the combined static+semantic recall is the real target and is measured by adding
  the `sem_*`/`adv_*` findings before scoring.
- **The 3 false positives are genuine borderline calls on real TB tasks**, not detector
  noise: `break-filter-js-from-html` actually `COPY`s its verifier into the agent image
  (a real latent leak — arguably a true positive the `is_defect=0` label undersells);
  `git-workflow-hack` seeds the `deploy.yml` the agent must edit; `jq-data-processing`
  bakes an integrity-hash the verifier reads. These are exactly the candidates the
  Layer 2 reviewer's **false-positive verification** is meant to adjudicate.

The tuning that moved this from precision 0.29 / recall 0.20 → **0.50 / 0.30** (without
regressing the fixtures, which stay 1.0) lives in `check_leakage.py` (the
`reference-solve-reads-truth` read-vs-produce fix, broadened build-script scanning, and
the instruction-referenced / input-dir FP controls) and `check_structure.py` (indented
`FROM`). Re-run `eval/run_combined_eval.sh` after any detector change to catch
regressions.

### Why normalize, not dual-format

Public TB tasks are TB v1 (`task.yaml`, root `Dockerfile`, `run-tests.sh`,
`solution.sh`). Rather than teach every detector two layouts, `import_tb_tasks.py`
produces a faithful TB2 *view*: it preserves the Dockerfile / test / solution **bytes**
(so content checks — leakage, reward-hack, brittleness, portability — stay honest) and
**synthesizes** a clean `task.toml` from `task.yaml` (TB v1 has a genuinely different
metadata schema; running TB2 metadata checks on it directly would manufacture dozens of
phantom "defects" and corrupt the precision signal). It also rewrites TB v1's
`COPY tests/<input>` build inputs into the `environment/` context — except copies of the
*verifier itself*, which stay flagged because baking the verifier into the agent image
is a real leak.

## Fixture → report-pattern map

| Fixture | Defect | Report pattern |
|---|---|---|
| `fail/truth-baked` | answer baked to an agent-readable path the verifier reads | first/second-5k "truth at agent-readable paths" (290 + 49) |
| `fail/reference-reads-truth` | `solve.sh` reads the verifier's expected file | second-5k Pattern 4 (14 tasks) |
| `fail/unconditional-reward` | reward written without a success check; verifier `\|\| true` | MAI no-op / vacuous tests |
| `fail/copies-solution` | Dockerfile copies `solution/` into the image | anti-cheat / leakage |
| `fail/missing-solution` | required file absent | structure |
| `fail/bad-metadata` | missing timeout, seconds-as-minutes, generic tags | metadata |
| `pass/clean-records-etl` | none | the shape a clean task should have |
| `pass/hidden-truth-verifier-only` | none (near-miss) | truth in source `tests/.truth/` (verify-time mount, not baked) — precision counterpart to `truth-baked`/`reference-reads-truth`; the *remediated* shape (truth moved to `tests/.truth/` / `/tmp`) |
| `pass/webservice-healthcheck` | none (near-miss) | server launched right (`nohup … >log 2>&1 &` + wait-for-port) — precision counterpart to `backgrounded-daemon-no-redirect`/`server-defined-not-started` |

> Behavioral defects (oracle≠1, OOM, timeout) aren't in this static eval — they're
> confirmed by the delivery-stage run, not by reading files.
