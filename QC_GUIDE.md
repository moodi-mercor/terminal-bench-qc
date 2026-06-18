# Terminal-Bench QC — Criteria & Rubric (self-contained)

The operating reference for this skill: what each check looks for and how to
judge it. This file is intentionally scrubbed of client-specific feedback and
delivery-stage strategy — those internal docs are kept out of this repo. Pair it
with `SKILL.md` (how to run) and the scripts in `scripts/`.

## Verdict scale

Every check returns **PASS** / **WARN** / **FAIL**. A task's verdict for an area
is its worst finding; the task's overall verdict is its worst area.

- **FAIL** — must fix: missing required file/metadata, answer leaked where the
  agent can read it, an untested hard requirement, a phantom or over-constrained
  verifier, a hardcoded solution.
- **WARN** — fix but non-blocking: over-broad tag, single typo, unpinned test
  dep, time estimate out of range, resource above a client cap, a weak test.
- **PASS** — clean or trivially cosmetic.

Static flags are **candidates, not verdicts** — confirm a flagged leak actually
survives the build and is exploitable before treating it as a real defect. Drive
**recall to 100% first** (catch every real defect), then improve precision.

---

## Part 1 — Static checks (deterministic, `scripts/`)

**Structure** (`check_structure.py`): required files present & non-empty —
`task.toml`, `instruction.md`, `environment/Dockerfile`, `tests/test.sh`,
`solution/solve.sh`; Dockerfile has a base image and isn't trivially empty.

**Metadata** (`check_metadata.py`): `task.toml` has difficulty / category / tags /
expert+junior time / verifier+agent timeouts / env resources; category is
specific (not "programming"); tags specific (not "general"); `junior_time ≥
expert_time > 0`; time estimates within the difficulty's range (watch the
seconds-mistaken-for-minutes smell: values ~60× too high); `agent_timeout ≥
verifier_timeout`; resources within client caps (~1 CPU / 4 GB).

**Reward-hack screen** (`check_reward_hack.py`): the statically-decidable half of
reward-hacking — tests that pass without the work, and gameable pass signals:
- **Vacuous tests** — a test body that's trivial (`pass`/`return`/`assert True`),
  an assertion swallowed by `except: pass`, an existence-only check, or a test
  with no assertion at all.
- **Swallowed verifier** — `tests/test.sh` runs `pytest ... || true` (failure
  ignored). Benign `|| true` on dep installs / pre-runs is not flagged.
- **Unconditional reward** — `test.sh` writes a passing reward not gated on the
  verifier's exit code (FAIL).
- **Agent-writable pass signal** — the verifier reads a reward/score/status file
  the agent could write.
These are candidates; a no-op run confirms them. (Subtle gameable logic that only
fires at runtime is *not* statically decidable — that's the delivery-stage gate.)

**Environment fairness** (`check_env_fairness.py`): the statically-decidable half
of "task fairness" — confirm the agent's starting context is only the intended
input by reconstructing what the build leaves in the image. Flags **leftover
generators** (`create_*`/`generate*`/`mutate*` scripts left in the image — the
agent can read how data/answers are made), **uncleaned setup scripts**,
**git-history-exposed** (`git clone` with no `.git` removal), and **runtime-network**
(the verifier fetching an external URL). What's left for the run: probing that the
agent truly can't reach something, and container/network isolation (an infra
guarantee). Reading `tests/`/`solution/` at solve time is architecturally
impossible (verify-time mounts).

