# Overview

This document describes the quality-control standard every terminal task meets before delivery, and the checks we run to enforce it. Each task is authored in the Harbor format and contains the following components:

* `instruction.md`: the prompt shown to the agent
* `task.toml`: the configuration and metadata file
* `environment/`: the sandboxed runtime the agent sees, including the Dockerfile and any assets, source files, data, scripts, services, or configuration needed to solve the task
* `tests/`: the verifier that grades the final state, with `test.sh` as the entry point and any deterministic test files or fixtures required for grading
* `solution/`: the Oracle reference solution, with `solve.sh`

A task ships when it meets two sets of criteria:

* **Quality**: the task-level checks described below
* **Difficulty**: it is hard enough, measured with frontier models (see [Difficulty](#difficulty))

---

# Quality

This section outlines the task-level quality requirements. Every submitted task is expected to meet every applicable criterion. Failure on any required criterion sends the task back for rework or replacement.

These are the four main ways we check each criterion, named alongside it below:

* **static**: automated checks that read the task files (ten checks covering structure and hygiene, metadata, Dockerfile reproducibility, instructions, leakage, reward-hacks, environment fairness, portability, verifier defenses, and security), plus a check for overlap with public benchmarks
* **semantic review**: an LLM reads the task and judges whether the instruction, tests, and solution line up, whether coverage is complete, whether it is realistic, and whether any test is weak or too strict
* **trajectory audit**: after the tasks have been run, we re-check real attempts to confirm the verifier is fair, meaning it does not fail correct work or pass bad work
* **behavioral run**: we build the task and run its verifier to confirm it works. A container where the agent did nothing should score 0, the reference solution should score 1, and a fake reward planted by the agent should still score 0

## General

The following criteria apply to all components:

* **Harbor-compliant**: follows the expected structure (`instruction.md`, `task.toml`, `environment/`, `solution/solve.sh`, `tests/test.sh`).
* **Well-formed**: files are syntactically valid and free of shell, TOML, Python, Dockerfile, and markdown errors.
* **Unix-compatible files**: no Windows line endings unless the task targets Windows containers.
* **Text-only assets**: instructions and assets are text-only, or parsable by tools so a non-multimodal model can read them.
* **Original**: not copied or closely adapted from public benchmark tasks. Each instruction is scored against a public-benchmark corpus (Terminal-Bench, SWE-bench Verified, LiveCodeBench, Aider polyglot) by cosine similarity.
* **Realistic**: represents a plausible terminal-based engineering, data, infrastructure, debugging, or operations workflow, not a contrived puzzle.
* **Agentic**: requires meaningful terminal interaction, exploration, debugging, or multi-step reasoning, and is not solvable by a single command or zero-shot generation.
* **Solvable**: a correct solution is achievable within the configured time and resource limits.
* **Verifiable**: graded by deterministic, programmatic Python tests on the final state, with no subjective or rubric-based grading.
* **Outcome-based**: tests verify the final state or observable behavior, not the specific command sequence the agent used.
* **Deterministic**: re-running the task, Oracle, no-op, and verifier under the same configuration gives the same outcome.
* **Secure**: no malicious code, credential exfiltration, prompt injection, host escape, destructive operations, or obfuscated payloads.
* **Anti-cheat robust**: does not expose answers, verifier logic, test data, or solution files, and cannot be completed by shortcutting the intended work.
* **Complete**: contains all required components.

## Task package

The following criteria apply to the package as a whole:

* **Correct file layout**: all required Harbor files and directories are present.
* **Valid configuration**: `task.toml` is valid TOML and conforms to the schema.
* **Correct task identity**: the name is meaningful, specific, concise, and lowercase kebab-case, and does not collide with another delivered task.
* **Correct resource configuration**: CPU, memory, storage, GPU, build, agent, and verifier limits are present, sufficient, and within caps.
* **Network policy specified**: network access is off by default; an internet flag that contradicts the instruction is flagged.
* **Buildable image**: the environment image builds from a clean checkout.
* **Pinned dependencies**: base images are digest-pinned and `pip` is pinned where the ecosystem supports it.
* **No runtime dependency installation in tests**: `test.sh` does not run live network installs.
* **No stale data**: behavior does not depend on wall-clock time, dates, live web content, mutable package indexes, or external services.
* **No hidden state**: no reliance on prior-run state, local caches, undeclared host files, hidden credentials, or developer-specific configuration.
* **No information leakage**: the agent image does not include `tests/`, `solution/`, ground-truth data, expected outputs, or hidden answer files.
* **No unnecessary files**: no stale artifacts, logs, caches, local virtualenvs, editor metadata, or VCS files.

## Instructions

The following criteria apply to `instruction.md`:

* **Clear objective**: states the goal directly and unambiguously.
* **Concise**: includes only what is needed, with no backstory, roleplay, or filler, and stays under about 1,500 tokens.
* **Realistic phrasing**: representative of how people prompt agents in real workflows, encouraging exploration, and not over-specified or prescriptive.
* **Absolute paths**: every path the agent must read, modify, or create is absolute.
* **No hidden requirements**: required output files, schemas, paths, ports, credentials, and constraints are stated in the instruction or a referenced environment file.
* **No solution hints**: describes what to achieve, not the algorithm or commands, unless those are intrinsic to the task.
* **Valid constraints**: constraints reflect real requirements or prevent cheating, and are not added solely to raise difficulty.
* **Structured outputs specified**: any JSON, CSV, database rows, or config output has its exact schema documented.
* **Environment description accurate**: described files, services, ports, commands, and data match the actual starting state, and nothing is claimed that the environment does not provide.
* **No subjective goals**: no "make this better" goals without objective acceptance criteria.

## Environment

The following criteria apply to `environment/` and the agent runtime image:

* **Necessary and correct initial state**: contains the files, services, and data the agent must interact with, in the state the instruction and tests assume.
* **Realistic**: resembles a plausible terminal workspace, codebase, data directory, or operational setup.
* **All required assets included**: every data file, script, source file, config, fixture, binary, or service definition needed for completion is present and accessible.
* **Dependencies available**: required dependencies are installed in the image or provided deterministically, for example vendored wheels.
* **No solution leakage**: no reference solution, expected outputs, verifier internals, or test-only fixtures in the agent image.
* **No solution-only dependencies in agent image**: the agent image does not reveal the solution path through Oracle-only dependencies.
* **Stable services**: any required service starts reliably and exposes its expected ports.
* **No live external dependency**: no live internet, mutable APIs, or third-party services unless approved per task.
* **Appropriate permissions**: ownership and permissions let the agent complete the task without exposing verifier-only materials.
* **Clean build behavior**: reproducible Dockerfile, consolidated layers, cleaned caches, narrow `COPY`, no unrelated local files.
* **Appropriate compute requirements**: hard because of reasoning or domain complexity, not because of excessive CPU, memory, storage, or runtime.
* **No brittle time dependence**: no task-critical data generated from the current timestamp, nondeterministic ordering, or unfixed random seeds.

## Solution

The following criteria apply to `solution/` and the Oracle reference solution:

* **Present and executable**: contains an executable `solve.sh`.
* **Correct**: the Oracle completes the task from a clean environment and produces a passing reward. A failing Oracle is a broken-oracle defect and blocks delivery.
* **Clear**: readable and organized enough for a reviewer to follow the intended approach.
* **Non-trivial**: demonstrates that the task requires meaningful work, and does not reduce to a one-liner or a copy from the instruction.
* **Not hardcoded**: derives outputs through real computation or environment interaction, and does not write the final answer directly.
* **No privileged knowledge**: relies only on information available to a real agent, with no hidden verifier data or expected outputs.
* **Consistent with instructions**: every action is allowed by and relevant to the instruction.
* **Consistent with tests**: satisfies all verifier checks without calling, modifying, or depending on the verifier.
* **No verifier dependency**: does not read from `tests/`, call test scripts, inspect expected outputs, or depend on verification-time files.
* **No unnecessary actions**: no unrelated edits, broad cleanup, environment tampering, or network calls.
* **Appropriate decomposition**: long or complex logic lives in separate files rather than large heredocs in `solve.sh`.
* **Deterministic**: re-running in a clean environment gives the same passing final state.

## Tests and verifier

The following criteria apply to `tests/`, verifier logic, and reward generation:

* **Present and executable**: contains an executable `test.sh` and a `test_outputs` file.
* **Reward file generated**: the verifier always writes `/logs/verifier/reward.txt`, with 1 for success and 0 for failure.
* **Reward not pre-created**: the reward file is not in the initial environment and is not written before verification runs.
* **Graceful failure**: on test failure, missing prerequisites, or a no-op agent, the verifier writes a failing reward instead of crashing without one.
* **Deterministic**: deterministic inputs, ordering, seeds, and assertions, with no unseeded randomness or live-generated test data.
* **Fast enough**: efficient, with timeouts on subprocesses and service calls, and within the configured verifier timeout.
* **Functional verification**: verifies behavior by executing code, checking outputs, querying services, or inspecting final state, not by matching source keywords or brittle regex.
* **Complete coverage**: covers the required behavior, including edge cases, failure modes, boundary conditions, and output schemas.
* **Granular**: assertions are specific to individual behaviors rather than overly broad.
* **Readable**: no encoded commands or opaque file hashes; the flow is reviewable.
* **No extra requirements**: does not assert anything absent from the instruction or referenced environment docs.
* **No missing requirements**: every material requirement in the instruction is tested.
* **Correct expected results**: each assertion maps inputs to outputs correctly, and expected values are derived or reviewable.
* **Materialized fixtures**: fixtures are committed or baked into the verifier image, and any generated fixtures are deterministic.
* **Independent from Oracle**: does not call the Oracle, import solution code, or depend on solution-side artifacts.
* **Independent from agent tampering**: the agent cannot modify the verifier, expected outputs, or scoring logic; files used to compute ground truth are re-copied into the environment at verification time.
* **No runtime installs**: `test.sh` does not run `apt-get install`, `pip install`, `curl | sh`, or equivalent.
* **Tolerances justified**: any numeric tolerance, similarity threshold, or fuzzy match is calibrated so correct alternatives pass and incorrect solutions fail.
* **No subjective grading**: no human preference, vague quality judgment, or LLM-as-a-judge inside the verifier.
* **Anti-cheat resistant**: resists monkey-patching libraries, replacing tools, fake wrappers, modifying test harnesses, caching answers, or exploiting accessible ground truth.

## Correctness validation

The following criteria are confirmed by the behavioral run and the difficulty calibration before submission:

* **Image builds cleanly** from a clean checkout.
* **Oracle passes**: the Oracle completes the task and produces reward 1.
* **No-op fails**: a no-op agent leaves the task incomplete and produces reward 0.
* **Repeated Oracle deterministic**: the Oracle passes consistently across repeated runs.
* **Repeated no-op deterministic**: the no-op agent fails consistently across repeated runs.
* **Verifier deterministic**: the same final state produces the same reward.
* **No reward-file errors**: the verifier never fails with a missing reward file.
* **Clean-run validated**: validation is from a clean environment, not a modified or previously solved workspace.
* **No hidden dependency validated**: confirmed not to rely on undeclared local files, caches, env vars, credentials, or internet.
* **Benchmarked with agents**: benchmarked against the required model and agent with the required number of attempts.
* **Pass rates recorded**: the measured pass rate is recorded in `task.toml`.

## Component alignment

The following criteria are checked by the semantic reviewer, which traces each requirement across components:

* **Instruction-test alignment**: every tested behavior is stated in the instruction, and every material requirement is tested.
* **Instruction-solution alignment**: the Oracle implements the instruction without unstated requirements or hidden knowledge.
* **Instruction-environment alignment**: files, services, commands, paths, and starting conditions match the actual environment.
* **Environment-test alignment**: tests inspect the correct files, services, ports, and final-state artifacts.
* **Environment-solution alignment**: the Oracle interacts with files, commands, services, and paths that exist in the environment.
* **Solution-test alignment**: the Oracle satisfies all tests, and its nontrivial behavior corresponds to a checked state.
* **Metadata-component alignment**: category, tags, time estimates, and resource limits match the actual task.
* **No contradictory requirements**: no component contradicts another about paths, schemas, outputs, limits, or constraints.
* **No orphaned behavior**: no unused assets, untested requirements, stale metadata, or verifier checks with no matching instruction.
* **Traceable requirements**: each requirement can be traced from instruction to solution behavior to verifier assertion.

## Metadata

The following criteria apply to `task.toml` metadata:

* **Accurate metadata labels**: category, subcategory, tags, and difficulty are the best available fit and not over-broad.
* **Expert time estimate present**: a plausible estimate of how long a domain expert would take is recorded.
* **Resource rationale accurate**: timeout and resource settings are consistent with the expected runtime.
* **Benchmark results accurate**: the recorded pass rate corresponds to actual runs, and the recorded `avg_at_8` agrees with the rollouts.
* **No stale claims**: no outdated performance claims, inaccurate descriptions, or references to files that no longer exist.
* **No default placeholders**: template defaults, empty strings, and placeholder categories or explanations are replaced before submission.

## Security and anti-cheat

The following criteria apply to all files and runtime behavior:

* **No malicious behavior**: no credential theft, exfiltration, host escape, denial of service, destructive operations, or cross-task interference.
* **No prompt injection**: environment files, comments, READMEs, logs, and fixtures do not tell the agent to ignore rules, reveal secrets, or tamper with evaluation.
* **No obfuscation**: no obfuscated commands, hidden Unicode, encoded payloads, misleading filenames, or dynamic `eval` of decoded data.
* **No exposed ground truth**: answers, expected outputs, hidden fixtures, and scoring code are not visible to the agent.
* **No mutable verifier**: the agent cannot alter verifier code, expected results, reward files, or test-only dependencies.
* **No answer in image layers**: image history, copied files, caches, and build layers do not reveal the solution.
* **No trivial bypasses**: prevents writing reward files directly, replacing tools under test, short-circuiting tests, PATH interception, and fake logs. Concrete vectors we screen for include agent-writable verifiers, plantable `conftest.py`, and `__pycache__` shadowing.
* **Agent traces reviewed**: representative rollouts are reviewed for cheating, shortcutting, and verifier tampering.

---

# Difficulty

This section describes the difficulty requirement and how we measure it. These are the GDM (Google DeepMind) difficulty specs.

## Network policy

Tasks must not use the internet. No live internet, mutable APIs, or third-party network services are permitted during task execution or evaluation. Network access is off, and any task requiring it is rejected.

## Criteria

To determine difficulty, each task is evaluated 8 times per model so the pass rate can be measured (the proportion of attempts the model answers correctly). Difficulty requirements are distribution-based across the delivered set — every task is measured, and the delivered set as a whole must satisfy the following bands.

**Gemini 3.5 Flash (target model)** — the primary difficulty gate:

* 0 of 8 passing → **≥70%** of delivered tasks
* 1–2 of 8 passing → **≤30%** of delivered tasks
* 3 or more of 8 passing → **0%** (Google will reject any delivered task that passes 3+ times out of 8)

**Competitor models (GPT-5.5 or Claude Opus 4.8, or their respective latest)** — solvability / not-too-hard gate:

* 0–4 of 8 passing → **≥50%** of delivered tasks
* 5–8 of 8 passing → **≤50%** of delivered tasks

## Models and Settings

Gemini 3.5 Flash is the target model against which the primary difficulty distribution is measured. GPT-5.5 and Claude Opus 4.8 (or their respective latest versions) are the competitor models used to confirm that tasks are neither too easy nor trivially solvable, per the competitor bands above.

* Gemini 3.5 Flash (target)
  * Attempts: 8 per task
* Competitor models: GPT-5.5 / Claude Opus 4.8 (or respective latest)
  * Attempts: 8 per task

Given the cost of running the full 8-attempt measurement across models, we suggest a cheaper model be used for an initial difficulty assessment (for example, tasks easily solvable by a small model are unlikely to fall in-band and can be filtered out early). No matter the filtering strategy used, the final difficulty assessment must use the 8-attempt measurement on Gemini 3.5 Flash for the target bands and on the competitor models for the competitor bands.
