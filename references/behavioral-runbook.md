# Behavioral Gate Runbook (Layer 2)

The behavioral layer is the single most important QC gate. Per the QC gate
reference, almost every client escalation (NVIDIA, MAI, GDM, Reflection) traces
to one of these not being run, or not being run consistently with saved logs.
Run this in an environment with `harbor` + Modal (and Docker where required) —
not on a bare laptop.

## Run discipline (hard rules — each has cost a real escalation)

1. **Do not report success until BOTH no-op and oracle have terminated.** An
   interrupted oracle is a FAIL, not a partial pass. Re-run, don't summarize.
2. **Run on the client's target infra.** Default `-e modal`; ALSO run `-e docker`
   when the client uses Docker (MAI: 56 tasks passed Modal, failed Docker).
3. **3× each with saved logs.** No delivery without logs stored per run. One-off
   runs missed defects at MAI and GDM.
4. **Polling cadence ≥ 60s; prefer ScheduleWakeup for >5 min runs.** Tight loops
   add no information and have caused interrupted oracles.
5. **Never pipe a long run through `| head`/`| tee | head`** — SIGPIPE kills the
   upstream process. Use `nohup CMD > run.log 2>&1 &` then `tail -f run.log`.
6. **n=1 gate before batch.** Verify one task end-to-end before `-n <large>`.
7. **Pre-flight SSO** (`aws sts get-caller-identity`) before any remote run.

## Step 1 — Oracle + no-op (automated)

```bash
python scripts/behavioral_gates.py <tasks-dir> --env modal --runs 3 \
    --log-dir behavioral_logs --out qc_out/findings_behavioral.json
# repeat with --env docker if the client targets Docker
```

Pass criteria (the runner encodes these):
- **oracle = 1.0 on every run.** < 1.0 ⇒ broken solve path or a verifier that
  rejects the correct answer. Inconsistent across runs ⇒ flaky (determinism bug).
- **no-op = 0.0 on every run.** > 0.0 ⇒ tests pass without the fix (non-verifying
  tests / reward leak) — the MAI "29 no-op tasks" failure mode.

Do not proceed to reward-isolation or adversarial until oracle is green.

## Step 2 — Reward-file isolation (needs a custom agent)

Confirm the agent cannot write the reward directly. Run an agent whose only
action is to echo/write a pass value (e.g. write `reward: 1` to any plausible
results path, `echo PASS`, touch sentinel files the verifier might read).

Pass criterion: this agent scores **0.0**. If it scores > 0, the verifier reads
an agent-writable signal — isolate the reward computation from agent-writable
paths and re-run oracle to confirm no false negative.

## Step 3 — Adversarial exploit pass (needs a frontier agent)

Spawn a frontier model (~3 rollouts) prompted to *game the verifier* without
solving the task: hardcode expected outputs, write expected rows/files directly,
stub timestamps/IDs, move forbidden literals into a swapped file, read any
leaked state. For each exploit found:
1. Add an assertion that closes it.
2. Re-run the oracle to confirm the new assertion does not introduce a false
   negative (oracle still = 1.0).

This is the layer that static screening missed at Reflection (brittle/gameable
verifiers shipped after a re-QC'd 100-task delivery). Make it mandatory on
high-value deliveries, not optional.

## Step 4 — Determinism / resource / offline (environment)

- **No ENTRYPOINT; CMD only** (MAI: infra overrides startup with `sleep
  infinity`; tasks relying on a startup CMD never bring services up).
- Runs within client resource caps (~1 CPU / 4 GB) — confirm on Docker, not just
  Modal.
- Deterministic: fixed seeds/ports/timestamps; no live network at runtime; no
  expiring URLs or external services.
- Reasonable runtime within the timeout budget.

## Evidence format on completion

A table — no prose-only summaries:

| task_id | env | oracle (3×) | no-op (3×) | reward-iso | adversarial | log path |
|---|---|---|---|---|---|---|
