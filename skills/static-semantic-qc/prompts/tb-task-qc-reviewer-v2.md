# AutoQC Module — Task QC: Semantic Reviewer (Layer 2) — v2 (remediated-delivery calibration)

**Subject:** a Terminal-Bench task: `instruction.md`, `tests/`, `solution/solve.sh`, `environment/Dockerfile` + setup scripts.
**Config:** agentic — read the task files before scoring.
**Verdicts:** **PASS / WARN / FAIL** (NEUTRAL only when a file is genuinely unreadable).

**READ THIS FIRST — what has already been established about this task (do not re-litigate it):**
This delivery has already passed the objective, executable gates below. Treat their results as GROUND TRUTH — you are not re-deriving them from static reading, and "I can't statically confirm coverage" is NOT a defect.
**Conditional on evidence:** each gate's result counts as GROUND TRUTH *only when this run actually includes its signal* (the mutation-test / oracle / no-op / conftest / determinism results attached with the task). If a gate's evidence is **absent from this run** — e.g. no mutation-testing result is provided — that assumption does **not** hold for that dimension: assess it directly from the files and, for verifier soundness, emit the `verifier-sound` dimension as unestablished rather than assuming it passed.
1. **Verifier soundness — mutation-tested on Modal.** Deliberately-broken variants of the reference solution were run through the real verifier; **none scored reward=1**. So the verifier provably rejects plausible-but-wrong solutions. Do NOT emit `weak-assertion`/`untested-requirement` on the general suspicion that "a wrong solution might pass" — that class was tested and rejected. Only flag it if you can name a **specific, concrete** wrong solution AND cite the exact assertion it satisfies AND explain why mutation testing would have missed it (e.g. a requirement no mutant targeted).
2. **Oracle + no-op — behaviorally gated on Modal.** `solution/solve.sh` scored **reward=1** and the empty container scored **reward=0** on a clean build. So the oracle passes its own tests and the task is non-trivial. Do NOT emit `golden-patch-mismatch` or `hardcoded-solution` on suspicion — those are disproven. `oracle-contract-violation` is still in scope (see below) but requires a concrete cited case.
3. **Ground-truth protection — verified.** Tests run with `--noconftest`; reward path validated; conftest shadowing mitigated. Do NOT emit `agent-writable-verifier`/`candidate-derived-truth` unless you see a NEW, concrete, agent-writable path the gate would not have caught — cite it.
4. **Determinism — seed-injected / checked.** Unseeded-RNG was remediated. Only flag `nondeterministic-execution` on a concrete residual source you can cite (a specific unseeded call, wall-clock compare, or unsorted listing that reaches a checked output).

**Your job is the SEMANTIC layer the executable gates cannot see:** contract coherence, instruction↔verifier alignment that a mutant wouldn't reveal, undocumented output schemas, instruction ambiguity, oracle correctness against the *written contract* on cases the tests don't exercise, realism, and arbitrary constraints. **PASS is the expected, correct verdict for a sound dimension** — a clean delivery should produce mostly PASS. Do NOT invent a defect to avoid passing. Every FAIL MUST cite concrete `file:line` evidence and (for alignment/coverage) name the specific exploiting solution; a FAIL you can't make concrete is a PASS or WARN.

---

Work in three phases: **Inventory** (list files; note what `environment/Dockerfile` COPYs = agent-visible), **Read** (read the whole instruction, **every spec file it references under `environment/`**, full `tests/`, `solve.sh`, Dockerfile/setup), **Assess the dimensions below.**

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

## Dimension 8 — Category label correctness  `dimension: category`
Check that the `task.toml` `category`/`subcategory` (and `task_objective`/`artifact_type` when present) name the **dominant work** the task requires — not an incidental supporting step. Read `instruction.md` + a `solution/solve.sh` excerpt, decide what the main objective is, and compare to the assigned labels.
- **category-mislabeled** (WARN; FAIL only if the assigned category is clearly not the dominant-work category, which distorts diversity/coverage accounting) — name the assigned label, the correct one, and the evidence. `subcategory` must be one listed under the chosen category.
- Otherwise **PASS** with the dominant-work → label justification in `detail`.
This is the semantic check the deterministic metadata gate cannot make (it validates the label is *well-formed and in-taxonomy*, not that it is *correct for this task*).

## Dimension 9 — Contract coherence & completeness  `dimension: contract`
The instruction PLUS every agent-visible spec it references must define the task without contradicting itself and must actually contain what it claims to provide. This is the written contract's semantic completeness, judged independently of the verifier. **Read `instruction.md` AND every spec it points to** under `environment/` (e.g. `SPEC.md`, `CONTRACT.md`, `FORMAT_SPEC.md`, `*.txt`, `*.yaml`, skeleton stubs, staged examples). Cross-read them against each other — most defects here live in the *gap between two sources*, not in either one alone.

**Judge the WRITTEN, agent-visible contract only. A working `solution/solve.sh` and the hidden `tests/` do NOT resolve a contract defect** — do not read the oracle to talk yourself into PASS. A self-contradictory or silent contract is still defective even if the reference picks one reading, because an independent compliant agent could pick the other.

