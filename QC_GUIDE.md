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
`over-specified-instruction`, `hardcoded-solution`, `instruction-clarity`,
`spelling-grammar`, `semantic-cheat-vector`, `public-benchmark-contamination`,
`near-duplicate-in-set`. Append `*-ok` (e.g. `tests-ok`) for clean PASS findings.
