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

## Expanded 200-task eval (first/second 5k + OTS + cold-discovery + clean controls)

`expanded_labels.csv` is the recall/precision target from the action item
"grab ~50 tasks and iterate until recall is 100%," now broadened to **200 rows**
(30 defects, 170 clean controls). It mixes:

- synthetic fixtures for deterministic regression coverage;
- current manually audited OTS examples from `run50_labels.csv`;
- extra OTS Terminal-Bench samples pulled directly from Studio, including both
  confirmed defects and remediated/known-pass controls;
- **60 cold-discovery OTS tasks** sampled blind from Studio and audited fresh (see
  the cold-discovery section below);
- public Terminal-Bench clean controls for precision;
- high-confidence defect IDs from the first/second 5k validation markdowns
  (`server-defined-not-started`, `cmd-entrypoint-reliance`,
  `reference-solve-reads-truth`, and `broad-pkill`).

The broader `expanded_candidate_cases.csv` keeps interesting report cases that are
not yet in the scored labels because they need current-snapshot verification, are
delivery-behavioral only, or are likely client-sandbox infra rather than task QC
defects. Promote from the candidate catalog into `expanded_labels.csv` only after
reading the current task tree and confirming the defect still exists.

### Ground-truth correction (2026-06-18) — current-snapshot audit

When first scored, the 13 first/second-5k report-pattern rows were all labeled
`is_defect=1` from the *report*, not the *current tree*. Auditing each against the
live Studio snapshot showed **12 are already REMEDIATED** (server-defined-not-started
→ solve.sh now launches + waits; cmd-entrypoint-reliance → solve.sh starts the daemon,
no Docker-CMD reliance; reference-solve-reads-truth → solve recomputes the HMAC/digest
instead of reading `tests/`; broad-pkill → scoped `pkill -f <task-proc>`). Those 12
were relabeled `is_defect=0` (clean precision controls) with per-task line-level
evidence in their `notes`. This is the README's own "confirm the defect still exists"
rule applied — stale report labels were the cause of most static "false negatives".

The original 7 real defects left were the manually-audited run50 OTS verifier defects
(brittle-false-reject, brittle-overconstrained, instruction-environment/test
misalignment, untested-requirement, weak-verifier, reward-hacking). These are
undecidable by file shape, so they are caught by the **Layer-2 semantic reviewer**;
the confirmed findings are committed as `eval/expanded_sem_findings/sem_*.json` and
folded into the score by `run_expanded_eval.sh`.

The 100→200 expansion keeps the prior 24 defects and grows **both ends** from their
real-world sources (per the data strategy: defects ⇐ client/audit flags, clean ⇐ real
TB), via a **blind cold-discovery pass** that answers the previous caveat below.

### Cold-discovery expansion (2026-06-18) — the blind rigor step

The local defect pool was exhausted (all report candidates already promoted-or-rejected),
so 200 was reached by genuinely *discovering* new defects rather than re-confirming known
ones:

- **60 fresh Studio tasks** were sampled by hashing task names across the 13,430-task world
  (deterministic, none previously seen — see `expanded_v200_ots_tasks.txt`), pulled to
  `tasks_cache_v200`, and reviewed **blind**: static QC ran first, then one Layer-2 reviewer
  per task hunted semantic defects *without being told which tasks were defective*.
- The pass confirmed **6 new defects** (~10% yield) — committed as
  `eval/expanded_sem_findings_v200/sem_*.json`:
  `coldchain-shadow-generator` (verifier never runs the agent's script — a correct agent
  scores 0), `slo-budget-packet-reconciler` (answer key baked into the agent image at
  `/tests/.truth/`), `port-scheduler-slo-envelope-builder` (verifier helper baked agent-
  writable into `/tests/`, also confirmed by static), `scanner-sync-daemon` (an
  undiscoverable required tie-break rule decides ~35% of outputs vs exact-equality tests),
  `contain-trojan-lateral` (brittle exact-substring `10.55.0.0/16 drop` false-rejects
  idiomatic nftables), and `factory-calibration-verifier` (declared `hard` with time
  estimates in the `medium` band).
- **Precision under cold conditions held:** static raised **5 leakage FAILs** on the fresh
  batch; the Layer-2 review **confirmed 1** (port-scheduler, a true positive) and **refuted
  4** as input/verify-time-only data wrongly read as baked truth. The 4 refutations are
  committed as `verify-refuted` metas (`expanded_sem_findings_v200/verify_refuted.json`)
  that `aggregate.py` drops before scoring — so those 4 do **not** register as false
  positives. This is the FP-verification layer doing exactly its job on unseen tasks.
- The other 54 fresh tasks are audited-clean OTS controls, plus **40 more public TB clean
  controls** top up precision.