**Portability** (`check_portability.py`): the `solve.sh`/test defects that
dominated the customer's validation tail and are visible by reading the files —
**backgrounded-daemon-no-redirect** (pipe-hang, their #1 lever), **pip without
--break-system-packages** (PEP 668), **server-defined-not-started**,
**redis-no-daemonize**, **mixed-bash-python solve**, **broad pkill -f**, plus test
**systemd-assumption** and **cmd-entrypoint-reliance**. (A real reward=1.0 smoke
run is still the definitive catch — this flags them pre-run.)

**Leakage / anti-cheat** (`check_leakage.py`): the agent's container is built
**only** from `environment/Dockerfile`; `tests/` and `solution/` are mounted at
verify time and must never be COPY'd in. Flag:
- Dockerfile/setup COPY of `solution/` or `tests/` into the image.
- Ground-truth/answer files written at build time into an **agent-visible** path
  (anywhere except `/tmp`, `/tests` scratch) that the **verifier reads** as the
  expected value — the classic "answer left in the workspace" leak.
- Hint files (notes/answer/walkthrough) copied into the image.
- Exception: a path the **instruction references** is legitimate task input, not a
  leak — downgrade to WARN for manual confirmation.

---

## Part 2 — Semantic review (sub-agent rubric)

The reviewer reads `instruction.md`, `tests/`, `solution/`, and the Dockerfile,
and judges what static tools can't:

**Instruction ↔ test alignment (bidirectional)** — every hard requirement in the
instruction has ≥1 test, AND every test maps to a requirement that is either
stated OR discoverable in the agent-visible environment. **Grep the environment
for a value before calling a test "phantom."**

- **Phantom test** — checks a value/behaviour found nowhere the agent can see.
  FAIL.
- **Brittle test (false-reject)** — asserts *how* the code is built, not *what* it
  produces: source-code greps for function/library names, exact-string or
  whitespace matches the spec never fixed, file-count/directory-layout guards.
  Test: construct a *correct* solution that this check would wrongly fail. FAIL.
- **Weak test (false-accept)** — too permissive: asserts a substring but ignores
  the return code, checks only the *format* of a value, or bare file-existence.
  WARN→FAIL if it lets a wrong solution pass an essential requirement.
- **Over-specification** — the instruction hands over the solution: enumerated
  fix lists, step-by-step recipes, exact bug locations, answer-key tables. The
  instruction should state *what success looks like*, not *how to get there*.
- **Hygiene & realism** — spelling/grammar/markdown clean; the task resembles a
  real developer workflow; no major ambiguity a competent dev would have to guess.

**Solution review** — `solve.sh` satisfies the spec, implements real logic (no
hardcoded outputs, no reading test files, no lookahead bias), and is a reasonable
approach.

### False-positive rules (check BEFORE flagging)

1. **Anti-shortcut guards are PASS, not WARN.** A grep/source check that sits
   *alongside* a runtime/outcome test is a guard against gaming — not "static-only
   validation." Read the whole test file first.
2. **Discoverable values are not phantom.** A value present in any agent-visible
   file (config, source, schema, data, init SQL, error message) is discoverable.
3. **Instruction-referenced inputs are legitimate**, not leaks.
4. **Deterministic ground truth is fine.** Hardcoded expected values are correct
   when the input data is baked into the image and never changes, or when the task
   requires finding *all* of a known set.
5. **The one canonical approach** named in an instruction/test is stating the
   answer's shape, not over-constraining, when there's genuinely one right way.

---

## Part 3 — Dataset-level

- **Decontamination** — compare each instruction to the public Terminal-Bench
  corpus (`data/decontam_corpus.jsonl`) by similarity; high similarity ⇒ possible
  contamination / trivially searchable.
- **Near-duplicate / template reuse** — high pairwise similarity *within* a
  delivery ⇒ low diversity.

---

## Deep-dive QC routine (per-task)

Run this as one sub-agent per task, *in addition* to the static + semantic
passes. These five checks are fully decidable by analysis (reading the task) — no
task run required.

> The statically-decidable parts of reward-hacking and fairness — vacuous tests,
> gameable pass signals, baked-answer leaks, leftover generators / setup scripts /
> git history — are caught in **Part 1** (`check_reward_hack.py`,
> `check_leakage.py`, `check_env_fairness.py`). What remains for the delivery-stage
> run is *confirming* an exploit actually fires and adversarially probing
> reachability — those need execution.

1. **Instruction ↔ verifier alignment** — everything in the prompt is tested, and
   everything tested is in the prompt (or discoverable in the agent-visible env).
2. **Comprehensive test cases** — rubric and/or unit tests verify **every part of
   the instruction**, across both the correctness route and the optimal-solution
   route. Flag brittle tests that go flaky across runs, and tests that
   over-constrain the path via hardcoded literals / functions / strings.
3. **Hygiene** — grammar, typos, markdown/LaTeX.
4. **Golden-patch correctness** — confirm the golden solution mirrors the happy
   path and would score 100%. In chain-of-thought, **first identify the underlying
   algorithm/method** the task calls for, **then** check the golden patch against
   a top/canonical solution for that method — confirm it solves the problem
   properly, not via a shortcut that only works because of test structure.
5. **Task realism** — `instruction.md` aligns with a real developer workflow
   plausibly found in modern coding-agent data.

### Ready-to-run deep-dive sub-agent prompt

> Do a deep-dive QC run on `<TASK_DIR>` for the five checks below. Read
> `instruction.md`, `tests/`, `solution/`, and `environment/Dockerfile` + setup
> scripts.
> 1. **Instruction↔verifier alignment** — everything in the prompt is tested;
>    everything tested is in the prompt or discoverable in the env.
> 2. **Comprehensive tests** — every part of the instruction is verified on both
>    the correctness and optimal-solution routes; flag flaky/brittle tests and
>    over-constraining hardcoded literals/functions/strings.
> 3. **Hygiene** — grammar, typos, formatting.
> 4. **Golden-patch correctness** — in your reasoning, first name the underlying
>    algorithm/method, then verify the golden solution matches a canonical
>    solution and would score 100% (no shortcut).
> 5. **Realism** — the task resembles a real developer workflow.
> Output a JSON array of findings:
> `{"task","area":"instructions|tests|solution","severity":"PASS|WARN|FAIL","title","location","detail","fix"}`
> using the stable titles below. Emit one PASS `*-ok` per clean area.

## Sub-agent orchestration: review + verify (Layer 2 driver)

Run the static gates first, then fan out **one sub-agent per task, in parallel
batches** (independent tasks → embarrassingly parallel). Each agent does two jobs
at once, so semantic review and false-positive control cost the same single agent:

1. **Semantic deep-dive** — the 5 checks above.
2. **Adversarial verification of this task's static findings** — it is handed the
   static findings for its task and must try to **refute** each FAIL/WARN: read the
   build context and decide if the flag is a real, exploitable defect or a false
   positive (e.g. a "leak" path that's actually instruction-referenced input).

The agent writes a JSON array to `qc_out/sem_<task>.json`. Re-running
`aggregate.py` then **auto-drops refuted false positives** (precision win) and
folds in the semantic findings (recall win). This is the funnel: cheap static on
all tasks → judgment agents only where judgment is needed, verifying static's own
output.

### Verification output convention (consumed by `aggregate.py`)

In addition to normal semantic findings, the agent emits one meta finding per
static flag it reviewed:
- refute a false positive: `{"task","area","title":"verify-refuted","ref":"<static-title>","severity":"PASS","detail":"why it's a FP"}` → that static finding is dropped from the verdict.
- confirm a real one: `{"task","area","title":"verify-confirm","ref":"<static-title>","severity":"PASS","detail":"evidence"}` → informational; verdict unchanged.

### Combined sub-agent prompt

> Review the single task at `<TASK_DIR>`. Its static QC findings: `<STATIC_FINDINGS_JSON>`.
> **(A) Semantic deep-dive** — apply the 5 checks (instruction↔verifier alignment,
> comprehensive tests, hygiene, golden-patch correctness, realism); emit a finding
> per issue (and a `*-ok` PASS per clean area).
> **(B) Verify the static findings** — for each FAIL/WARN above, read the relevant
> files and try to REFUTE it. If it's a false positive, emit
> `{"title":"verify-refuted","ref":"<that title>","severity":"PASS","area":<same>,"task":...,"detail":...}`.
> If it's real, emit `verify-confirm` with evidence. Default to leaving a flag in
> place unless you can clearly show it's a false positive.
> Output ONLY a JSON array of findings (schema:
> `{"task","area","severity","title","location","detail","fix"}`, plus `ref` on the
> verify-* metas). Write it to `qc_out/sem_<task>.json`.

## Out of scope here: behavioral

The runtime **oracle/no-op** gate (reference solution → pass, untouched container
→ fail) is run at the **delivery stage** on the client's target infra, not by this
skill. If those results are available, drop them into the findings dir as
`{task, area:"behavioral", severity, title, ...}` and they aggregate with the
rest.

---

## Stable defect-class titles (for the distribution report)

Use these exact titles so the histogram groups cleanly:

`missing-required-file`, `missing-metadata`, `generic-category`, `broad-tags`,
`expert-time-out-of-range`, `cpus-above-client-cap`, `dockerfile-copies-solution`,
`dockerfile-copies-tests`, `truth-baked-verifier-reads`, `tests-bake-verifier-reads`,
`untested-requirement`, `phantom-test`, `brittle-string-match`, `weak-assertion`,
`flaky-test`, `over-specified-instruction`, `hardcoded-solution`,
`golden-patch-mismatch`, `task-realism`, `instruction-clarity`, `spelling-grammar`,
`semantic-cheat-vector`, `public-benchmark-contamination`, `near-duplicate-in-set`,
`vacuous-test`, `swallowed-assertion`, `existence-only-check`, `no-assertion-test`,
`test-sh-swallows-failure`, `unconditional-reward`, `agent-writable-reward-signal`,
`leftover-generator`, `uncleaned-setup-script`, `git-history-exposed`,
`runtime-network`, `reference-solve-reads-truth`, `memory-vs-workload`,
`backgrounded-daemon-no-redirect`, `pip-no-break-system-packages`,
`server-defined-not-started`, `redis-no-daemonize`, `mixed-bash-python-solve`,
`broad-pkill`, `config-edit-no-restart`, `systemd-assumption`,
`cmd-entrypoint-reliance`. Append `*-ok`
(e.g. `tests-ok`) for clean PASS findings.
