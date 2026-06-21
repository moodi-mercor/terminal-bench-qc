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

A failed Docker build is itself a defect (`build-fails`). A clean run emits
`behavioral-ok`.

## How to run

This gate is **opt-in and confirm-to-run**: by default it runs **nothing** — it
prints the Docker plan. Add **`--execute`** to actually build and run.

```bash
# plan only (safe — runs nothing, just shows the docker commands):
python scripts/check_behavioral.py <tasks-dir> --only task-a,task-b

# actually run (opt-in, expensive; needs Docker/colima running):
python scripts/check_behavioral.py <tasks-dir> --only task-a,task-b --execute [--reward-iso]
```

- `--only` — comma-separated task names (target the flagged suspects).
- `--verifier-cmd` — how to invoke the verifier inside the container (default
  `bash /tests/test.sh`); match it to your harness if needed.
- `--out` — findings JSON path (default `findings_behavioral.json`); drop it into the
  shared `qc_out/` so it aggregates with the other layers.

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

- **Needs Docker.** Start colima / Docker first; without `--execute` it never touches
  Docker. See the local-run notes in the workspace memory for the colima
  BuildKit / `--platform` gotchas.
- **Single-container approximation.** Real harbor uses a *separate* verifier
  container; this runs the verifier in the same image with `tests/` + `solution/`
  mounted read-only. Enough for no-op/oracle, not a full harbor replica.
- **Reads + runs only this task.** It builds the task's own image and removes it
  after; it does not call any external service.
