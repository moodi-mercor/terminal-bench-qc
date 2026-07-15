# Terminal-Bench QC — the dimension checklist (all layers)

The purpose of this file is stringency: **every task is assessed on every dimension,
with evidence, and no dimension is silently skipped.** It is the single list an LLM
judge (or a human) ticks through, and it names the exact tool + command that answers
each dimension and how the answer is recorded.

The checklist is **machine-enforced**, not honour-system. Each finding is tagged with
its `dimension`; a dimension with no evidence-backed finding fails the task as
`qc-incomplete` and the gate quarantines it (`aggregate.py --require-complete` /
`gate.py --require-complete`). You cannot pass a task by staying silent on a dimension,
and a `PASS` with no `file:line` evidence is rejected the same as a skip.

The canonical definition lives in [`shared/common.py`](shared/common.py) `QC_DIMENSIONS`;
this doc is its operator-facing companion. See [`QC_GUIDE.md`](QC_GUIDE.md) for *how to
judge* each dimension and [`README.md`](README.md) for the sticky-FAIL gate contract.

## The eleven dimensions

| # | Dimension key | What it answers | Layer / tool | Evidence recorded in |
|---|---|---|---|---|
| 1 | `alignment` | every instruction requirement has a test; every test maps to something agent-visible | semantic — `judge.py --role reviewer` | finding `detail` (`file:line`) |
| 2 | `coverage` | both routes tested (right answer **and** required algorithm/perf bound) | semantic — `judge.py --role reviewer` | finding `detail` |
| 3 | `hygiene` | instruction clarity, no leftover placeholders, not over-/under-specified | semantic — `judge.py --role reviewer` | finding `detail` |
| 4 | `golden-patch` | the reference `solve.sh` actually scores 100% on the verifier | semantic — `judge.py --role reviewer` | finding `detail` |
| 5 | `realism` | a real engineer would plausibly be assigned this workflow | semantic — `judge.py --role reviewer` | finding `detail` |
| 6 | `constraints` | agentic, distractor-free, no arbitrary/uncalibrated constraints | semantic — `judge.py --role reviewer` | finding `detail` |
| 7 | `category` | the `task.toml` category/subcategory matches the **dominant work** (not just in-taxonomy) | semantic — `judge.py --role reviewer` | finding `detail` |
| 8 | `cheat-vector` | can the verifier be gamed without doing the work? | adversarial — `judge.py --role adversary` | finding `detail` |
| 9 | `oracle-passes` | the oracle (`solve.sh`) → **reward 1** | behavioral — `modal_gate.py` | `behavioral_signals.json` |
| 10 | `noop-fails` | the untouched container (no-op) → **reward 0** | behavioral — `modal_gate.py` | `behavioral_signals.json` |
| 11 | `verifier-sound` | deliberately-broken solutions (mutants) → **reward 0** (verifier rejects wrong work) | behavioral — `mutation_test.py` | `behavioral_signals.json` (`mutation`) |

Dimensions 1–8 are answered by **reading** the task (the LLM judge). Dimensions 9–11
**cannot be decided by reading** — a broken oracle, a vacuous verifier, and a verifier
that a wrong solution slips past only show up at execution time — so they are answered
by **running** the task on Modal. The completeness gate requires all three: a task with
a perfect reviewer pass but no Modal oracle/no-op result, or no mutation-testing result,
is still `qc-incomplete`. This is what stops the reviewer from *assuming* verifier
soundness without evidence it was tested.

## How to run each layer

### Dimensions 1–8 — the LLM judge (`judge.py`, on an API key)

Run the reviewer (dims 1–7) and the adversary (dim 8) as one Anthropic Messages API
call per task. **Use a Claude API key, not your Claude.ai account** — set
`ANTHROPIC_API_KEY` (`sk-ant-…`) or `ANT_KEY` in `.env`; the calls are billed to it.

```bash
# reviewer (dims 1–7) + adversary (dim 8); reads static findings so it can refute FPs
python skills/static-semantic-qc/scripts/judge.py <tasks> \
    --out-dir qc_out --static-dir qc_out --role both
# -> sem_<task>.json (reviewer, one finding per dimension) + adv_<task>.json (adversary)
```

