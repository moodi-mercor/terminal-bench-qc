---
name: trajectory-audit
description: >-
  Audit a Terminal-Bench task by inspecting REAL eval rollouts, not just reading
  its files. Use when asked to analyze trajectories, audit an eval/batch run,
  investigate why a task's pass-rate is low, or confirm whether a verifier is
  fair after models have actually run the task. Pulls a completed Studio
  trajectory batch (every attempt already has its score), triages deterministically
  for split-score tasks, all-fail tasks, and any single test that fails across
  almost every attempt and model, measures empirical difficulty (avg@8 from the
  scores — flags tasks the frontier model solves too often and recorded-vs-actual
  avg_at_8 mismatches), then fans out one judge sub-agent per candidate
  to confirm false-negatives (brittle verifier fails correct work), false-positives
  (weak verifier passes cheats), leaked answers, and runtime/setup bugs. This is the
  POST-RUN stage and needs a Studio batch_id + RLS_KEY; the pre-run static/semantic
  pass is the separate terminal-bench-qc skill. Emits the same finding schema so
  results aggregate into one report.
---

# Trajectory Audit — QC a task from its real rollouts

A static read of a task tells you what *should* happen. This skill tells you what
*did* happen when models actually ran it — which is the only way to catch the
defects a read can't see:

- **verifiers that are too strict** — fail a genuinely correct solution (false negative)
- **weak verifiers** — pass work that didn't do the job, or cheated (false positive)
- **cheating / leaked answers** — the agent read the answer instead of producing it
- **runtime / setup bugs** — the task only breaks when actually executed

> **Stage, not a replacement.** This is **Layer 2**. It runs *after* an eval batch
> exists. The pre-run, offline checks (structure, leakage, brittle-verifier
> *prediction*, reward-hack) are **Layer 1** — the sibling
> [`static-semantic-qc`](../static-semantic-qc/SKILL.md) skill; *running* the task is
> **Layer 3** ([`behavioral-qc`](../behavioral-qc/SKILL.md)). This skill *confirms*
> Layer-1 predictions against real solutions and finds what only shows up at runtime.
> All layers emit the **same finding schema**, so `../../shared/aggregate.py` folds
> them into one per-task verdict and `../../shared/gate.py` quarantines any FAIL.

## What a trajectory batch is

A Studio **trajectory batch** (the thing behind a
`studio.mercor.com/admin/batch/<batch_id>` URL) is a completed eval run: every
task attempted N times (e.g. pass@4) by one or more models. Each attempt already
carries the **score the verifier gave it**, a **per-test pass/fail map**, and the
agent's **code diff**. We read what exists — we never re-run the task.

```
Batch ── Trajectory (one attempt) ── final_score (0/1)
                                   ── test_statuses {check: pass/fail}
                                   ── solution (the diff the agent produced)
```

## The pipeline

| Stage | What | Agent? | Entry point |
|---|---|---|---|
| **1 · Pull** | page the batch → one row per attempt (task, model, score); optional per-test + diff detail for a narrowed task set | no | `pull_batch.py` |
| **2 · Triage** | deterministic: split-score tasks, all-fail tasks, any test that fails across almost every attempt+model → ranked candidates | no | `triage.py` |
| **2b · Difficulty** | deterministic: empirical **avg@8** per (task, model) from the scores → flag tasks the frontier model solves > 50% (too easy), and recorded-vs-actual `avg_at_8` mismatches | no | `difficulty.py` |
| **2c · Diff signals** | deterministic, per-attempt: **noop-pass** (passed on an empty diff), **verifier-tampering** (diff writes the test/grader), **score-test-mismatch** (score disagrees with the per-test map) | no | `diff_signals.py` |
| **3 · Judge** | one sub-agent per candidate reads the diff (not the giant transcript) → confirm false-neg / false-pos / cheat / runtime bug | yes, candidates only | dispatch sub-agents |
| **3b · Confirm** | for every Stage-3 **FAIL**, one adversarial sub-agent tries to *refute* it; the FAIL stands only if it survives | yes, FAILs only | dispatch sub-agents |
| **4 · Aggregate** | fold findings into the shared SSOT + gate | no | `../../shared/aggregate.py` + `../../shared/gate.py` |

