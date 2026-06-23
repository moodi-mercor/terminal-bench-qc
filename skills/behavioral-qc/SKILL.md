---
name: behavioral-qc
description: >-
  Confirm a Terminal-Bench task is sound by RUNNING it — the only QC layer that
  executes the task instead of reading it. Use when asked to do a behavioral /
  oracle / no-op check, validate that a verifier actually requires the agent's
  work, confirm the reference solution passes its own tests, or run the
  delivery-stage gate before shipping. Builds the task's Docker image and runs the
  verifier three ways: the untouched container must score 0 (no-op), the oracle
  solution must score 1 (solve.sh), and an injected fake reward must still score 0
  (reward isolation). Catches the two defects a static/semantic read structurally
  cannot — a vacuous verifier a no-op passes, and a broken oracle that fails its
  own task. This is Layer 3 (post static+semantic Layer 1 and trajectory Layer 2);
  it is EXPENSIVE and opt-in (needs Docker), so it runs targeted on flagged tasks.
  Emits the same finding schema so results aggregate into one report.
---

# Behavioral QC — confirm a task by running it (Layer 3)

Layers 1 (static + semantic) and 2 (trajectory) tell you what a task *should* do by
reading it and reading its rollouts. This layer is the only one that **executes the
task**, so it is the sure catch for the two defects a read cannot decide:

- **no-op passes** — the verifier scores the *untouched* container ≥ pass. The task
  doesn't actually require the agent's work (a vacuous verifier).
- **oracle fails** — the reference `solution/solve.sh` does **not** pass the task's
  own verifier. The reference is broken (or an env/harbor-conversion defect).

> **Stage, not a replacement.** This runs *after* the cheap layers have flagged the
> suspects. It is expensive (a Docker build + verifier run per task, minutes each),
> so run it **targeted** — on the tasks Layer 1/2 flagged, or a sample — never as a
> reflex over the whole set. The authoritative version is the client's delivery-stage
> gate (harbor + Modal); this is the single-container approximation that catches the
> dominant no-op/oracle defects before handoff.

## The three trials (`scripts/check_behavioral.py`)

Each trial mounts `tests/` + `solution/` at run time (never into the image) — the
same separation harbor enforces.

| Trial | What runs | Expected | Defect if not |
|---|---|---|---|
| **no-op** | build image, run the verifier on the untouched container | **FAIL** (score 0) | `no-op-passes` — verifier is vacuous |
| **oracle** | fresh container, run `solution/solve.sh`, then the verifier | **PASS** (score 1) | `oracle-fails` — reference can't solve its own task |
| **reward-iso** *(`--reward-iso`)* | write a fake passing reward/score file, then the verifier | **FAIL** | `reward-signal-gameable` — pass signal is agent-writable |

A failed Docker build is a defect (`build-fails`) **only on the task's target arch
with enough time**. A build **timeout** (`build-timeout`, often from too many
`--workers`) or a build failure under `--native-arch` (`build-untested-native-arch`,
the task targets amd64) is **WARN-inconclusive, not a defect** — it means "couldn't
test here," confirm on native amd64. A clean run emits `behavioral-ok`.

**Verifier runtime vs budget (Reflection "Fast enough").** Each trial times *only the
verifier step* (not `solve.sh`) and compares it to the task's configured
`verifier.timeout_sec`: `verifier-exceeds-timeout` (**FAIL** — the verifier alone blows
its budget on the oracle-solved container, so it'd be killed and score even a correct
solution 0) or `verifier-near-timeout` (**WARN** — within 20% of the budget, little
headroom). The static pre-run co-signal is `verifier-unbounded-call` (a verifier network
call with no timeout) in Layer-1 `check_portability`.

## How to run

This gate is **opt-in and confirm-to-run**. By default it runs **nothing** — it
prints the Docker plan.

> **Always ask the user before running with `--execute`.** This is the only layer
> that *executes* tasks and it is expensive (a Docker build + trials per task —
> minutes each; a bulk set is hours and uses real CPU/disk). First do the plan-only
> dry run, then **show the user the scope and cost and get explicit confirmation**
> before adding `--execute`:
> - how many tasks, and which (the flagged suspects, or a promoted set?)
> - est. time (≈ build + trials per task ÷ `--workers`) and that it executes code
> - off-amd64? then `--native-arch`; bulk? then `--workers`
>
> Only run `--execute` once the user says go. Never kick off a bulk execute on your
> own initiative.