The reviewer is bound by a **coverage contract** (in the prompt and enforced in
`judge.py`): it must emit exactly one finding per dimension, each with non-empty
`file:line` evidence. If the model skips one or asserts it without evidence, `judge.py`
injects a `dimension-not-assessed` **FAIL** so the gap is visible in the SSOT instead
of passing silently.

> Prefer `judge.py` over judging inside a Claude Code session. A single-shot judge with
> everything inlined and a forced output shape is more stringent and reproducible; an
> in-session agent wanders and skips. Reserve interactive judging for the few tasks the
> judge flags `judge-refused` / `judge-unparsable` or marks uncertain.

### Dimensions 9–11 — the Modal behavioral gates (`modal_gate.py` + `mutation_test.py`)

Run the two oracle/no-op trials on native amd64 in parallel (a 1,000-task corpus gates in
~20–40 min). Full setup + triage: [`skills/behavioral-qc/MODAL_GATE.md`](skills/behavioral-qc/MODAL_GATE.md).

```bash
V=_local/modalenv/bin/python
ls TASKS_DIR/tasks > all_tasks.txt
# dims 9–10: oracle -> reward 1, no-op -> reward 0
$V skills/behavioral-qc/scripts/modal_gate.py TASKS_DIR all_tasks.txt \
    --workers 200 --state _local/oracle_done.txt --out _local/oracle_results.txt
# verdicts per task: OK / ORACLE-FAIL / NOOP-PASS / BUILD-FAIL   (TSV: task \t verdict \t detail)

# dim 11: verifier soundness — generate k mutants of solve.sh, then run them; ALL must score reward 0
$V skills/behavioral-qc/scripts/mutation_test.py TASKS_DIR all_tasks.txt --generate --mutdir _local/mut --k 3
$V skills/behavioral-qc/scripts/mutation_test.py TASKS_DIR all_tasks.txt --run --mutdir _local/mut \
    --out _local/mutation_results.txt --workers 100
# a task's verifier is SOUND when no mutant scored reward=1
```

Feed the results into the completeness gate as `qc_out/behavioral_signals.json`, a map of
`{task: {"oracle": 1|0, "noop": 1|0, "mutation": 1|0}}` (1 = the good outcome: oracle
passed / no-op failed / all mutants rejected). Turn the `modal_gate.py` TSV into that map
(`OK` ⇒ `oracle:1, noop:0`; `ORACLE-FAIL` ⇒ `oracle:0`; `NOOP-PASS` ⇒ `noop:1`), add
`mutation:1` when no mutant of that task scored reward=1 (else `mutation:0`), and drop it
in `qc_out/`. A missing `mutation` key ⇒ `verifier-sound` is unassessed and the task stays
`qc-incomplete` under `--require-complete`.

> No Modal? The local single-container approximation is
> [`skills/behavioral-qc/scripts/check_behavioral.py`](skills/behavioral-qc/scripts/check_behavioral.py)
> (`--execute`, opt-in and expensive — always confirm scope first). It writes the same
> `behavioral` findings; on Apple Silicon pass `--native-arch`.

## Assemble + enforce completeness

```bash
# 1. static gates (dims are structural, not in this checklist — run first anyway)
python skills/static-semantic-qc/scripts/run_static_qc.py <tasks> --out-dir qc_out
# 2. dims 1–8  (writes sem_*.json / adv_*.json into qc_out/)
python skills/static-semantic-qc/scripts/judge.py <tasks> --out-dir qc_out --static-dir qc_out --role both
# 3. dims 9–11 (Modal oracle/no-op + mutation), then write qc_out/behavioral_signals.json  (see above)
# 4. aggregate with the completeness gate ON:
python shared/aggregate.py qc_out --require-complete
# 5. quarantine anything incomplete or FAILed; promote the rest:
python shared/gate.py qc_out --require-complete
```

`--require-complete` flags any un-assessed dimension as a `qc-incomplete` **FAIL**, so a
task only reaches `promote.txt` once **all eleven** dimensions carry evidence. It is
**off by default** (the OTS precision/recall harness relies on the un-gated behaviour);
turn it on for stringent per-delivery QC.

- Running a reviewer-only pass (no adversary/Modal yet)? Exempt those layers so you are
  not blocked on work you did not intend to do this pass:
  `--no-require-adversary` and/or `--no-require-behavioral`.
- The gap findings roll into the normal SSOT — read them in `defects.csv`
  (`title = qc-incomplete`, with the `fix` naming the tool to run).