The label set now contains **30 defects and 170 clean controls**: 9 fixtures, 68 prior OTS
rows, 60 cold-discovery OTS rows (6 defect / 54 clean), 50 public TB controls, and 13
first/second-5k report rows.

**Scored result (static + semantic, 200 rows): `TP=30 FP=0 FN=0 TN=170` →
precision = recall = 1.0.** Static catches the shape defects; the Layer-2 reviewer catches
the semantic ones *and* clears the static leakage false positives. Unlike the 100-row run,
the 6 new defects come from a **blind** pass (reviewers were not told which tasks were
defective), so this is no longer purely confirmatory. The realized defect ratio (15%)
reflects the true ~10% cold-discovery yield — defects were **not** padded to hit a target.

> ⚠️ **Read this `1.0/1.0` with the clean-label audit below.** Recall=1.0 only means
> *no labeled defect was missed* — and for the semantic rows the label and the prediction
> come from the same reviewer (confirmatory, not independent). The clean-label audit
> (next section) adversarially re-checked 30 of the 170 "clean" controls and found **2
> were actually defective**, both in the public-TB pool. Counting those, the honest recall
> is **≤0.94 and probably lower** — see below. Precision is unaffected (no new false flags).

```bash
# Pull the real OTS/report tasks when available in Studio.
python scripts/studio_pull.py --names @eval/expanded_ots_tasks.txt --out tasks_cache_expanded
# Pull the cold-discovery batch.
python scripts/studio_pull.py --names @eval/expanded_v200_ots_tasks.txt --out tasks_cache_v200

# Ensure the public TB precision controls exist.
python scripts/import_tb_tasks.py

# Score every present piece against the expanded labels.
bash eval/run_expanded_eval.sh
```

### Clean-label audit (2026-06-18) — the "clean" controls are not all clean

A `precision = recall = 1.0` should be distrusted, so we stress-tested the *clean* side
directly: **randomly sampled 30 of the 170 `is_defect=0` controls and adversarially
audited each** — one sub-agent per task, instructed to *prove the task is broken* (not to
confirm it clean), executing the solution + verifier end-to-end where feasible.

**Result: 2 of the 30 "clean" controls were actually defective** — a **6.7%
false-negative rate in the clean labels (95% Wilson CI 1.8%–21.3%)**, and **both were
public-TerminalBench tasks that only ever saw Part 1 (static)**:

- **`model-extraction-relu-logits` — weak verifier (false-accept).** The grader matches
  rows by the ratio `stolen_row / original_row` being ~constant. An all-zeros submission
  gives `0/x = 0` everywhere → "matches" all 30 rows, so a **no-op that never queries the
  model scores 100%**. Verified directly (`tests/test_outputs.py:55-75`).
- **`attention-mil` — broken oracle (false-reject).** The shipped `solution/solve.sh`
  fails the task's *own* tests: it patches the assignment via an exact `str.replace` whose
  pattern assumes a blank line of 8 spaces, but the file's blank line is empty → the edit
  silently no-ops → 9/11 tests crash. A correct solution exists (fixing the blank line →
  11/11), so the *delivered reference* is wrong. (Possible TB-v1→TB2 import artifact —
  worth a single rebuild to confirm it's upstream.)

These are the two opposite failure modes: **#1 lets a wrong answer pass; #2 makes a right
answer fail.**

**Why the pipeline missed them — a real blind spot, not noise:**
the public-TB tasks were imported as a *precision baseline* and **assumed clean**, so they
only ever ran the deterministic Part-1 static gates — **Parts 2–3 (semantic reviewer +
adversary) were never run on them, and behavioral is off by default.** Part 1 reads file
*shapes*; it cannot tell that a no-op passes a grader (Part 2/3 + behavioral territory) or
that the oracle fails (behavioral only). The audit also showed the rot is *localized*:
**public-TB audited 2/10 broken (~20%); non-public-TB clean (OTS / run50 / cold-discovery)
audited 20/0 — those labels held.** Extrapolating ~20% over the 68 public-TB controls
suggests **~10–14 of them are likely defective**.

**Honest standing:** the artifacts are sound (defects verified, refutation logic verified),
but the headline `1.0/1.0` rests on labels whose clean side has a measured error rate. The
truthful recall, counting the 2 found defects the pipeline did not flag, is
**30/32 = 0.938**, and likely lower once the rest of the public-TB pool is reviewed.

**Action items this surfaced (not yet applied):**
1. **Relabel the 2 confirmed defects `is_defect=1`** (recall becomes the honest 0.938).
2. **Run Part 2 (semantic reviewer) over the remaining ~66 public-TB controls** before
   scoring precision against them — a control only earns the `is_defect=0` label by
   surviving the same review the defects got, not by reputation.
3. **Wire the behavioral gate (oracle-must-pass / no-op-must-fail) into the eval** as the
   ground-truth catch for the weak-verifier and broken-oracle classes that reading misses.

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