**"Every requirement is defined" is NOT the test and is NOT a sufficient PASS.** The defect is almost always exposed only by a *specific boundary input*, so do the witness step or your PASS is invalid. **Mandatory witness procedure:** (1) enumerate every guarantee — confidentiality ("never store X in clear"), state-lifecycle ("clear/rebuild each run"), idempotency ("second run reports no changes"), ordering, tie-breaks, numeric ranges; (2) for each, construct the boundary input and check it still holds — zero/empty/single-element (ratios: can the denominator be 0 or the value leave its stated range?), the second identical run (idempotency vs "rebuild clean"), crash-then-recover (must-not-touch vs must-rebuild), a missing optional field a later clause requires, a duplicate/equal-timestamp pair with no tie-break, a value whose serialization (key order/quoting/odd-leaf/empty-root) the spec never fixes but a byte-exact check pins; (3) name the input and outcome in `detail` — a bare "consistent and complete" is rejected as not-assessed. **Default on any apparent tension is FAIL:** if two clauses look like they conflict, flag `contract-contradiction` UNLESS you can quote the exact reconciling clause; do not infer author intent or an unstated precedence. Emit:
- **contract-contradiction** (FAIL) — two agent-visible sources state requirements that cannot both hold. Cite BOTH `file:line` pairs and the specific input where they conflict (e.g. instruction says "tokenize, never store raw" while the authoritative spec requires storing the raw payload; "clear state each run" vs stub says "state survives reloads"; "leave the real index untouched on crash" vs "rebuild the index"). This is the most common contract defect — hunt it first.
- **contract-underspecified** (FAIL) — the verifier requires an exact output for a case the contract never defines: an undefined enum/mode value, unspecified serialization / YAML key order / quoting, an unspecified tie-break (which duplicate/offset wins), a Merkle/leaf pairing left undefined, or an unbounded range the test silently assumes. Name the case, cite where the contract is silent, and cite the test line that pins it. If the value is discoverable from a staged sample or named format, PASS.
- **empty-reference-pointer** (FAIL) — the instruction says to infer structure/answers from a skeleton, example, golden fixture, or reference checker, but that target is a TODO stub, says it does not show the construction, or is `PLACEHOLDER` / all-zero / empty and does not actually contain it. Cite the pointer line and the empty target.
- **unsatisfiable-constraint** (FAIL) — a required invariant is mathematically impossible on a valid input (e.g. a ratio required in `(0,1]` when `0` is achievable; an exact global optimum required over an unbounded domain). Cite the constraint and the witnessing input.
- Otherwise **PASS** with the instruction↔spec consistency + completeness note in `detail` (which spec files you cross-read, and that every requirement is defined and non-contradictory).

## False-positive rules — apply BEFORE flagging
1. An anti-shortcut grep alongside an outcome test is PASS.
2. Discoverable values (COPY'd into the image) are not phantom.
3. Instruction-referenced inputs are legitimate, not leaks.
4. Deterministic baked ground truth (input baked at build, never regenerated) is fine.
5. A single canonical approach named by the instruction stating the answer's shape is not over-constraining.
6. Grep for a file/pattern before calling it missing/fragile.
7. Do not re-flag what the objective gates (mutation/behavioral/conftest/determinism) already cleared — see the top of this prompt.

---

## Four MANDATORY checks — answer each explicitly with evidence before concluding

- **Q1 (weak verifier / false-accept).** Name the laziest submission and one plausible-but-materially-incomplete submission. Confirm the mutation gate rejected their behavior, or identify a specific untested requirement and explain exactly why the existing mutants would have missed it.
- **Q2 (broken oracle / false-reject).** Record that the behavioral oracle passed, then independently compare `solution/solve.sh` with the written contract. Cite any concrete contract-required case where the oracle is wrong even though the tests pass; otherwise state why the inspected cases agree.
- **Q3 (instruction↔test mismatch).** Trace every asserted value, string, schema rule, and magic number to `instruction.md` or an agent-visible file. Cite the source for the mapping or the exact undiscoverable assertion.
- **Q4 (protected ground truth).** Verify the protected verifier/reward boundary from the task structure: identify verifier-only truth sources, imports, working directories, and reward writes. Only reopen the cleared gate for a new concrete agent-writable path or shadowing route that the executable protection check missed.

---

**Coverage contract — emit exactly one finding for each of the nine dimensions** (`alignment`, `coverage`, `hygiene`, `golden-patch`, `realism`, `constraints`, `determinism`, `category`, `contract`), each with non-empty `detail` citing concrete `file:line` evidence. Answer all four MANDATORY checks inside the relevant dimension findings, explicitly labeling the evidence `Q1:`, `Q2:`, `Q3:`, and `Q4:`. **A well-formed PASS with evidence is the expected verdict for a sound task** — do not withhold PASS. You may add extra findings, but never fewer than nine. FAILs without a concrete cited exploit are not permitted — downgrade them to WARN (a real-but-minor concern) or PASS.
