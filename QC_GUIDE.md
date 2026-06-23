# Terminal-Bench QC — Criteria & Rubric

The operating reference for the Layer-1 (static + semantic) skill: **what each check
looks for and how to judge it.** Pair it with
[`SKILL.md`](skills/static-semantic-qc/SKILL.md) (what it is / how to run) and the
gate scripts in `skills/static-semantic-qc/scripts/`. This file is intentionally
scrubbed of client-specific feedback and delivery-stage strategy — those internal
docs are kept out of this repo.

**Contents**

- [How the parts fit together](#how-the-parts-fit-together) — start here for the mental model
- [Verdict scale](#verdict-scale) — PASS / WARN / FAIL definitions
- [Part 1 — Static checks](#part-1--static-checks-deterministic-scripts) — the deterministic gates (`skills/static-semantic-qc/scripts/`)
- [Part 2 — Semantic review](#part-2--semantic-review-per-task-sub-agent) — the per-task reviewer (5 checks + FP verification)
- [Part 3 — Adversarial reward-hack pass](#part-3--adversarial-reward-hack-pass-per-task-sub-agent) — the per-task red-team
- [Part 4 — Dataset-level](#part-4--dataset-level) — decontamination, near-duplicates
- [Sub-agent orchestration](#sub-agent-orchestration-parts-23-driver) — how Parts 2–3 fan out and aggregate
- [Out of scope: behavioral](#out-of-scope-behavioral)
- [Stable defect-class titles](#stable-defect-class-titles-for-the-distribution-report)

## How the parts fit together

Read this once — it's the mental model the rest of the file assumes. The pipeline
has four parts. **Every part scores tasks by *reading* them; no task is executed
here** — runtime confirmation is the delivery-stage behavioral gate, which is
[out of scope](#out-of-scope-behavioral).

- **Part 1 — Static checks.** Deterministic gate scripts that flag *candidates*
  mechanically across every task. Cheap, so run first.
- **Part 2 — Semantic review.** One **reviewer** sub-agent per task. It applies the
  judgment the scripts can't (the six checks in Part 2) *and* verifies Part 1's
  flags to drop false positives. The question it asks: ***"is this task correct?"***
- **Part 3 — Adversarial reward-hack pass.** One **adversary** sub-agent per task,
  with the opposite stance: it role-plays the eval model and tries to beat the
  verifier *without doing the work*. The question it asks: ***"can I cheat it?"***
- **Part 4 — Dataset-level.** Decontamination and near-duplicate checks across the
  whole delivery, not per task.

Parts 2 and 3 run **after** Part 1 and fan out in parallel (one sub-agent each per
task); their findings feed back through
[`aggregate.py`](#sub-agent-orchestration-parts-23-driver), which drops the
false positives Part 2 refuted and folds in the new defects Parts 2–3 found.

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

**Structure + package hygiene** (`check_structure.py`): required files present &
non-empty — `task.toml`, `instruction.md`, `environment/Dockerfile`, `tests/test.sh`,
`solution/solve.sh`; Dockerfile has a base image and isn't trivially empty. Plus
Reflection's package-hygiene rules (all WARN): **task-name-not-kebab** /
**task-name-too-long** (the dir name must be lowercase-kebab and concise),
**unnecessary-files** (`.git`/`__pycache__`/`.venv`/`node_modules`/`.DS_Store`/`*.pyc`/
`*.log` shipped in the package), **crlf-line-endings** (Windows CRLF in task text files,
unless the task targets Windows), **non-text-asset** (an agent-visible PDF/office doc a
non-multimodal model can't read).

**Metadata** (`check_metadata.py`): **schema-tolerant** — validates both the TB2/OTS
shape (`difficulty`/`tags`/`expert+junior time` in *minutes*) and Reflection's Harbor
shape (`subcategory`/`task_objective[]`/`artifact_type[]`/`expert_time_estimate_hours`/
`model_tested`/`agent_tested`/`avg_at_8`/`[environment].build_timeout_sec`). A task is
treated as Reflection-shaped when it carries any Reflection-only key, and neither
schema is flagged for the other's missing fields. Shared to both: category specific
(not "programming"); verifier+agent timeouts present & positive; `agent_timeout ≥
verifier_timeout`; env resources within client caps (~1 CPU / 4 GB) and not left at
the template zero (`placeholder-zero-resource`); **internet-flag-contradiction**
(`allow_internet=false` while the instruction tells the agent to fetch from the
network). TB2-only: tags specific (not "general"); `junior_time ≥ expert_time > 0`;
time within the difficulty band (**easy** 5–60 / 20–120, **medium** 5–180 / 10–480,
**hard** 300–480 / 600–19200 min) with the seconds-mistaken-for-minutes smell (values
~60× too high; flag the *pattern* for a whole batch, not each task). Reflection-only:
`subcategory` present; `task_objective`/`artifact_type` present and drawn from the
diversity taxonomy (`unknown-task-objective` / `unknown-artifact-type`); difficulty
benchmarked on the mandated model+agent (`model_tested` ∈ Opus 4.8 / GPT-5.4,
`agent_tested` = Terminus-2); and the **difficulty bar** — `avg_at_8 ≤ 0.5` (a recorded
`avg_at_8 > 0.5` is `avg-at-8-too-easy`, **FAIL** — the frontier model solves it too
often). `build_timeout_sec` present & positive.

**Dockerfile reproducibility + structuring** (`check_dockerfile.py`): build-hygiene
smells + Reflection's Dockerfile-structuring rules (all WARN — non-blocking).
**unpinned-base-image** (`FROM …:latest`/untagged) is universal; the Harbor-specific
base-image rules fire only on **Reflection-schema tasks** (auto-detected via
`is_reflection_schema`, so legacy OTS tasks aren't blanketed): **base-image-not-digest-pinned**
(tagged but no `@sha256` — Reflection requires digest pinning), **base-image-not-approved**
(`FROM` outside the pre-approved set: python/debian/ubuntu/node/rust/go/gcc/ruby/maven/
eclipse-temurin on `public.ecr.aws/docker/library`). Universal structuring smells: **apt-no-update** (`apt-get install`
with no `update`), **apt-not-consolidated** (apt installs split across >1 RUN layer),
**apt-get-upgrade** (defeats digest pinning), **unpinned-pip** (`pip install pkg` with no
`==`), **add-remote-url** (`ADD http(s)://` fetches at build), **curl-pipe-sh** (`curl … | sh`),
**missing-multistage-build** (compiles an artifact — `cargo/go build`, `mvn package`,
`npm run build`, `dotnet publish` — in a single stage, so the toolchain ships to runtime),
**broad-chmod** (`chmod -R` rewrites every file's mode), **dockerfile-heredoc-source**
(source embedded via `RUN cat > f <<EOF` — put it on disk and COPY it),
**archive-fixture-not-extracted** (a `.tar.gz`/`.zip` COPY'd in as an opaque archive —
extract at build), **missing-dockerignore** (non-trivial `environment/` tree with no
`.dockerignore`), **dockerfile-entrypoint** (`ENTRYPOINT` set — client infra overrides
startup with `sleep infinity`; use `CMD`), **test-deps-in-image** (a test framework like
`pytest` baked into the agent image — test deps belong in the verifier). Structure
(no `FROM`) stays in `check_structure.py`; COPY-leaks in `check_leakage.py`.

**Instruction static heuristics** (`check_instructions.py`): the mechanically-decidable
instruction defects (the nuanced clarity/over-spec calls are Part 2's job):
**instruction-placeholder** (leftover TODO/FIXME/lorem/`<PLACEHOLDER>`),
**instruction-too-short** (almost no prompt ⇒ underspecified), **instruction-too-long**
(over ~1500 tokens — Reflection caps instruction length), **instruction-relative-path**
(explicit `./`/`../` paths — Reflection requires absolute paths for files the agent
reads/modifies/creates), **instruction-empty** (missing/empty — FAIL).

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
- **Agent-writable verifier** (`agent-writable-verifier`, FAIL) — the Dockerfile
  COPYs a grading script (`verify.py` / `grader.sh` / `check.py` …) into an
  agent-visible path and `tests/` *invokes* it to grade. Because the agent (root, no
  `USER` drop) can overwrite that script with one that prints `SUCCESS`, the verifier
  is trivially defeated. The grader must live under `tests/` (verify-time read-only
  mount), never in agent-writable space. *(Reference's "most common anti-cheat hole";
  real example caught: a task running `python3 /root/verify_logic.py` from `tests/`.)*
- **Skipped/empty scored test** — a test decorated `skip`/`skipif`/`xfail`, or
  `@parametrize(..., [])` over an empty list: it silently never runs. WARN.
- **`set -e` reward abort** (`test-sh-set-e-reward-abort`, WARN) — `test.sh` runs
  under `set -e` with no `set +e` around the verifier, then branches on its exit
  code to write the reward. On a failing run `set -e` aborts *before* the `reward=0`
  write, so a no-op produces no reward file instead of 0.0 (breaks no-op grounding).
  Fix: bracket the verifier in `set +e` … `set -e`, or capture `rc=$?` immediately.
- **Runtime install in the verifier** (`test-runtime-install`, WARN — Reflection-schema
  tasks only) — `tests/test.sh` runs `apt-get`/`pip install`/`npm ci`/`curl … | sh` at
  verify time. Verifier deps must be baked into the (verifier) image, not pulled from the
  network during grading.
- **Non-standard reward path** (`reward-path-nonstandard`, WARN — Reflection-schema tasks
  only) — `test.sh` writes the reward to a literal path other than the Harbor-standard
  `/logs/verifier/reward.txt` (which must hold `1` for success / `0` for failure).
- **Pre-created reward** (`reward-pre-created`, WARN — universal) — a `reward.txt` is
  shipped in `environment/` or baked by the Dockerfile, so the task starts already
  "passed." The reward must be written by the verifier at run time.
These are candidates; a no-op run confirms them. (Subtle gameable logic that only
fires at runtime is *not* statically decidable — that's the delivery-stage gate.)

**Security content scan** (`check_security.py`, area `anti_cheat`): the statically-
decidable half of Reflection's "Security and anti-cheat" tab, over the **agent-visible**
files only (`instruction.md` + `environment/` — not `tests/`/`solution/`): **prompt-injection**
(text telling the agent to ignore the task, reveal the answer/secret, skip the tests, or
tamper with evaluation), **hidden-unicode** (zero-width / bidi-control / BOM characters
that hide or reorder text from a reviewer), **obfuscated-payload** (base64/hex piped into
a shell, `eval`/`exec` of decoded data, `curl|sh`, or a long encoded blob). All WARN —
review prompts, not proofs (a task may legitimately be *about* injection). Answer/solution
leakage and reward tampering stay in `check_leakage.py` / `check_reward_hack.py`.

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

  **Degenerate integrity guards** (`degenerate-integrity-guard`, WARN): a shell
  `sha256sum -c` / `md5sum -c` / `cmp` against an **in-image baked reference**
  (e.g. a `.sha256` or `.orig` backup) is *not* a real recompute defense when the
  agent runs as **root** (no `USER` drop) — the agent overwrites both the file and
  its reference. When this is the only "recompute" signal, it is dropped (the
  verifier reads as `verifier-undefended`). It counts as a real defense only if the
  reference lives under `tests/` (verify-time mount) or is a literal in
  `test_outputs.py`. (Verify-time scratch under `/tmp` is excluded — not baked.)

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

The **reviewer** sub-agent — the single canonical semantic rubric. (For where it
sits in the pipeline, see [How the parts fit together](#how-the-parts-fit-together).)
It does **two jobs**, both driven by the [one prompt below](#ready-to-run-reviewer-sub-agent-prompt):
the **semantic deep-dive** — the six checks in this section — and
**false-positive verification** of Part 1's static flags for this task. ("Semantic
deep-dive" is just the name for these six checks; it is not a separate pass.)

It reads `instruction.md`, `tests/` (`test.sh` + `test_outputs.py`),
`solution/solve.sh`, and `environment/Dockerfile` + setup scripts, then judges the
six checks below. **Grep the environment for a value before calling a test
"phantom," and read the *whole* test file before flagging a single assertion** —
miscalibration here is the main failure mode.

### Check 1 — Instruction ↔ verifier alignment (bidirectional)

Every hard requirement in the instruction has ≥1 test, AND every test maps to a
requirement that is either stated in the instruction OR discoverable in the
agent-visible environment.

- **Untested requirement** — a hard requirement in the prompt that no test checks.
  The agent could skip it and still score 100%. FAIL (`untested-requirement`).
- **Phantom test** (`phantom-test`, FAIL) — asserts a value/behavior found nowhere
  the agent can see. Example: test expects `version == "2.4.1"` but no file,
  config, or instruction line mentions `2.4.1`. Before flagging: grep
  `environment/`, source, configs, schema, seed data, error strings — if the value
  appears anywhere agent-visible it is *discoverable*, not phantom. **"Agent-visible"
  means COPY'd into the image by `environment/Dockerfile`, not merely present in the
  task tree** — a value found *only* in `solution/` or `tests/` is phantom (those are
  oracle/verify-time-only), and that is the most-missed phantom.
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
- **Undocumented structured output** (`structured-output-undocumented`, WARN→FAIL) —
  the task must produce a structured artifact (JSON / CSV / YAML / config file /
  database rows / API response) and the verifier asserts its **shape**, but the exact
  schema (fields / columns / types / format) is documented **neither** in the
  instruction **nor** in a clearly-referenced spec/sample staged in `environment/`.
  The agent can't know the required structure, so a correct-in-spirit solution fails
  on shape alone. **FAIL** when the verifier pins a structure the agent has no way to
  discover; **WARN** when it's partial/ambiguous. Before flagging: the schema counts
  as documented if it's an example block, a field/column/key list, a named key
  (`"result"`), or derivable from a sample/input the instruction points to — and a
  schema that is purely *verifier-intrinsic* but also discoverable in the env is fine.
  (The static gate raises this as a candidate; confirm against the env + the
  verifier's assertions, or emit `verify-refuted` if the schema is documented/derivable.)

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
instruction hands over the *method* instead of stating *what success looks like* —
against Reflection's "simple, exploration-encouraging" bar. **Triggers:** dictated
function/method signatures, step-by-step algorithm recipes ("1. read X, 2. compute
SHA256, 3. hex-encode…"), exact byte/hex layouts for an artifact the agent must
*produce*, enumerated fix lists, exact bug locations, answer-key tables, or dictated
internal file/module names.

- **The litmus:** *can you write a correct solution that differs meaningfully from
  the prescribed method?* If the prompt pins the algorithm / data structure / helper
  names so tightly there's only one way to write it, it's over-specified.
- **The intrinsic gate (check BEFORE flagging):** specificity is **legitimate** when
  it is (a) **verifier-intrinsic** — a signature the test links / imports, an output
  schema/path the verifier reads, a value the test asserts — or (b) **describing what
  *exists*** in the environment (an input file format, the data already staged). It is
  **over-specification** only when it dictates *how to implement* something the
  verifier never requires. Documenting the **input** format the agent must parse is
  fine; dictating the **output** code the agent must write is not.
- The static gate raises `prescriptive-instruction` (WARN candidate) on spec-sheet
  smells; confirm or refute it here against the litmus + intrinsic gate. Default WARN;
  escalate toward FAIL only when the prescription removes essentially all problem-solving.

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

### Check 6 — Agentic, distractor-free, valid constraints (Reflection)

The semantic-judgment half of Reflection's Quality criteria that no script can
decide. Default these to **WARN** — they are softer judgments than checks 1–4, so
reserve any escalation for clear-cut cases (over-calling here costs precision).

- **Non-agentic** (`non-agentic`, WARN) — the task is solvable by a *single command*,
  a simple transcription, or zero-shot code generation with no exploration,
  debugging, or multi-step terminal work. Reflection requires *meaningful* terminal
  interaction. Litmus: *could a competent dev one-shot this without looking at the
  environment?* If yes → non-agentic. (Don't flag a task that merely *looks* small —
  judge whether it needs real investigation/iteration.)
- **Misleading distractor** (`misleading-distractor`, WARN) — extraneous environment
  content (dead files, decoy configs, red-herring code) that would actively *mislead*
  the agent, **unless** the task is explicitly designed and reviewed as an
  instruction-alignment / distractor task. Incidental unused files are not distractors.
- **Arbitrary constraint** (`arbitrary-constraint`, WARN) — a formatting, precision,
  tool-use, or process constraint that doesn't reflect a real requirement or prevent
  cheating, and looks added *only* to inflate difficulty (e.g. "use exactly 3 spaces",
  "you must use awk", "round to 7 decimals" with no reason). This is the inverse of
  over-specification: over-spec hands over the *solution*; an arbitrary constraint adds
  a *pointless hoop*. A constraint that genuinely prevents a shortcut is valid, not
  arbitrary.
- **Uncalibrated tolerance** (`uncalibrated-tolerance`, WARN) — a numeric tolerance,
  similarity threshold, fuzzy match, or range assertion that isn't justified, so it
  either rejects correct alternative solutions (too tight) or passes wrong ones (too
  loose). Confirm a correct solution lands inside it and a plausible wrong one doesn't.

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
6. **Grep for the file before calling it "missing."** A file a test references is
   often COPY'd into the image by the Dockerfile — confirm it's absent from both the
   build *and* the task tree before flagging a missing input/requirement.
7. **Read the actual grep pattern before calling it fragile.** A source grep that
   already includes the alternatives (e.g. `(-S|--buffer-size)`, `re.search(...)`)
   accepts the valid variants — it is not a brittle exact-match.
8. **Exhaustive-search tasks: a hardcoded set is ground truth.** When the task is
   "find *all* X in a fixed, baked input," a literal list of expected paths/values
   is the correct answer key, not over-constraint (pairs with rule 4).
9. **Map each test fn to a requirement before calling coverage "minimal."** A small
   test count can still cover every requirement — enumerate the mapping first;
   only flag a requirement that genuinely has no corresponding assertion.

### Ready-to-run reviewer sub-agent prompt

This agent does **two jobs at once** — the six checks above, and false-positive
verification of Part 1's flags for the same task (they cost one agent together). The
prompt is deliberately verbose so the sub-agent needs no other context.

**Maintenance note:** keep this prompt detailed. QC sub-agents miscalibrate or
hallucinate when a check is one vague line — concrete bands, examples, and litmus
tests are what hold them on-target. Expand the per-check detail as the skill is
tested on new tasks; do not thin it out.

> Review the single task at `<TASK_DIR>`. Read `instruction.md`, `tests/`,
> `solution/`, and `environment/Dockerfile` + setup scripts. Its Part-1 static QC
> findings: `<STATIC_FINDINGS_JSON>`.
>
> **(A) The six semantic checks (the "semantic deep-dive").** Emit one finding per
> issue and one PASS `*-ok` per clean area. A generic "looks fine" is not an answer —
> cite the file/line behind every verdict.
> 1. **Instruction↔verifier alignment.** Every hard requirement has ≥1 test, and
>    every assertion maps to something stated in the prompt or discoverable in the
>    agent-visible env.
>    - `untested-requirement` (FAIL) — a hard requirement no test checks; the agent
>      could skip it and still score 100%.
>    - `phantom-test` (FAIL) — asserts a value found nowhere agent-visible (e.g.
>      expects `version == "2.4.1"` but nothing mentions `2.4.1`). First grep
>      `environment/`, source, configs, schema, seed data, error strings — if the
>      value appears anywhere agent-visible it is *discoverable*, not phantom.
>    - `brittle-string-match` (FAIL) — asserts *how* the code is built, not *what* it
>      produces. Litmus: *can you write a correct solution this test wrongly fails?*
>      (greps source for `import pandas` so a correct numpy answer fails;
>      exact-string/whitespace match the spec never pinned; `len(os.listdir())==3`
>      when the count was never fixed.)
>    - `weak-assertion` (WARN→FAIL) — too permissive; lets a wrong solution pass
>      (asserts a substring but ignores exit code; checks a value's *format* not its
>      value; `os.path.exists(out)` when the file's *contents* are the deliverable).
>      FAIL when it lets a wrong solution pass an essential requirement.
>    - `structured-output-undocumented` (WARN→FAIL) — the task must produce a structured
>      output (JSON/CSV/YAML/config/DB rows/API response) and the verifier asserts its
>      shape, but the exact schema is documented neither in the instruction nor a
>      referenced spec/sample in `environment/`. FAIL when the verifier pins a structure
>      the agent can't discover. Don't flag if the schema is shown (example block,
>      field/column list, named key like `"result"`) or derivable from a sample/input
>      the instruction points to. (A static candidate may be attached — confirm or refute.)
> 2. **Comprehensive coverage.** Every requirement is tested on *both* the
>    correctness route (right answer?) and the optimal-solution route (the required
>    algorithm / perf bound / API?). Flag a stated O(n log n), latency, or memory
>    bound no test exercises. `flaky-test` (WARN→FAIL) — pass/fail varies for the
>    *same correct solution* (wall-clock margins like "<0.5s", network, unseeded RNG,
>    set/dict ordering, races). Over-constraining an incidental helper name /
>    intermediate value / log string → `brittle-string-match`.
> 3. **Hygiene.** `spelling-grammar` (WARN) — typos/grammar/markdown/LaTeX in
>    `instruction.md`. `instruction-clarity` (escalate toward FAIL) — two plausible
>    readings the tests only accept one of. `over-specified-instruction` — the prompt
>    dictates the *method* (function signatures, step-by-step algorithm recipes, exact
>    byte/hex layout of an artifact the agent must PRODUCE, enumerated fix lists, exact
>    bug locations) instead of *what success looks like*. Litmus: *could you write a
>    meaningfully different correct solution?* If not, it's over-specified. **Intrinsic
>    gate:** detail required by the verifier (a signature the test links, an output
>    schema/path it reads) or describing an EXISTING input/env format is legitimate —
>    NOT over-spec; only dictating the implementation the agent must author counts.
>    (A static `prescriptive-instruction` candidate may be attached — confirm or refute it.)
> 4. **Golden-patch correctness.** *First name the underlying algorithm/method the
>    task calls for*, then trace `solve.sh` against a canonical solution for that
>    method and through each `test_check_*`. It must implement real logic (no
>    hardcoded outputs, no reading `tests/`, no lookahead) and score 100% on the
>    happy path. `golden-patch-mismatch` (FAIL) — wouldn't actually score 100%
>    (misses a requirement, wrong output shape, relies on something absent at run
>    time). `hardcoded-solution` (FAIL) — emits the expected answer literally / reads
>    it from `tests/`.
> 5. **Realism** (`task-realism`). Does the instruction describe a workflow a real
>    engineer would plausibly be assigned (fix a failing test, implement an endpoint,
>    debug a perf regression, parse logs, migrate a config, repair a build)? Judge
>    plausibility of the *workflow*, not size — a small self-contained repro is fine.
>    - **PASS** — a senior engineer would recognize it as assignable; a minimal repro
>      of a real bug class counts.
>    - **WARN** — plausible domain but artificial framing a real ticket wouldn't have:
>      pervasive `foo`/`bar`/`do_thing_1` naming with no domain grounding, a contrived
>      backstory, or thresholds/constants picked to make a test pass ("benchmark
>      smell").
>    - **FAIL** — no real-world analog (an invented puzzle/cipher with no motivation,
>      unless the category is explicitly puzzles), a workflow no dev would do
>      ("hand-edit this binary at offset 0x1F"), or an internally implausible scenario
>      (stated motivation contradicts the work; references tools/services that can't
>      coexist).
>    - **Do not hallucinate unrealism** — don't penalize a task for being small,
>      synthetic-by-necessity, or lacking a narrative. Reserve FAIL for genuinely
>      contrived, not merely concise.
> 6. **Agentic / valid-constraints** (all WARN — softer judgments; don't over-call).
>    - `non-agentic` — solvable by a single command / transcription / zero-shot codegen
>      with no exploration or multi-step work. Litmus: *could a dev one-shot this without
>      reading the env?* (Judge whether it needs real investigation, not whether it looks
>      small.)
>    - `misleading-distractor` — extraneous env content that would actively mislead the
>      agent, UNLESS the task is explicitly a reviewed instruction-alignment/distractor
>      task. Incidental unused files don't count.
>    - `arbitrary-constraint` — a formatting/precision/tool/process constraint with no
>      real purpose and no anti-cheat value, added only to inflate difficulty ("use
>      exactly 3 spaces", "must use awk"). The inverse of over-spec. A constraint that
>      genuinely blocks a shortcut is valid.
>    - `uncalibrated-tolerance` — a numeric tolerance / fuzzy-match / range assertion
>      that isn't justified, so it rejects correct alternatives (too tight) or passes
>      wrong ones (too loose). Confirm a correct solution lands inside and a wrong one
>      doesn't.
>
> Before flagging anything in (A), apply the **false-positive rules**: an
> anti-shortcut grep *alongside* an outcome test is PASS not WARN; discoverable
> values aren't phantom (and "discoverable" = COPY'd into the image, NOT merely
> present in `solution/`/`tests/` — a value only in those is phantom); instruction-
> referenced inputs aren't leaks; deterministic baked ground truth is fine, and a
> hardcoded set is the answer key for a find-*all* task; the one genuinely-canonical
> approach isn't over-constraining; grep for a file before calling it "missing"
> (it may be COPY'd in); read the actual grep pattern before calling it fragile (it
> may already include the alternatives); and map each test fn to a requirement
> before calling coverage "minimal."
>
> **These three questions are MANDATORY — answer each explicitly, with evidence,
> before you conclude. They target the defect classes reading-based review misses
> most often; a generic "looks fine" is not an answer.**
> - **Q1 (weak verifier / false-accept).** *Write the laziest submission that still
>   passes.* Trace the grading concretely: would a no-op, empty file, all-zeros, or
>   constant output satisfy every assertion? Watch for ratios/normalizations that
>   collapse to a constant, substring/length/existence-only checks, and value
>   comparisons with a degenerate solution. If a lazy output passes an essential
>   requirement → `weak-assertion` FAIL.
> - **Q2 (broken oracle / false-reject).** *Does the reference `solve.sh` pass every
>   test?* Name the algorithm, then trace the reference through each `test_check_*`.
>   Watch for patches/`str.replace` that silently no-op (pattern doesn't match),
>   missing deps, wrong output paths, or whitespace/format mismatches. If the shipped
>   reference would fail its own tests → `golden-patch-mismatch` FAIL.
> - **Q3 (instruction↔test mismatch).** *Does every value/string the tests assert
>   appear in the instruction or an agent-visible file?* Cross-check enum/reason
>   strings, magic numbers, and expected outputs against the prompt AND any sample
>   the instruction tells the agent to study. A test that demands a value contradicting
>   the agent's only spec → `brittle-string-match` / `untested-requirement` FAIL.
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

A **separate** sub-agent per task with the opposite stance from Part 2. It **is the
eval model attempting this task** — it starts from exactly what the real agent sees
(`instruction.md` + `environment/`), is told it will be scored *solely* by `tests/`,
and its goal is to make the verifier report success while doing **as little of the
intended work as possible — ideally none.** It enumerates concrete hacks and decides
whether any would actually pass. A viable hack is a defect in the *task* (the
verifier is gameable), reported as `semantic-cheat-vector` — a **WARN candidate**,
not an auto-FAIL (see the calibration note below).

This agent **attacks the task, not the QC output.** It does not read or critique the
static findings or the reviewer's verdicts — reviewing/refuting Part 1's flags is the
*reviewer's* job (Part 2 job B). Part 3's only objective is to beat the verifier the
way a reward-hacking eval model would.

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
- **Intercept via PATH / fake wrapper** — prepend a dir to `PATH`, or drop a fake
  binary/wrapper, so a tool the verifier shells out to (the program under test, a
  checker, `python`) is replaced with one that returns the expected result; or
  monkey-patch a library the verifier imports. (Reflection's named shortcuts:
  replacing tools, fake wrappers, PATH interception, modifying the test harness.)
- **Overwrite an in-image grader/reference** — if a grading script (`verify.py`,
  `grader.sh`) or an integrity reference (`.orig` backup, `.sha256`) lives in
  agent-writable space and the verifier invokes/compares against it, replace it with
  one that always passes (pairs with Part 1's `agent-writable-verifier` /
  `degenerate-integrity-guard`). Only viable when the agent is root and the file is
  NOT under `tests/`.
- **Echo leaked ground truth** — read the answer from any agent-visible location (a
  baked answer file, a generator script) and print it.

If a viable hack exists → `semantic-cheat-vector` **WARN (a candidate, not a verdict)**
with the **exact recipe** and which assertion it beats. If the verifier resists every
attempt (outcome-based, hidden/held-out inputs, no agent-writable signal) → emit one
PASS `cheat-vector-ok`.

> **Calibration — confirm before you fail.** On the 50-task
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

> You are the eval model attempting the task at `<TASK_DIR>`, but adversarial: you
> will be scored **only** by `tests/`, so your goal is to make the verifier report
> **success while doing as little of the intended work as possible — ideally none.**
> You start from exactly what the real agent sees — `instruction.md` and
> `environment/`. Because this is a read-only red-team, you may **also** read the
> verifier (`tests/test.sh`, `tests/test_outputs.py`) to find what is gameable
> (a real eval run wouldn't see them; whether a hack actually fires is confirmed
> later at the behavioral stage). Do **not** critique the task or review any QC
> findings — your only objective is to beat the verifier.
>
> Work through the hack classes: hardcode checked literals, write the inspected
> artifact directly, stub the called surface, exploit weak assertions, detect the
> harness and branch, tamper with the scoring path, intercept a tool the verifier
> calls via PATH / a fake wrapper / a monkey-patched library, overwrite any in-image
> grader/reference the verifier invokes (when you are root and it is not under
> `tests/`), echo any leaked ground truth. For each, decide concretely whether it
> would PASS `tests/`.
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
> precision — on eval the undisciplined version flagged 49/50 tasks.)
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

Decontamination + the instruction near-dup are **general** (always useful). The
**diversity distribution** and the **0.90 cross-artifact (solve.sh/test_outputs.py)
near-dup** are **Reflection-delivery opt-in** — `decontaminate.py --reflection` and
`check_diversity.py`, run only when prepping a Reflection delivery, not part of default OTS QC.

- **Decontamination** (`decontaminate.py`) — compare each instruction to the
  public-benchmark corpus (`data/decontam_corpus.jsonl`: Terminal-Bench + SWE-bench +
  LiveCodeBench + Aider, the four NVIDIA names) by similarity; high similarity ⇒ possible
  contamination / trivially searchable. Rebuild with
  `skills/static-semantic-qc/scripts/build_decontam_corpus.py`.
- **Near-duplicate / template reuse** (`decontaminate.py`) — high pairwise similarity
  *within* a delivery ⇒ low diversity. Reflection requires pairwise cosine **< 0.90
  (all-MiniLM-L6-v2)** across **three** artifacts, so all three are checked:
  `near-duplicate-in-set` (instruction.md, at the tuned 0.6 sensitivity),
  `near-duplicate-solve` (solve.sh), `near-duplicate-test` (test_outputs.py) — the last
  two at Reflection's 0.90 bar. Use `--method embed` for the embedding cosine
  methodology NVIDIA/Reflection specify.
- **Diversity distribution** (`check_diversity.py`) — Reflection's dataset-level
  taxonomy constraints, checked once over a whole delivery and attributed to the
  synthetic `__dataset__` row (plus a `diversity-report.md`): no category >20% of tasks
  (`category-over-represented`) and none <5% (`category-under-represented`); no
  subcategory >20% (`subcategory-over-represented`); each assigned `task_objective` label
  ≥10% coverage (`task-objective-under-represented`) and each `artifact_type` label ≥5%
  (`artifact-type-under-represented`); plus the avg@8 difficulty distribution.
  Under-representation/coverage floors only fire at/above `--min-tasks` (default 20) —
  small samples can't be assessed; over-representation is always reported.

---

## Sub-agent orchestration (Parts 2–3 driver)

After Part 1, run the reviewer (Part 2 → `qc_out/sem_<task>.json`) and adversary
(Part 3 → `qc_out/adv_<task>.json`) once per task. Two ways: **programmatically via
the Anthropic API** — `skills/static-semantic-qc/scripts/judge.py`, which uses a
**Claude API key** (`ANTHROPIC_API_KEY` / `ANT_KEY`, *not* your Claude.ai account)
and scales to a large set; or **interactively**, fanning out one sub-agent per task
from a Claude Code session (independent tasks → embarrassingly parallel). Then re-run
`aggregate.py`, which auto-drops the false positives the reviewer refuted (precision)
and folds in every new semantic and cheat-vector finding (recall).

### Verification output convention (consumed by `aggregate.py`)

The reviewer emits one meta finding per static flag it reviewed:
- refute a false positive: `{"task","area","title":"verify-refuted","ref":"<static-title>","severity":"PASS","detail":"why it's a FP"}` → that static finding is dropped from the verdict.
- confirm a real one: `{"task","area","title":"verify-confirm","ref":"<static-title>","severity":"PASS","detail":"evidence"}` → informational; verdict unchanged.

The adversary emits `semantic-cheat-vector` (a **WARN candidate**, never FAIL)
or `cheat-vector-ok` (PASS). `aggregate.py` reconciles each cheat-vector rather than
taking it at face value:
- if the task has a `verifier-defended` finding (`check_verifier_defenses.py` proved a
  mutated-rerun / recompute / source-grep / re-exec defense), the cheat-vector is
  **dropped** — the verifier mechanically resists it, no agent in the loop;
- a confirmation step (a skeptic sub-agent or the delivery behavioral run) emits
  `cheat-vector-confirmed` → the cheat-vector is **promoted to FAIL**, or
  `cheat-vector-refuted` → it is **dropped**;
- otherwise (unreviewed) it stays a **WARN candidate**.

The `verify-*` and `cheat-vector-confirmed`/`-refuted` metas are themselves never rolled
into a verdict — they only adjudicate other findings.

## Out of scope: behavioral

The runtime **oracle/no-op** gate (reference solution → pass, untouched container
→ fail) is out of scope for *this* (Layer 1) skill — it is its own **Layer 3**
skill, [`behavioral-qc`](skills/behavioral-qc/SKILL.md), and at the delivery stage
runs on the client's target infra. It emits the same schema
(`{task, area:"behavioral", severity, title, ..., "layer":"behavioral"}`), so its
findings aggregate into the same SSOT and a behavioral `FAIL` is sticky over a
Layer-1 `PASS` (see the repo `README.md` defect gate).

---

## Stable defect-class titles (for the distribution report)

Use these exact titles so the histogram groups cleanly. Sub-agents (Parts 2–3) must
emit from this set; the deterministic gates emit these plus a few granular per-field
siblings (e.g. `junior-time-out-of-range`, `nonpositive-expert-time`,
`memory-above-client-cap`), which the distribution report counts as they appear.

`missing-required-file`, `missing-difficulty`, `missing-category`, `missing-tags`,
`missing-expert-time`, `missing-junior-time`, `missing-verifier-timeout`,
`missing-agent-timeout`, `generic-category`, `broad-tags-only`, `some-broad-tags`,
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
`verifier-defended`, `verifier-undefended`, `agent-writable-verifier`,
`degenerate-integrity-guard`, `test-sh-set-e-reward-abort`,
`missing-subcategory`, `missing-task-objective`, `unknown-task-objective`,
`missing-artifact-type`, `unknown-artifact-type`, `avg-at-8-too-easy`,
`missing-avg-at-8`, `missing-model-tested`, `model-tested-not-approved`,
`missing-agent-tested`, `agent-tested-not-approved`, `missing-build-timeout`,
`nonpositive-build-timeout`, `placeholder-zero-resource`,
`base-image-not-digest-pinned`, `base-image-not-approved`, `apt-not-consolidated`,
`apt-get-upgrade`, `missing-multistage-build`, `broad-chmod`,
`dockerfile-heredoc-source`, `archive-fixture-not-extracted`, `missing-dockerignore`,
`instruction-too-long`, `instruction-relative-path`, `prescriptive-instruction`,
`structured-output-undocumented`, `solve-embedded-heredoc`, `solve-too-long`,
`source-match-verification`, `verifier-unbounded-call`, `verifier-exceeds-timeout`,
`verifier-near-timeout`, `test-runtime-install`,
`reward-path-nonstandard`, `reward-pre-created`, `task-name-not-kebab`,
`task-name-too-long`, `unnecessary-files`, `crlf-line-endings`, `non-text-asset`,
`prompt-injection`, `hidden-unicode`, `obfuscated-payload`, `near-duplicate-solve`,
`near-duplicate-test`, `category-over-represented`, `category-under-represented`,
`subcategory-over-represented`, `task-objective-under-represented`,
`artifact-type-under-represented`, `non-agentic`, `misleading-distractor`,
`arbitrary-constraint`, `uncalibrated-tolerance`, `difficulty-too-easy`,
`avg-at-8-mismatch`. Append `*-ok`
(e.g. `tests-ok`) for clean PASS findings.
