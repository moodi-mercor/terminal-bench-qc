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
verifier_timeout`; resources within client caps (~1 CPU / 4 GB); and
**internet-flag-contradiction** — `allow_internet=false` while the instruction tells
the agent to download/fetch from the network (likely unrunnable offline).

**Dockerfile reproducibility** (`check_dockerfile.py`): build-hygiene smells that make
a task drift across rebuilds (all WARN — non-blocking): **unpinned-base-image**
(`FROM …:latest`/untagged), **apt-no-update** (`apt-get install` with no `update`),
**unpinned-pip** (`pip install pkg` with no `==`), **add-remote-url** (`ADD http(s)://`
fetches at build), **curl-pipe-sh** (`curl … | sh` runs an unpinned remote script),
**dockerfile-entrypoint** (`ENTRYPOINT` set — client infra overrides startup with
`sleep infinity`, so anything it launches never comes up; use `CMD`), **test-deps-in-image**
(a test framework like `pytest` installed in the agent image — test deps belong in the
verifier). Structure (no `FROM`) stays in `check_structure.py`; COPY-leaks in
`check_leakage.py`.

**Instruction static heuristics** (`check_instructions.py`): the mechanically-decidable
instruction defects (the nuanced clarity/over-spec calls are Part 2's job):
**instruction-placeholder** (leftover TODO/FIXME/lorem/`<PLACEHOLDER>`),
**instruction-too-short** (almost no prompt ⇒ underspecified), **instruction-empty**
(missing/empty — FAIL).

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
- **Test imports the solution** — `tests/` does `from solution import …` / `import
  solve`: the verifier can grade against the oracle's own output, not the agent's. FAIL.
- **Skipped/empty scored test** — a test decorated `skip`/`skipif`/`xfail`, or
  `@parametrize(..., [])` over an empty list: it silently never runs. WARN.
These are candidates; a no-op run confirms them. (Subtle gameable logic that only
fires at runtime is *not* statically decidable — that's the delivery-stage gate.)

**Verifier defense detector** (`check_verifier_defenses.py`): the deterministic gate
on Part 3's adversarial cheat-vectors. Reading-only agents can't tell a real exploit
from a theoretical one, so instead of confirming exploits this asks the answerable
question — does the verifier have an anti-cheat **defense** that defeats the
hardcode / fake-artifact class? Detects **mutated-rerun** (test re-runs the agent's
program on regenerated/held-out inputs), **recompute-or-hash** (expected value is
derived, not a baked literal), **source-grep-guard** (greps the agent's code for
hardcoded answers), **re-exec-agent** (runs the agent's produced program, not just
reads its output). `verifier-defended` (PASS) ⇒ cheat-vector candidates against it are
**suppressed** in `aggregate.py` (provably can't work). `verifier-undefended` (WARN) ⇒
no defense found; a cheat-vector here is credible. On eval this deterministically
killed 31→9 of the adversary's flags (81% of its false alarms) with no agent in the loop.

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
- **Baked secret** (`secret-baked-in-image`, WARN) — a live credential (private key,
  AWS/GitHub/Slack token, GCP key) in an agent-visible file: a leaked secret / personal
  data, unless it's a deliberately-planted secret the recovery task is about.
- Exception: a path the **instruction references** is legitimate task input, not a
  leak — downgrade to WARN for manual confirmation.

---

## Part 2 — Semantic review (per-task sub-agent)

> **How Parts 1–3 divide the work** (read this once — it's the thing people get
> confused about):
> - **Part 1** is the deterministic scripts. They flag *candidates* mechanically.
> - **Part 2** is the per-task **reviewer** sub-agent. It applies human judgment
>   the scripts can't, AND verifies Part 1's flags to drop false positives. This is
>   the single canonical semantic rubric — the five checks below.
> - **Part 3** is the per-task **adversary** sub-agent. Different stance entirely:
>   it role-plays the eval model and *tries to beat the verifier without doing the
>   work*. Part 2 asks "is this task correct?"; Part 3 asks "can I cheat it?"
>
> Parts 2 and 3 both fan out one sub-agent per task and both run after Part 1. They
> are decidable by **reading** the task — no task run required. Confirming an
> exploit actually fires at runtime is the delivery-stage behavioral gate (out of
> scope here; see the bottom of this file).

The reviewer reads `instruction.md`, `tests/` (`test.sh` + `test_outputs.py`),
`solution/solve.sh`, and `environment/Dockerfile` + setup scripts, then judges the
five checks below. **Grep the environment for a value before calling a test
"phantom," and read the *whole* test file before flagging a single assertion** —
miscalibration here is the main failure mode.

### Check 1 — Instruction ↔ verifier alignment (bidirectional)

Every hard requirement in the instruction has ≥1 test, AND every test maps to a
requirement that is either stated in the instruction OR discoverable in the
agent-visible environment.

- **Untested requirement** — a hard requirement in the prompt that no test checks.
  The agent could skip it and still score 100%. FAIL (`untested-requirement`).
- **Phantom test** (`phantom-test`, FAIL) — asserts a value/behaviour found nowhere
  the agent can see. Example: test expects `version == "2.4.1"` but no file,
  config, or instruction line mentions `2.4.1`. Before flagging: grep
  `environment/`, source, configs, schema, seed data, error strings — if the value
  appears anywhere agent-visible it is *discoverable*, not phantom.
- **Brittle test / false-reject** (`brittle-string-match`, FAIL) — asserts *how*
  the code is built, not *what* it produces. Litmus test: **construct a correct
  solution this check would wrongly fail.** If you can, it's brittle. Examples:
  - greps source for a function/class/library name (`assert "import pandas" in src`)
    — a correct numpy solution fails;
  - exact-string / whitespace / trailing-newline match on output the spec never
    pinned (`assert out == "Done.\n"` when "done" would be equally correct);
  - file-count or directory-layout guards (`assert len(os.listdir("out")) == 3`)
    when the spec never fixed the file count.
- **Weak test / false-accept** (`weak-assertion`, WARN→FAIL) — too permissive, lets
  a wrong solution pass. Examples: asserts a substring but ignores the process exit
  code; checks only the *format* of a value (`assert resp.json()["total"]` exists
  but never its value); bare `os.path.exists(out)` for a file whose *contents* are
  the actual deliverable. WARN normally; **FAIL when it lets a wrong solution pass
  an essential requirement**.

### Check 2 — Comprehensive test coverage

Tests (rubric and/or unit) verify **every part of the instruction**, across both
the *correctness route* (does it produce the right answer?) and the *optimal-solution
route* (does it do so the way the task demands — e.g. the required algorithm,
performance bound, or API)?

- Flag a requirement covered on only one route (e.g. correctness checked but a
  stated O(n log n) / latency / memory bound never tested).
- **Flaky test** (`flaky-test`, WARN→FAIL) — a test whose pass/fail varies across
  runs of the *same correct solution*: depends on wall-clock timing, network,
  unseeded randomness, ordering of a set/dict, or a race. Tight timing margins
  (e.g. "must finish in <0.5s") are the common offender.
- **Over-constraining literals/functions/strings** — same failure mode as a brittle
  test but framed from coverage: the test pins an incidental implementation detail
  (a specific helper name, intermediate value, or log string) rather than the
  observable result. Report under `brittle-string-match`.

### Check 3 — Hygiene

Spelling, grammar, markdown, and LaTeX in `instruction.md` are clean; no major
ambiguity a competent developer would have to *guess* past. Single typo →
`spelling-grammar` WARN. Ambiguity that changes what gets built (two readings,
both plausible, tests only accept one) → `instruction-clarity`, escalate toward
FAIL. Also flag **over-specification** here (`over-specified-instruction`): the
instruction hands over the solution — enumerated fix lists, step-by-step recipes,
exact bug locations, answer-key tables. The instruction should state *what success
looks like*, not *how to get there*.

### Check 4 — Golden-patch / solution correctness

Confirm `solution/solve.sh` satisfies the spec, implements real logic (no hardcoded
outputs, no reading `tests/`, no lookahead bias), and would score 100% on the happy
path. **Required reasoning order: first name the underlying algorithm/method the
task calls for, then compare the golden patch against a canonical solution for that
method.** This catches a solution that only passes because of test structure rather
than because it actually solves the problem.

- `golden-patch-mismatch` (FAIL) — the golden solution wouldn't actually score
  100% (misses a requirement, wrong output shape, or relies on something absent at
  run time).
- `hardcoded-solution` (FAIL) — `solve.sh` emits the expected answer literally /
  reads it from `tests/` rather than computing it.

### Check 5 — Task realism

Does `instruction.md` describe a workflow a real engineer plausibly performs —
the kind of task that shows up in modern coding-agent / SWE data (fix a failing
test, implement an endpoint, debug a perf regression, parse logs, migrate a
config, repair a broken build)? Realism is about the **plausibility of the
workflow**, not its size — a small, self-contained task can be perfectly realistic.

Calibrate to these bands (use `task-realism`):

- **PASS** — a senior engineer would recognize this as something they could
  plausibly be assigned. Self-contained minimal repro of a real bug class counts.
- **WARN** — plausible domain but artificial framing a real ticket wouldn't have:
  pervasive `foo`/`bar`/`do_thing_1` naming with no domain grounding, a contrived
  backstory, or thresholds/constants that look chosen to make a test pass
  ("benchmark smell").
- **FAIL** — no real-world analog (an invented puzzle/cipher with no plausible
  motivation, unless the task is explicitly a puzzle category), a workflow no dev
  would do (e.g. "hand-edit this binary at offset 0x1F"), or an *internally
  implausible* scenario (the stated motivation contradicts the work, or it
  references tools/services that can't coexist).

**Do not hallucinate unrealism.** Don't penalize a task merely for being small,
synthetic-by-necessity (benchmarks are scoped), or lacking a narrative. Reserve
FAIL for tasks that are genuinely contrived, not merely concise.

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

### Ready-to-run reviewer sub-agent prompt

This agent does **two jobs at once** — the five checks above, and false-positive
verification of Part 1's flags for the same task (they cost one agent together).

> Review the single task at `<TASK_DIR>`. Read `instruction.md`, `tests/`,
> `solution/`, and `environment/Dockerfile` + setup scripts. Its Part-1 static QC
> findings: `<STATIC_FINDINGS_JSON>`.
>
> **(A) Semantic deep-dive — the 5 checks.** Emit one finding per issue, and one
> PASS `*-ok` per clean area.
> 1. **Instruction↔verifier alignment** — every hard requirement is tested;
>    everything tested is in the prompt or discoverable in the env. Grep the env
>    for a value before calling a test phantom. Titles: `untested-requirement`,
>    `phantom-test`, `brittle-string-match`, `weak-assertion`.
> 2. **Comprehensive tests** — every part of the instruction is verified on both
>    the correctness and optimal-solution routes; flag flaky tests
>    (`flaky-test`) and over-constraining literals/functions/strings
>    (`brittle-string-match`).
> 3. **Hygiene** — grammar/typos/formatting (`spelling-grammar`), ambiguity
>    (`instruction-clarity`), over-specification (`over-specified-instruction`).
> 4. **Golden-patch correctness** — *first name the underlying algorithm/method*,
>    then verify `solve.sh` matches a canonical solution and scores 100% with real
>    logic (`golden-patch-mismatch`, `hardcoded-solution`).
> 5. **Realism** — the task resembles a real developer workflow; use the PASS/WARN/
>    FAIL bands; do NOT flag a task merely for being small or self-contained
>    (`task-realism`).
>
> **(B) Verify the static findings.** For each FAIL/WARN in the static findings,
> read the relevant files and try to **refute** it. If it's a false positive (e.g.
> a "leak" path that's actually instruction-referenced input), emit
> `{"title":"verify-refuted","ref":"<that static title>","severity":"PASS","area":<same area>,"task":...,"detail":"why it's a FP"}`.
> If it's a real, exploitable defect, emit `verify-confirm` with evidence. Default
> to leaving a flag in place unless you can clearly show it's a false positive.
>
> Output ONLY a JSON array of findings, schema
> `{"task","area":"instructions|tests|solution","severity":"PASS|WARN|FAIL","title","location","detail","fix"}`
> (plus `ref` on the verify-* metas), using the stable titles. Write it to
> `qc_out/sem_<task>.json`.

---

## Part 3 — Adversarial reward-hack pass (per-task sub-agent)

A **separate** sub-agent per task with the opposite stance from Part 2. It role-plays
the eval model: it is told it will be scored *solely* by `tests/`, and its goal is to
make the verifier report success while doing **as little of the intended work as
possible — ideally none.** It enumerates concrete hacks and decides whether any would
actually pass. A viable hack is a defect in the *task* (the verifier is gameable),
reported as `semantic-cheat-vector` — a **WARN candidate**, not an auto-FAIL (see the
calibration note below; on eval the un-disciplined version flagged 49/50 tasks).

This is the analytical (read-only) half of reward-hack detection. The
statically-decidable patterns — vacuous tests, swallowed assertions, `pytest || true`,
agent-writable reward files, baked-answer leaks, leftover generators — are already
caught in Part 1 (`check_reward_hack.py`, `check_leakage.py`, `check_env_fairness.py`).
This pass catches the **judgment-level** cheat vectors those scripts can't see, by
reasoning about the specific assertions. *Confirming* a hack fires is the
delivery-stage behavioral oracle/no-op + adversarial run (out of scope here); this
pass surfaces the candidate pre-run.

**Hack classes to attempt** (find one that passes `tests/` without solving the task):

- **Hardcode the checked literals** — the verifier asserts specific output values
  that are discoverable (in `tests/`, seed data, or derivable); emit them directly.
- **Write the inspected artifact** — write straight to the file / DB row / path the
  verifier reads, bypassing the computation that should produce it.
- **Stub the called surface** — define the function/endpoint the verifier imports so
  it returns the expected value with no real logic behind it.
- **Exploit a weak assertion** — produce a value that satisfies a substring / format
  / exit-code / existence check but is semantically wrong (pairs with Part 2 check 1).
- **Detect the harness and branch** — behave differently when run under pytest / when
  a test fixture or env var is present.
- **Tamper with the scoring path** — overwrite/monkeypatch the verifier or its
  helpers, write the reward/score file, or force the exit code (pairs with Part 1's
  `agent-writable-reward-signal` / `unconditional-reward`).
- **Echo leaked ground truth** — read the answer from any agent-visible location (a
  baked answer file, a generator script) and print it.

If a viable hack exists → `semantic-cheat-vector` **WARN (a candidate, not a verdict)**
with the **exact recipe** and which assertion it beats. If the verifier resists every
attempt (outcome-based, hidden/held-out inputs, no agent-writable signal) → emit one
PASS `cheat-vector-ok`.

> **Calibration — confirm before you fail (learned the hard way).** On the 50-task
> eval, the raw adversary flagged a cheat-vector on **49 of 50 tasks** (10/10 real
> defects but also 39 clean tasks) — it *rationalizes* a hack for almost everything.
> So a claimed cheat-vector is a **candidate (WARN)**, never an auto-FAIL. It is
> promoted to FAIL only by a **confirmation step**: re-derive the exploit and rule out
> the verifier's defenses — anti-hardcoding source greps, **mutated/regenerated inputs**
> the agent never saw, held-out data, re-computation of the expected value. This is
> Reflection's protocol ("exploit found → confirm it actually beats the verifier →
> only then count"). Unconfirmed cheat-vectors stay WARN; the **behavioral**
> adversarial run (delivery) is the definitive confirm. The reviewer (Part 2), by
> contrast, was well-calibrated on the same eval (0 false positives) and its FAILs
> drive verdicts directly.

### Ready-to-run adversarial sub-agent prompt

> You are a capable but adversarial agent being evaluated on the task at
> `<TASK_DIR>`. You will be scored **only** by `tests/` (read `tests/test.sh` and
> `tests/test_outputs.py`). You also see `instruction.md` and the
> `environment/` the real agent starts from. Your goal: make the verifier report
> **success while doing as little of the intended work as possible — ideally none.**
>
> Work through the hack classes: hardcode checked literals, write the inspected
> artifact directly, stub the called surface, exploit weak assertions, detect the
> harness and branch, tamper with the scoring path, echo any leaked ground truth.
> For each, decide concretely whether it would PASS `tests/`.
>
> **Before you claim a hack, rule out the verifier's defenses — most verifiers have
> them, and a hack that any of these defeats is NOT viable:**
> - an anti-hardcoding **source grep** (the test greps your code for the literal/
>   operator/answer and fails if present);
> - a **mutated / regenerated input** the agent never saw (a second test re-runs on
>   fresh data, so hardcoded values fail there);
> - the verifier **re-computes** the expected value from the same inputs (so writing
>   the artifact directly still has to be correct);
> - held-out/hidden inputs, or a check on intermediate state you can't fake.
> Only claim a cheat-vector you are **highly confident actually PASSES every test**,
> defenses included. When unsure, treat it as resisted. (Over-claiming destroys
> precision — on eval the un-disciplined version flagged 49/50 tasks.)
>
> - If a hack survives all the defenses, emit a **candidate** (WARN, not FAIL):
>   `{"task","area":"tests","severity":"WARN","title":"semantic-cheat-vector","location":"<test + line>","detail":"the exact recipe + which assertion it beats + why each defense fails to stop it","fix":"how to make the verifier outcome-based / non-gameable"}`.
> - Otherwise emit one
>   `{"task","area":"tests","severity":"PASS","title":"cheat-vector-ok","detail":"verifier resists: <which defense stops each hack>"}`.
>
> Output ONLY a JSON array of findings. Write it to `qc_out/adv_<task>.json`. A
> separate confirmation step (or the delivery behavioral run) promotes a confirmed
> candidate to FAIL.

---

## Part 4 — Dataset-level

- **Decontamination** — compare each instruction to the public-benchmark corpus
  (`data/decontam_corpus.jsonl`: Terminal-Bench + SWE-bench + LiveCodeBench + Aider,
  the four NVIDIA names) by similarity; high similarity ⇒ possible contamination /
  trivially searchable. Rebuild with `scripts/build_decontam_corpus.py`.
- **Near-duplicate / template reuse** — high pairwise similarity *within* a
  delivery ⇒ low diversity.

---

## Sub-agent orchestration (Layer 2 driver)

Run the static gates (Part 1) first, then fan out **one sub-agent per task, in
parallel batches** (independent tasks → embarrassingly parallel). Two roles run per
task:

1. **Reviewer + FP-verification** (Part 2) — the 5 semantic checks *and*
   false-positive verification of that task's static findings, in one agent. Writes
   `qc_out/sem_<task>.json`.
2. **Adversary** (Part 3) — the reward-hack red-team, a separate agent so the
   "try to cheat" stance stays clean. Writes `qc_out/adv_<task>.json`.

Then re-run `aggregate.py`. It **auto-drops refuted false positives** (precision
win, from role 1's `verify-refuted` metas) and folds in every new semantic and
cheat-vector finding (recall win). This is the funnel: cheap static on all tasks →
judgment agents only where judgment is needed, verifying static's own output and
red-teaming the verifier.

### Verification output convention (consumed by `aggregate.py`)

The reviewer (role 1) emits one meta finding per static flag it reviewed:
- refute a false positive: `{"task","area","title":"verify-refuted","ref":"<static-title>","severity":"PASS","detail":"why it's a FP"}` → that static finding is dropped from the verdict.
- confirm a real one: `{"task","area","title":"verify-confirm","ref":"<static-title>","severity":"PASS","detail":"evidence"}` → informational; verdict unchanged.

The adversary (role 2) emits ordinary findings (`semantic-cheat-vector` FAIL or
`cheat-vector-ok` PASS) — no special reconciliation; they aggregate like any other.

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
`cmd-entrypoint-reliance`, `unpinned-base-image`, `apt-no-update`, `unpinned-pip`,
`add-remote-url`, `curl-pipe-sh`, `internet-flag-contradiction`,
`test-imports-solution`, `skipped-scored-test`, `empty-parametrize`,
`instruction-placeholder`, `instruction-too-short`, `instruction-empty`,
`dockerfile-entrypoint`, `test-deps-in-image`, `secret-baked-in-image`,
`verifier-defended`, `verifier-undefended`. Append `*-ok`
(e.g. `tests-ok`) for clean PASS findings.