**Cheapest-first, same as the static skill.** Stages 1–2 are pure data and do all
the narrowing; the model only runs in Stage 3, only on the handful of candidates,
and reads the small diff — not the 600k-token transcript.

## How to run

```bash
cp .env.example .env       # set RLS_KEY=...   (.env is gitignored)

# 1. pull the batch (summary: task, model, score for every attempt)
python scripts/pull_batch.py batch_c5e617e48b0f41eaa13337976014e396 --out audit_out/attempts.jsonl

# 2. triage deterministically -> ranked candidates + findings
python scripts/triage.py audit_out/attempts.jsonl --out-dir audit_out

# 2b. empirical difficulty (avg@8 from the scores) -> flag too-easy tasks + metadata
#     mismatches. REFLECTION-DELIVERY OPT-IN (the avg@8 bar is Reflection-specific).
#     Pass --tasks-dir to compare against each task.toml's recorded avg_at_8.
python scripts/difficulty.py audit_out/attempts.jsonl --out-dir audit_out [--tasks-dir <task-trees>]

# 3. enrich the candidates with per-test detail + diffs, then re-triage
python scripts/pull_batch.py batch_c5e617... --out audit_out/detail.jsonl \
    --with-tests --tasks <comma-separated candidate task names>
python scripts/triage.py audit_out/detail.jsonl --out-dir audit_out

# 3c. per-attempt diff checks over the SAME detail file (no-op pass, verifier
#     tampering, score/test mismatch). Cheapest false-positive + harness-bug catches.
python scripts/diff_signals.py audit_out/detail.jsonl --out-dir audit_out

# 4. dispatch one judge sub-agent per candidate (prompt below); each writes
#    qc_out/traj_<task>.json. Then for every FAIL, dispatch the confirmer
#    (Stage 3b) before aggregating with the static findings.
```

**Use the failure pattern to point you.** If one test fails across almost every
attempt and model (`verifier-suspect-test` in `triage.md`), go read that test
first — it's the strongest signal a verifier is too strict or env-dependent.

## Outputs (`audit_out/`)

- `attempts.jsonl` / `detail.jsonl` — one attempt per line (score, and per-test + diff with `--with-tests`).
- `triage.md` — ranked candidates: split-score, all-fail, and high-fail tests.
- `findings_trajectory.json` — candidate findings in the shared schema (WARN; confirmed → FAIL by Stage 3).
- `findings_diff_signals.json` — per-attempt `noop-pass` / `verifier-tampering` / `score-test-mismatch` candidates (WARN; judge confirms).
- `difficulty.md` / `findings_difficulty.json` — empirical avg@N per task; `difficulty-too-easy`
  (FAIL when measured on an approved model — Opus 4.8 / GPT-5.4 — with ≥8 attempts and rate
  > 0.5; WARN otherwise) and `avg-at-8-mismatch` (recorded `avg_at_8` disagrees with the
  rollouts). These are `area="metadata"`, `layer="trajectory"`, so they fold into the same SSOT.

## Verdict rules

Triage findings are **candidates (WARN), never verdicts** — the same discipline
the rest of the QC stack uses. A split or a high-fail test points you at a place
to look; the judge sub-agent confirms whether it's a real defect before it
becomes a FAIL. A confirmed false-negative or false-positive is a `FAIL` against
`area="tests"`.

## Ready-to-run judge sub-agent prompt (Stage 3)

Dispatch one per candidate task. It reads the **diffs** triage flagged, plus the
task's `instruction.md` and `tests/test_outputs.py`, and decides — **blind to the
score the verifier gave** — whether each attempt actually solved the task.

