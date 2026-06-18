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

## Deep-dive QC routine (per-task, trajectory-aware)

Run this as one sub-agent per task, *in addition* to the static + semantic
passes. It captures the high-signal checks that surface real defects — especially
the trajectory reading (#2) and the setup/runtime/cleanup fairness probe (#7),
which static screening misses.

1. **Instruction ↔ verifier alignment** — everything in the prompt is tested, and
   everything tested is in the prompt (or discoverable in the agent-visible env).
2. **Reward-hackable criteria / test cases** — read the `evals/` folder and the
   agent **trajectories** if present. Flag any case where the agent benefits from
   access it shouldn't have, uses a disallowed approach, or hardcodes solutions.
   **Even if the trajectory looks clean, spawn an adversarial sub-agent that tries
   to cheat** — satisfy the verifier *outside* a genuine developer approach
   (hardcode expected outputs, write the expected files/rows directly, stub
   IDs/timestamps, read leaked state, move a forbidden literal into a swapped
   file). Any working exploit ⇒ **FAIL**; the fix is an assertion that closes it,
   after which the golden solution must still pass.
3. **Comprehensive test cases** — rubric and/or unit tests verify **every part of
   the instruction**, across both the correctness route and the optimal-solution
   route. Flag brittle tests that go flaky across runs, and tests that
   over-constrain the path via hardcoded literals / functions / strings.
4. **Hygiene** — grammar, typos, markdown/LaTeX.
5. **Golden-patch correctness** — confirm the golden solution mirrors the happy
   path and would score 100%. In chain-of-thought, **first identify the underlying
   algorithm/method** the task calls for, **then** check the golden patch against
   a top/canonical solution for that method — confirm it solves the problem
   properly, not via a shortcut that only works because of test structure.
6. **Task realism** — `instruction.md` aligns with a real developer workflow
   plausibly found in modern coding-agent data.
7. **Task fairness + cheating potential** — deep-dive the environment across
   **setup, runtime, and cleanup** to confirm the agent's context is **exactly**
   what the instruction/README defines and nothing more. **Spawn an adversarial
   sub-agent that tries to reach other parts of the environment** (leaked files,
   build artifacts, network, sibling containers/services) to confirm it cannot.

### Ready-to-run deep-dive sub-agent prompt

> Do a deep-dive QC run on `<TASK_DIR>` for the seven checks below. Read
> `instruction.md`, `tests/`, `solution/`, `environment/Dockerfile` + setup
> scripts, and the `evals/`/trajectory files if present.
> 1. **Instruction↔verifier alignment** — everything in the prompt is tested;
>    everything tested is in the prompt or discoverable in the env.
> 2. **Reward-hackable criteria** — read evals/trajectories; flag access the agent
>    shouldn't have, disallowed approaches, or hardcoded solutions. Then act as an
>    adversary: find a way to pass the verifier without genuinely solving the task.
>    Report any exploit + the assertion that would close it.
> 3. **Comprehensive tests** — every part of the instruction is verified on both
>    the correctness and optimal-solution routes; flag flaky/brittle tests and
>    over-constraining hardcoded literals/functions/strings.
> 4. **Hygiene** — grammar, typos, formatting.
> 5. **Golden-patch correctness** — in your reasoning, first name the underlying
>    algorithm/method, then verify the golden solution matches a canonical
>    solution and would score 100% (no shortcut).
> 6. **Realism** — the task resembles a real developer workflow.
> 7. **Fairness** — trace setup→runtime→cleanup; confirm the agent sees only what
>    the instruction defines. Adversarially probe for reachable leaked
>    files/artifacts/network/other env parts.
> Output a JSON array of findings:
> `{"task","area":"instructions|tests|solution|anti_cheat","severity":"PASS|WARN|FAIL","title","location","detail","fix"}`
> using the stable titles below. Emit one PASS `*-ok` per clean area.

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
`reward-hackable`, `env-fairness-breach`, `golden-patch-mismatch`,
`task-realism`, `instruction-clarity`, `spelling-grammar`, `semantic-cheat-vector`,
`public-benchmark-contamination`, `near-duplicate-in-set`. Append `*-ok` (e.g.
`tests-ok`) for clean PASS findings.