```bash
# plan only (safe — runs nothing, just shows the docker commands):
python scripts/check_behavioral.py <tasks-dir> --only task-a,task-b

# actually run (opt-in, expensive; needs Docker running):
python scripts/check_behavioral.py <tasks-dir> --only task-a,task-b --execute [--reward-iso]

# bulk run off-amd64 (e.g. an Apple-Silicon laptop), parallel + resilient:
python scripts/check_behavioral.py <tasks-dir> --only "$(paste -sd, qc_out/promote.txt)" \
    --execute --native-arch --workers 4 --build-timeout 600 --timeout 90 \
    --out qc_out/findings_behavioral.json
```

- `--only` — comma-separated task names (target the flagged suspects, or a promoted set).
- `--workers N` — run N tasks concurrently. Builds are CPU-bound, so this scales near-
  linearly; **4 is a safe laptop default** (1 = sequential). Turned a ~8 h × 82-task
  sweep into ~2 h.
- `--native-arch` — strip the `FROM --platform=linux/amd64` pin and build for the host
  arch. **Essential off-amd64**: qemu emulation of an amd64 `apt`/`gcc` build is so slow
  it effectively hangs; native builds take seconds. Results are *arch-indicative* for
  arch-sensitive tasks (gcc/strace), authoritative for pure-Python.
- `--timeout` (per-trial, default 600) / `--build-timeout` (default 600) — keep
  `--timeout` short (e.g. 90) so a **server-style verifier that blocks** (waits on a
  daemon the no-op never starts) is killed fast and recorded as "didn't pass"; the build
  needs its own longer budget for `apt`/`pip`.
- **Resume:** findings are persisted **after each task**, and a re-run **skips tasks
  already in `--out`** — so a laptop sleep / interrupt can't wipe a long run; just re-run.
  Pass `--no-resume` to start clean.
- `--verifier-cmd` — how to invoke the verifier inside the container (default
  `bash /tests/test.sh`); match it to your harness if needed.
- `--out` — findings JSON path (default `findings_behavioral.json`); drop it into the
  shared `qc_out/` so it aggregates with the other layers.
- `--yes` / `-y` — skip the interactive confirmation. When run in a terminal,
  `--execute` first **asks for confirmation** (shows task count + est. time); `--yes`
  bypasses it for automation. (Non-interactive/background runs don't prompt.)

## Verdict rules

Findings use `area="behavioral"` and the standard PASS/WARN/FAIL scale. A behavioral
`FAIL` is the strongest verdict in the stack — it is an observed runtime fact, not a
prediction. Because every layer writes into one cumulative report and a `FAIL` is
**sticky** (see the repo `README.md` "Defect gate"), a behavioral `FAIL` overrides an
earlier Layer-1/2 `PASS`: a task only earns "clean" if it survives the layers you
actually ran on it.

## Outputs

- `findings_behavioral.json` — one finding per trial per task, in the shared schema.
  Aggregate with `python ../../shared/aggregate.py qc_out` and gate with
  `python ../../shared/gate.py qc_out`.

## Notes

- **Needs Docker.** Start Docker (or colima) first; without `--execute` it never touches
  Docker. **Off amd64 (Apple Silicon), pass `--native-arch`** — without it, the
  emulated amd64 `apt`/`gcc` build is so slow the run effectively hangs.
- **Bulk runs are an "overnight"-class job even native + parallel.** Each native build
  is ~3 min and there's no cross-task cache reuse; with `--workers 4`, ~80 tasks ≈ 2 h.
  Use the resume (persist-per-task + skip-already-done) and just re-run after a sleep.
- The authoritative confirmation is the **delivery-stage** run on native amd64 infra
  (harbor + Modal); `--native-arch` results are indicative for arch-sensitive tasks.
- **Single-container approximation.** Real harbor uses a *separate* verifier
  container; this runs the verifier in the same image with `tests/` + `solution/`
  mounted read-only. Enough for no-op/oracle, not a full harbor replica.
- **Reads + runs only this task.** It builds the task's own image and removes it
  after; it does not call any external service.