> You are auditing one Terminal-Bench task from its real eval rollouts. You are
> given: the task `instruction.md`, the verifier `tests/test_outputs.py`, and a
> set of attempt **diffs** with the score each one received. Your job is to find
> where the **verifier's score disagrees with reality**.
>
> For each attempt, decide **on the merits — ignore the score you were given**:
> did this diff actually satisfy the instruction? Did it cheat? Check the full
> reward-hack taxonomy, not just the obvious cases:
> - **hardcode / special-case** the exact value(s) or input(s) the verifier probes
>   (passes the checked cases, wrong in general)
> - **write the inspected artifact directly** instead of producing it (e.g. echo the
>   expected output file, commit a precomputed result)
> - **tamper with the verifier**: edit/delete the test file, `conftest.py`, the
>   grader, or `run-tests.sh`; monkeypatch/`conftest` injection; weaken an assertion
> - **mock / stub the function under test** to return the expected value
> - **swallow failures**: wrap the assertion in `try/except: pass`, `|| true`,
>   `pytest.skip`, or exit-0 the test runner
> - **edit a forbidden file** the spec says not to touch, or **echo a leaked answer**
>   present in the environment
>
> Then compare your judgment to the recorded score:
> - diff is **correct** but scored **0** → `verifier-false-negative` (FAIL): the
>   verifier is too strict. Name the exact check and *why* a correct solution trips
>   it (e.g. greps for an exact literal, exact-string output match, env assumption).
> - diff **cheated or is wrong** but scored **1** → `verifier-false-positive` (FAIL):
>   the verifier is weak/gameable. Name the check it slipped past.
> - score matches reality → no finding (a fair pass or a fair fail).
>
> Also read any `verifier-suspect-test` triage flagged: confirm whether that check
> rejects *correct* solutions (brittle) or the task is simply hard (fair).
>
> Emit ONLY a JSON array of findings to `qc_out/traj_<task>.json`, each (set
> `"layer":"trajectory"` so the gate attributes the catch to Layer 2):
> `{"task","area":"tests","severity":"FAIL","title":"verifier-false-negative|verifier-false-positive|verifier-leak|runtime-bug","location":"tests/test_outputs.py::<check> or trajectory id","detail":"which attempt(s), what the diff did, which check disagreed and why","fix":"make the check outcome-based / close the leak","layer":"trajectory"}`.
> If every score matched reality, emit one `{"task","area":"tests","severity":"PASS","title":"trajectory-audit-ok","detail":"scores matched merits across N attempts","layer":"trajectory"}`.

## Adversarial confirmer (Stage 3b)

A single LLM judge is itself an imperfect verifier — it can latch onto plausible
-but-wrong reasoning. So **every Stage-3 `FAIL` gets a second, adversarial pass**
before it reaches the gate. Dispatch one confirmer per FAIL finding; it sees the
same diff + check + the judge's claim, and is told to **break the finding**:

> You are red-teaming a QC finding, not re-judging the task. You are given a
> Terminal-Bench task's `instruction.md`, the relevant verifier check, an attempt
> **diff**, and a claim that this is a `verifier-false-negative` /
> `verifier-false-positive` / `verifier-leak` / `runtime-bug`. Your job is to
> **refute** it. Argue the most charitable case for the *opposite*: the diff really
> is wrong (so a 0 was fair), or really is correct (so a 1 was fair), or the cheat
> wouldn't actually pass the check, or the "bug" is the task working as intended.
> Default to **refuted=true when uncertain** — we only keep findings that survive a
> genuine attempt to kill them.
> Emit ONE JSON object to `qc_out/confirm_<task>_<n>.json`:
> `{"task","title":"<the finding being tested>","refuted":true|false,"reason":"the strongest counter-argument, or why it could not be refuted"}`.

Keep the FAIL only if `refuted == false`. A refuted finding is downgraded to WARN
(kept in the report as a lead, not a gate-blocking verdict). This mirrors the
contrastive / evaluator-stress-test literature: confirmations that survive an
adversarial pass are the ones worth quarantining a task over.

## Notes

- **Auth & scope.** `RLS_KEY` (in `.env`) + the `[OTS] Terminal Bench` campaign/
  company headers, overridable via `STUDIO_CAMPAIGN` / `STUDIO_COMPANY` env vars.
- **Not every disagreement is a bug.** A split can just be capability (one model
  solved it, another got stuck). The judge distinguishes a *fair* fail from a
  *brittle* one — that's the whole point of Stage 3.
- **Reads only.** This skill never runs a task or mutates Studio; it GETs the
  batch. Confirming a runtime bug definitively is the behavioral gate's job.
