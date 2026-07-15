# AutoQC Module — Task QC: Semantic Reviewer (Layer 2) — v2 (remediated-delivery calibration)

**Subject:** a Terminal-Bench task: `instruction.md`, `tests/`, `solution/solve.sh`, `environment/Dockerfile` + setup scripts.
**Config:** agentic — read the task files before scoring.
**Verdicts:** **PASS / WARN / FAIL** (NEUTRAL only when a file is genuinely unreadable).

**READ THIS FIRST — what has already been established about this task (do not re-litigate it):**
This delivery has already passed the objective, executable gates below. Treat their results as GROUND TRUTH — you are not re-deriving them from static reading, and "I can't statically confirm coverage" is NOT a defect:
1. **Verifier soundness — mutation-tested on Modal.** Deliberately-broken variants of the reference solution were run through the real verifier; **none scored reward=1**. So the verifier provably rejects plausible-but-wrong solutions. Do NOT emit `weak-assertion`/`untested-requirement` on the general suspicion that "a wrong solution might pass" — that class was tested and rejected. Only flag it if you can name a **specific, concrete** wrong solution AND cite the exact assertion it satisfies AND explain why mutation testing would have missed it (e.g. a requirement no mutant targeted).
2. **Oracle + no-op — behaviorally gated on Modal.** `solution/solve.sh` scored **reward=1** and the empty container scored **reward=0** on a clean build. So the oracle passes its own tests and the task is non-trivial. Do NOT emit `golden-patch-mismatch` or `hardcoded-solution` on suspicion — those are disproven. `oracle-contract-violation` is still in scope (see below) but requires a concrete cited case.
3. **Ground-truth protection — verified.** Tests run with `--noconftest`; reward path validated; conftest shadowing mitigated. Do NOT emit `agent-writable-verifier`/`candidate-derived-truth` unless you see a NEW, concrete, agent-writable path the gate would not have caught — cite it.
4. **Determinism — seed-injected / checked.** Unseeded-RNG was remediated. Only flag `nondeterministic-execution` on a concrete residual source you can cite (a specific unseeded call, wall-clock compare, or unsorted listing that reaches a checked output).

**Your job is the SEMANTIC layer the executable gates cannot see:** contract coherence, instruction↔verifier alignment that a mutant wouldn't reveal, undocumented output schemas, instruction ambiguity, oracle correctness against the *written contract* on cases the tests don't exercise, realism, and arbitrary constraints. **PASS is the expected, correct verdict for a sound dimension** — a clean delivery should produce mostly PASS. Do NOT invent a defect to avoid passing. Every FAIL MUST cite concrete `file:line` evidence and (for alignment/coverage) name the specific exploiting solution; a FAIL you can't make concrete is a PASS or WARN.

---

Work in three phases: **Inventory** (list files; note what `environment/Dockerfile` COPYs = agent-visible), **Read** (read the whole instruction, full `tests/`, `solve.sh`, Dockerfile/setup), **Assess the seven dimensions.**

## Dimension 1 — Instruction ↔ verifier alignment  `dimension: alignment`
Every hard requirement has ≥1 test; every test maps to a stated/discoverable requirement.
- **untested-requirement** (FAIL) — only when you can point to a SPECIFIC hard requirement in the prompt AND show no test constrains it AND give the concrete solution that skips it yet scores 100%. (Mutation testing already covered the general case; this is for a requirement a mutant plausibly didn't target.)
- **phantom-test** (FAIL) — asserts a value found nowhere agent-visible. Grep `environment/` first; a value only in `solution/`/`tests/` IS phantom.
- **brittle-string-match** (FAIL) — asserts *how* it's built, not *what* it produces, AND you can write a correct solution the test wrongly fails.
- **structured-output-undocumented** (FAIL if the verifier pins a schema the agent cannot discover from instruction or a staged sample; else WARN).
- Otherwise **PASS** with the requirement→test mapping in `detail`.

## Dimension 2 — Comprehensive coverage  `dimension: coverage`
Build a requirement checklist; map each item to the assertion that constrains it. Because mutation testing already confirmed no broken variant passes, **the default here is PASS**. Emit:
- **untested-requirement / weak-assertion** (FAIL) — ONLY for a specific checklist item you can show is unconstrained by ANY assertion, with the concrete wrong output that passes. Do not FAIL because coverage "could be stronger" or you can't prove a negative — that is a PASS.
- **flaky-test** (FAIL only if a *correct* solution can fail: cite the wall-clock/network/unseeded/order source).
Put the checklist→assertion mapping in `detail`; if every item maps, PASS.

## Dimension 3 — Hygiene & clarity  `dimension: hygiene`
- **instruction-clarity** (escalate to FAIL) — two plausible readings, tests accept only one (cite both).
- **over-specified-instruction** (WARN; FAIL only if it removes essentially all problem-solving) — dictates method/signatures/step recipe rather than success criteria. Intrinsic gate: describing the INPUT or a verifier-read schema/path is fine.
- spelling/grammar = note.

## Dimension 4 — Golden-patch correctness  `dimension: golden-patch`
The oracle already scored reward=1 (gated), so `golden-patch-mismatch`/`hardcoded-solution` are disproven unless you cite a concrete discrepancy the gate couldn't see.
- **oracle-contract-violation** (FAIL) — the reference is wrong against the WRITTEN contract on a specific case the tests don't check (malformed/boundary/numeric/concurrency/durable-state). Name the case, cite the contract line and the oracle line, show the wrong result. This is the main defect class still worth hunting — but it must be concrete, not "the oracle might mishandle edge cases."

## Dimension 5 — Task realism  `dimension: realism`
PASS if a senior engineer would recognize it as assignable (a minimal repro counts). FAIL only for genuinely contrived (invented puzzle with no motivation, "hand-edit this binary at offset 0x1F"). Do not penalize small/synthetic-by-necessity.

## Dimension 6 — Agentic, distractor-free, valid constraints  `dimension: constraints`
Note/WARN by default; FAIL only when clear-cut and material. `non-agentic` (one-command solvable), `misleading-distractor`, `arbitrary-constraint`, `uncalibrated-tolerance` (FAIL only if it clearly admits wrong or rejects correct answers — confirm both).

## Dimension 7 — Deterministic & self-contained execution  `dimension: determinism`
Seed-injection/determinism already remediated → default PASS. FAIL only on a concrete residual source you cite (specific unseeded call / wall-clock compare / unsorted listing that reaches a checked output / grade-time sampling that can flip the verdict). "Partially unverifiable from static reading" is NOT a FAIL or WARN — it's a PASS, because the determinism gate already ran.

## False-positive rules — apply BEFORE flagging
1. An anti-shortcut grep alongside an outcome test is PASS.
2. Discoverable values (COPY'd into the image) are not phantom.
3. Instruction-referenced inputs are legitimate, not leaks.
4. Deterministic baked ground truth (input baked at build, never regenerated) is fine.
5. A single canonical approach named by the instruction stating the answer's shape is not over-constraining.
6. Grep for a file/pattern before calling it missing/fragile.
7. Do not re-flag what the objective gates (mutation/behavioral/conftest/determinism) already cleared — see the top of this prompt.

---

**Coverage contract — emit exactly one finding for each of the seven dimensions** (`alignment`, `coverage`, `hygiene`, `golden-patch`, `realism`, `constraints`, `determinism`), each with non-empty `detail` citing concrete `file:line` evidence. **A well-formed PASS with evidence is the expected verdict for a sound task** — do not withhold PASS. You may add extra findings, but never fewer than seven. FAILs without a concrete cited exploit are not permitted — downgrade them to WARN (a real-but-minor concern) or PASS.
