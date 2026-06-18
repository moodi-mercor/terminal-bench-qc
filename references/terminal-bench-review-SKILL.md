---
name: terminal-bench-review
description: >-
  Evaluate Terminal-Bench tasks for quality, correctness, and adherence to standards.
  Use when asked to review, QA, or evaluate terminal-bench tasks. Checks instruction↔test
  alignment (no test-only or untested requirements), prefers runtime/outcome validation
  over static string matching, and produces a CSV summary plus detailed markdown findings.
---

# Terminal-Bench Task Quality Review

Evaluate Terminal-Bench tasks in a given folder for quality, correctness, and adherence to standards. Produce two output files: a scannable summary and a detailed findings report.

## Task Structure Expected
Each task directory should contain some or all of:
- `task.toml` — metadata
- `instruction.md` — problem statement
- `Dockerfile` — environment setup
- `test.sh` and/or test files — verification tests
- `solution/` or solution files — reference solution
- `data/` — any supporting data

---

## Evaluation Procedure

For each task found in the provided folder, perform the following checks by dispatching sub-agents. Run sub-agents in parallel where possible.

### Sub-Agent 1: Metadata Review (`task.toml`)

Read `task.toml` and check:

1. **Required fields present** — All of the following must exist (values must be non-empty):
   - `difficulty`
   - `category`
   - `tags`
   - `expert_time_estimate` (or equivalent key like `expert_time`)
   - `junior_time_estimate` (or equivalent key like `junior_time`)
   - `verifier_timeout`
   - `agent_timeout`
   - Environment requirements (e.g., `memory`, `storage`, `cpus` — at least one must be specified)

2. **Category validity** — The category should be a reasonable, specific label that matches the task described in `instruction.md`. Flag if it's overly generic (e.g., just "programming") or seemingly mismatched with the task content.

3. **Tags quality** — Tags should be specific but not absurdly narrow. Flag if:
   - Any tag is overly broad (e.g., "general", "code", "task")
   - There are no tags
   - Tags don't relate to the actual task content

4. **Time estimates sanity** — The `expert_time_estimate_min` and `junior_time_estimate_min` fields are in **minutes** (the `_min` suffix means minutes, not "minimum"). `junior_time_estimate` should be >= `expert_time_estimate`. Both should be positive. Flag if expert time is 0 or if junior time is less than expert time.

   **Time-difficulty alignment (TB2 reference ranges):**
   - `easy`: expert 5-60 min, junior 20-120 min
   - `medium`: expert 5-180 min, junior 10-480 min
   - `hard`: expert 300-480 min, junior 600-19200 min

   Flag as FAIL if time estimates fall outside the range for the declared difficulty. Common anti-pattern: values written as if they were seconds (e.g., expert=1200 for an easy task = 20 hours, when it should be ~10 min). If all values in a batch are 60x too high, they were likely recorded in seconds instead of minutes.

5. **Timeouts sanity** — `verifier_timeout` and `agent_timeout` should be positive integers. `agent_timeout` should generally be >= `verifier_timeout`.

### Sub-Agent 2: Dockerfile Review

Read the `Dockerfile` and cross-reference with the full task directory structure:

1. **No solution leakage** — Scan all `COPY`, `ADD`, and `RUN` commands. Verify that:
   - Solution files (from `solution/` or any file clearly containing a reference solution) are NOT copied into the agent's working environment. Note: in harbor-based tasks, `solution/` and `tests/` are mounted at verification time only — they should never appear in the Dockerfile.
   - Test files (`test.sh`, test directories, unit test files) are NOT copied into the agent's working environment.
   - Also check `data/` directory contents — flag if any file in `data/` appears to contain solutions or test answers.
   - Watch for `COPY data/ /app/` (wildcard copies) — these pull in everything under `data/`, including any subdirectories like `expected_output/` that shouldn't be agent-visible. Prefer explicit `COPY data/logs/ /app/logs/` style copies.

2. **No implementation hints in agent-visible files** — For every file the Dockerfile copies into the container, verify it does not contain:
   - Step-by-step solution guides or numbered "recommended approach" sections
   - Exact field indices, column numbers, or awk/grep one-liners that solve the parsing problem
   - Named entities (specific usernames, IPs, account names) that the agent should discover from the data
   - Exact output format prescriptions beyond what's in `instruction.md`
   - "Common issues" or debugging guides that reveal what mistakes to avoid (and thus what the solution looks like)
   - Any `PREVIOUS ATTEMPTS` or similar sections that reveal the solution approach
   Reference files (e.g., threshold configs, pattern lists) are fine — they define *what* the task needs, not *how* to implement it. The line to flag is when a file describes implementation details an agent should figure out themselves.

   Pay special attention to documentation-style files (markdown, text) copied into the container — these are highest risk for containing complete solution paths disguised as "reference docs". For each such file, verify it contains ONLY policy contracts (what rules apply) and NOT implementation guidance (how to implement them, exact config values to set, step-by-step procedures).

3. **Test dependencies** — **pytest and standard testing frameworks installed in the Dockerfile are acceptable and should NOT be flagged.** Many tasks legitimately include pytest in the image because the task's own CI/test suite (distinct from the harbor verifier) uses it. Only flag test-only dependencies if they are clearly exotic or heavyweight libraries installed solely for the verifier (not the task's own test infrastructure).

4. **Constraint files in `tests/` namespace — verify BOTH conditions before excusing.** Some tasks intentionally place constraint files (e.g., `run_tests.py`, `verify_ota.py`) inside `tests/` or `/app/tests/` in the container. These can be legitimate task constraints, but the "constraint file" exception only applies when **both** of the following are true — verify each by actually grepping, do not infer from naming or convention depedning on task:
   - (a) `instruction.md` explicitly names the file as immutable ("do not modify `<path>`"), AND
   - (b) A harbor test enforces integrity (MD5/SHA256 hash check, ref-file diff like `gateway_ref.py`, or equivalent guard)

   If either condition is missing, the file is **agent-writable and unverified**. Do NOT mark PASS. Proceed to the agent-writable execution path audit (item 5 below) before assigning a verdict. Default verdict when only one condition holds is **FAIL on Anti-Cheat**, because the integrity gap is exploitable; the cosmetic-naming framing does not apply.

5. **Agent-writable execution paths — mandatory cheat-trace for every COPY'd file the test suite executes or reads.** For every file the Dockerfile copies into agent-writable space (anywhere not root-owned 0555 or under a path the agent demonstrably cannot write), if the harbor tests **invoke** that file (directly, or transitively via `make`, shell scripts, services, or imports) or **read its output as ground truth**, perform this trace before assigning a verdict:

   1. **List every harbor assertion** in `tests/test_outputs.py` (or equivalent).
   2. **For each assertion, ask: "If the agent replaces the COPY'd file's contents with the minimum code that satisfies this assertion, does the assertion still pass?"** Common replacement strategies to consider:
      - Print the expected substring/value the test greps for.
      - Write the expected rows/files/state directly (bypassing the real protocol/algorithm/pipeline).
      - Stub timestamps/IDs from `time.time()` / `uuid` to satisfy freshness or uniqueness checks.
      - Move forbidden literals (e.g., `sqlite3`, `subprocess`) into the swapped file if the ban only covers a sibling file.
   3. **Walk all assertions together.** If the union of swap strategies satisfies every assertion without exercising the real task logic, the file is an exploitable cheat vector. Flag **FAIL on Anti-Cheat**.
   4. **Remember reset behavior.** Pre-test hooks (`pre_run.sh`, `test.sh`) may wipe DB state, restart services, or recreate fixtures — but they typically do **not** restore the agent-writable file itself. Verify by reading the hooks; do not assume.

   The load-bearing question is *"if the agent edits this file, what breaks?"* — not *"is this file named like a test?"*. Apply this trace BEFORE any FP-rule exception (FP-8 in particular). FP rules prevent over-flagging; they do not excuse skipping the cheat-trace. Inversion of that order is the most common review failure mode for tasks with agent-side test runners.

6. **General Dockerfile quality** — Flag if:
   - No base image is specified
   - The Dockerfile is empty or trivially minimal for a task that clearly needs dependencies
   - Obvious syntax errors exist

### Sub-Agent 3: Problem Statement Review (`instruction.md`)

Read `instruction.md` thoroughly:

1. **Testable requirements** — Extract every requirement, constraint, and expected behavior mentioned or strongly implied. For each, note whether it appears to be tested (cross-reference with test files). Flag any requirement that has no corresponding test.

2. **Data schemas explicit** — If the task requires writing data to files, databases, or specific formats, the expected schema/format MUST be explicitly defined in the instructions. Flag if the agent would have to guess the schema.

3. **File references — discoverability over explicitness** — File paths, config values, service ports, passwords, and other specific values do NOT need to be spelled out in the instructions if they are **discoverable** from the container filesystem (config files, scripts, logs, error messages, source code, database contents). The agent is expected to explore the codebase. Only flag if a required file/path/value is truly undiscoverable — i.e., it exists nowhere in the agent-visible environment and cannot be inferred from available files. Common discoverable patterns that should NOT be flagged:
   - Config values in `.json`, `.yaml`, `.toml`, `.conf`, `.env` files
   - File paths referenced in scripts, systemd units, or config files
   - Database schemas visible via `sqlite3 .schema`, `\d` in psql, or init SQL files
   - Service ports in config files or startup scripts
   - Passwords/secrets in config files, environment variables, or setup scripts
   - Error conditions reproducible by running the broken service
   - Success/status messages in source code (e.g., `print("Pipeline started successfully")` in `main.py`)
   - Default values in schema files (e.g., `"default": 5432` in a JSON schema)

   **CRITICAL: Before flagging "tests check undocumented values," grep the environment/ directory for the specific value.** A common false positive is flagging test assertions as "not in instructions" when the value exists in a config file, source code, or schema file that the agent can discover by exploring. Run `grep -r "VALUE" environment/` mentally or actually before flagging.

4. **Hard requirements use hard language** — Any behavior that is enforced by a test must be stated as a hard requirement ("must", "must not", "required") in the instructions. Flag if tested behavior is described with soft language like "likely", "should consider", "may want to" — agents may skip optional-sounding rules, causing failures that look like task defects but are actually instruction ambiguity.

5. **Examples enforced as requirements** — Flag if the instructions use example-only wording ("for example", "e.g.", "such as", "like") but tests enforce that exact example as a hard requirement. Either the instruction must state it as a requirement (not an example), or the test must accept other valid approaches. This is a **FAIL** — agents reasonably treat examples as illustrative, not prescriptive.

6. **No classification leakage** — Flag if the instructions list specific expected output values (e.g., `(HIT vs MISS/RefreshHit/Error)` in an enumeration) in a way that accidentally reveals how edge cases should be classified. The instructions should define the rule, not enumerate the answer.

7. **Spelling and grammar** — Flag any obvious spelling errors, grammatical issues, or malformed markdown/LaTeX. Be reasonable — minor style differences are fine, but confusing or incorrect language should be flagged.

8. **Clarity and completeness** — Flag if the problem statement is ambiguous enough that a competent developer would have to guess about major aspects of the task. Note: terse/vague instructions are acceptable when the agent can fill in details by exploring the environment. Only flag if the task goal itself is unclear.

9. **No over-specification (difficulty reduction)** — The instruction should describe WHAT must be true (goals, output schemas, constraints) but NOT HOW to achieve it. Flag any of these patterns that reduce task difficulty:

   - **Enumerated fix lists**: Listing every bug with its exact file, function, or line to fix. The instruction should state observable symptoms and let the agent discover root causes.
   - **Step-by-step recipes**: Numbered procedures walking the agent through exact commands or fixes. State end-state requirements instead.
   - **Exact bug-location references**: Telling the agent precisely which files contain bugs and what to change. The agent should discover buggy files through investigation. Exception: references to INPUT files the agent must read or OUTPUT files the agent must produce are fine.
   - **Enumerated expected values**: Listing every expected decision or output value that the agent should derive from analysis.
   - **Answer-key tables**: Tables mapping specific inputs to their exact expected outputs.

   The instruction should make a competent developer think "I know what success looks like, now I need to investigate how to get there."

10. **Discoverability of requirements** — Every value or behavior that tests check MUST be either (a) stated in the instruction, or (b) discoverable from files present in the Docker container (source code, config files, tool output, existing data, running services, error messages). **Do NOT flag as "undocumented" if the value is discoverable by exploring the environment.** Only flag (as FAIL) if a test checks for a truly phantom value that appears nowhere the agent can find it and cannot be inferred from any available context. When reviewing, actually check the environment/ directory for the value before flagging.

11. **Instruction ↔ test alignment (bidirectional)** — Treat mismatch between `instruction.md` and tests as a first-class failure mode (see also Sub-Agent 4). Flag explicitly when:
    - **Untested requirements** — The instruction states a hard requirement (success criterion, output format, edge case, constraint) but no test exercises it. This is a **FAIL** for instruction coverage unless the requirement is purely exploratory / intentionally not auto-verified (rare — call it out).
    - **Test-only / additional requirements** — A test asserts behavior, thresholds, or outputs that are **not** stated or clearly implied in `instruction.md` **and** are **not** discoverable from the agent-visible environment (anti-cheat guards are OK if documented per TIA #20). This is a **FAIL** (or **WARN** if the fix is only to add one sentence to the instruction — prefer documenting legitimate constraints in `instruction.md` rather than deleting tests).
    - **Contradictory spec** — Instructions say one thing and tests enforce another (different paths, formats, limits). **FAIL** until `instruction.md`, tests, and solution agree.

### Sub-Agent 4: Tests Review

**Critical architectural note:** `tests/` and `solution/` directories are mounted by harbor at **verification time only**. The agent executes in a container built solely from `environment/Dockerfile` — it **cannot read test files or solution files during task execution**. This has important implications for what constitutes a "cheating vector":

- **Hardcoded values in test assertions** (forensic IPs, passwords, TOTP secrets, computed quantities): NOT a FAIL for anti-cheat. The agent cannot read these values. This is a **WARN** test quality concern (the oracle could trivially pass if it read tests, and the test over-constrains to specific values). Mark as WARN, not FAIL.
- **A real FAIL** requires the value to also appear in a file COPY'd into the container by the Dockerfile — i.e., agent-visible at runtime.

Read `test.sh` and all test files:

1. **No phantom tests** — Every test should correspond to a requirement that is either (a) stated or clearly implied in `instruction.md`, OR (b) discoverable from the container environment (config files, source code, logs, scripts, database contents). A test is only a "phantom" if the checked value/behavior cannot be found anywhere the agent can look. Before flagging, verify the value isn't in any environment/ file. Common false positives: config values in .json/.yaml files, paths in scripts, schema in init SQL, passwords in setup files — these are all discoverable and should NOT be flagged.

2. **Full coverage** — Cross-reference with the requirements extracted from `instruction.md`. Every **essential** stated requirement should have at least one test. "Essential" means anything that defines success, required outputs, or must-not behaviors — not optional nice-to-haves. If the instruction lists multiple independent success criteria, each should be covered or explicitly marked as not auto-verified.

3. **Runtime / outcome validation vs static string matching** — Prefer checks that exercise the real behavior of the system under test:
   - **Strong (preferred):** Run binaries or services; assert on HTTP responses, exit codes, command output, database rows, generated files, metrics, or structured data. These validate that the task outcome works end-to-end.
   - **Weak (supplementary):** `grep`, `rg`, or Python reads of **source files** for specific strings, function names, or patterns. Use these only as **anti-shortcut** or **sanity** checks alongside outcome tests — not as the sole proof of correctness.
   - **Flag as WARN** when the **primary** or **only** correctness checks are static substring matches in source (e.g., "solution must contain `foo()`") with no execution-based validation of behavior. **FAIL** only if that pattern hides untested essential requirements (e.g., no test proves the service actually starts or the output file is valid).
   - **Exception:** When the **deliverable is** source code (refactor, add decorator, fix specific line), targeted source checks can be primary — still pair with minimal runtime/import or execution smoke where feasible.
   - **CRITICAL: Before flagging static checks as WARN, verify the FULL test file for runtime tests.** A common false positive is flagging grep-based anti-shortcut guards when runtime/outcome tests exist in the SAME file but weren't noticed. Read the ENTIRE test file and categorize EVERY test function as "runtime" or "static" before concluding that static checks are the only validation. If runtime tests cover the same behavior, the static checks are anti-shortcut guards and should be marked PASS, not WARN.

4. **Test quality** — Check that:
   - Tests have clear names or comments explaining what they verify
   - Tests are reasonably structured (not a single giant test doing everything)
   - Test logic is sound (no obvious bugs in test assertions)
   - Expected values are derived dynamically from the container data rather than hardcoded (WARN if hardcoded — test quality concern)
   - **Exception for static evidence:** If evidence/input data is baked into the Docker image and never changes, hardcoded expected values in tests are deterministic ground truth — mark as PASS, not WARN. The key question is: "Can the expected values change without rebuilding the Docker image?" If no, hardcoding is correct.
   - **Exception for exhaustive search tasks:** When a task requires finding ALL instances of something (e.g., migrate all endpoint references, find all encoded variants), hardcoded path/value sets in tests are ground truth for verification. This is correct test design — the test must know the complete set to verify exhaustiveness. Mark as PASS.

5. **Dependencies pinned** — Any dependencies installed in `test.sh` should have pinned versions (e.g., `pip install pytest==7.4.0`, not just `pip install pytest`). Flag unpinned dependencies.

6. **Test reliability** — Flag if any tests appear flaky (e.g., depend on timing, network calls without mocking, random values without seeds, race conditions).

7. **Oracle-compatible agent-rerun tests** — If tests re-run the agent's solution script (e.g., to verify it's not hardcoded), they must use `pytest.skip()` — not `assert` — when the solution file is absent. The oracle agent runs the reference solution directly without creating a solution script, so a hard `assert` will fail the oracle and produce a false 0.0 reward. Pattern to enforce:
   ```python
   def _find_solution_script():
       candidate = Path("/app/solution.sh")
       if not candidate.exists():
           pytest.skip("solution script not found — skipping re-run test")
       return str(candidate)
   ```

### Sub-Agent 5: Solution Review

Read the reference solution and cross-reference with `instruction.md` and tests:

1. **Correctness** — Does the solution follow the requirements in `instruction.md`? Would it pass all the tests? Flag any inconsistencies between the solution, the problem statement, and the tests.

2. **No hardcoding** — The solution should implement actual logic, not hardcode expected outputs. Flag if:
   - The solution returns literal values that match test expectations without computation
   - The solution reads test files to extract expected answers
   - The solution exhibits lookahead bias (uses information only available from tests)

3. **Reasonable approach** — The solution should represent what a competent developer or agent would write. Flag if the solution uses an unreasonable shortcut that only works because of specific test structure.

### Sub-Agent 6: Anti-Cheat & Global Review

**Key architectural constraint:** In harbor tasks, `tests/` and `solution/` are **never** in the `environment/Dockerfile`. They are mounted at verification time only. The agent's container is built solely from `environment/Dockerfile`. Therefore:
- Hardcoded values in `tests/` files are **NOT** accessible to agents — this is a WARN (test quality), not a FAIL
- Hardcoded values in `solution/` files are **NOT** accessible to agents — this is a WARN (solution quality), not a FAIL
- **FAIL-level anti-cheat concern** = values that are accessible via Dockerfile COPY'd files in the container (e.g., a hint file in `data/`, a wildcard COPY that includes answer-key data)

Review all components holistically:

1. **No cheating vectors** — Verify:
   - Tests are not accessible to the agent at runtime (in harbor tasks, tests are mounted by the verifier at verification time — they must NOT appear in the Dockerfile)
   - Solution is not accessible to the agent at runtime (in harbor tasks, `solution/` is oracle-only — must NOT appear in the Dockerfile)
   - If internet access is permitted (check `task.toml` or Dockerfile), consider whether the task/solution could be trivially found via web search. Flag if this is a well-known problem (e.g., a famous LeetCode problem, a textbook exercise) that an agent could solve by searching.

2. **Agent-visible files audit** — For every file copied into the container by the Dockerfile, check for unintended hints:
   - Step-by-step implementation guides masquerading as "notes" or "analysis" files
   - Exact field/column indices that solve the parsing problem
   - Named internal entities (specific service account names, IP ranges, expected output values) the agent should derive from data
   - Any file named `analysis_notes.txt`, `notes.txt`, `hints.txt`, or similar — these are highest risk for over-hinting and deserve close scrutiny
   - Reference documents are acceptable (thresholds, classification rules, format specs) as long as they define *what*, not *how*

3. **Cross-component consistency** — Verify that `instruction.md`, tests, and solution all agree on:
   - Input/output formats
   - File paths and names
   - Expected behavior and edge cases
   - No **instruction ↔ test drift**: same success criteria in prose and in assertions (see Sub-Agent 3 §10 and Sub-Agent 4 §§2–3)

4. **Environment adequacy** — Do the resource requirements in `task.toml` seem sufficient for the task? Flag if a task clearly needs GPU but none is specified, or if memory seems too low.

5. **Build-time writes to the absolute `/tests/` path** — Separately from `COPY tests/`, scan the Dockerfile and every setup script it runs for writes to the literal `/tests/...` path: `RUN ... > /tests/x`, `cp`/`mv ... /tests/`, `cat <<EOF > /tests/x`, `open('/tests/x','w')`, `os.makedirs('/tests...')`, `echo <b64> | base64 -d > /tests/x`, `COPY x /tests/x`. The verifier mounts source `tests/` at `/tests/` only at verification time, and that mount shadows the image's `/tests/`; so any build-time write to `/tests/` is baked into the agent image and readable at solve time.

   Classify each such write:
   - **FAIL (real leak)** if it survives to the final image layer AND is exploitable: a label/expected/golden file the verifier compares against; a hidden test input; an anti-hardcode generator (e.g. `mutate*.py`, `generate_mutated*.py`, `*_more.py`) the agent can read to defeat the anti-hardcode check; or a verifier script that reveals grading logic, expected literals, or pass thresholds.
   - **Not a leak** if a later build step `rm`s it before the image is finalized — verify by building the image and listing `/tests/`.
   - **Not exploitable (cleanup only)** if it is a smoke test already visible elsewhere in the image, a hash of an unmodified agent-visible file (the agent can compute it itself, so knowing it grants nothing), or a verifier whose only constants are already published in an agent-visible spec. Still relocate it out of `/tests/` (see fix) to avoid the verify-time shadow failure below.
   - **Dead** if no verifier reads it — but check for base64-encoded, variable, or relative-path references before concluding this. Then remove the write.

   **Required even when not exploitable:** if the file is baked into `/tests/` with no committed source copy and no `rm`, the task passes only via the baked copy; once the source `tests/` mount shadows `/tests/`, the file is missing and the verifier errors. Commit it under source `tests/` regardless.

   **Fix:** move the artifact to a committed source path — `tests/.truth/<name>` for labels / hidden inputs / generators, `tests/<name>` for verifier scripts the test invokes. Remove the build-time write and drop `/tests` from any build `mkdir`. Update verifier path references. If a non-test consumer needs the file under `/app/...`, add a dangling symlink (`ln -sf /tests/.truth/<name> /app/...`). Re-extract build-generated content by building the image and copying the exact bytes, or regenerate deterministically. Re-run the oracle and confirm reward 1.0; if it fails, revert.

   **Also watch (broader class):** the same exploit applies to truth/hash/expected files baked into other agent-visible paths (`/root/...`, `/app/*truth*`, `/opt/**/golden*`, `*_hash.txt`). Flag any build step that generates a grading artifact into an agent-readable location.

---

### Leakage QC workflow (batch reviews — methodology)

When auditing many tasks for leakage, follow this order. It scales and avoids the two recurring failure modes: trusting a noisy static flag, and shipping an incomplete fix.

1. **Triage with both detectors, don't hand-grep.** `scripts/leak_detect.py <tasks-dir>` (build-time writes into `/tests/`) and `scripts/leak_detect2.py <tasks-dir>` (truth baked into agent-visible paths outside `/tests/`). They emit per-task buckets to `/tmp/leak_results.json` / `/tmp/leak2_results.json`. Treat the output as a candidate list, never a verdict.
2. **Re-classify every flag independently.** Static flags are noisy — in practice a large fraction are false positives (instruction-promised samples/specs/references, code/generators, build-then-`rm`'d artifacts, hashes of agent-visible files, hidden-from-agent artifacts). Confirm survival (build + `docker run --rm <img> ls <path>`, or trace every `rm`) and exploitability (does the verifier read it as the hidden expected answer, and does the instruction withhold it from the agent?) before changing anything. Never "fix" an intentional reference/sample — it destroys eval signal.
3. **Fix real leaks per the canonical pattern, then oracle round-trip.** Confirm `harbor run -a oracle` (use `-e docker` or `-e modal`) returns reward=1 after each fix. If it fails, revert and downgrade to finding-only.
4. **Re-scan after the batch.** Re-run the detectors against the fixed tree to confirm zero real leaks remain — this catches incomplete fixes (e.g. a relocated file whose build-time write was left behind as an orphaned `mv ... || true`).
5. **Modal-only fallback (no local Docker).** These leak classes are statically decidable, so classification needs no build; use Modal solely for the oracle (`-e modal`). Reproduce build-generated truth by running the committed generator with `python` (no Docker), or extract it inside a Modal session — never block on local Docker.
6. **Parallelize and recover.** For large batches, dispatch one subagent per chunk (each runs the single-task protocol, never re-dispatches) and aggregate. If a subagent crashes mid-task, inspect its partial working-tree changes and dispatch a recovery agent that *verifies/completes* rather than restarts.

---

## Verdict Rules

Each sub-agent check produces one of:
- **PASS** — No issues or only trivial cosmetic issues
- **WARN** — Minor issues that should be fixed but don't block usage (e.g., a tag is slightly too broad, one minor spelling error, a test dependency isn't pinned)
- **FAIL** — Critical issues that must be fixed (e.g., solution leaked into agent env, **essential instruction requirements with no test**, **test-only requirements** not documented and not environment-discoverable, **instructions vs tests contradict**, hardcoded solution, phantom tests, missing required metadata)

**Overall task verdict**: FAIL if any sub-check is FAIL. WARN if any sub-check is WARN and none are FAIL. PASS only if all sub-checks pass.

Be strict but fair. The goal is to catch real quality problems, not to nitpick style. If a file is missing entirely (e.g., no Dockerfile), that's a FAIL for that section. When in doubt, prefer WARN rather than FAIL, but always explain reasoning.

---

## Output

Generate TWO files in the user's specified output location (default: the root of the provided folder).

### File 1: `review-summary.csv`

A CSV file for quick scanning, sorting, and filtering. One row per task.

**Columns:**
```
task,metadata,dockerfile,instructions,tests,solution,anti_cheat,overall,critical_issues
```

**Values for verdict columns:** `PASS`, `WARN`, or `FAIL`

**The `critical_issues` column:** A short semicolon-delimited string summarizing only FAIL-level issues (empty if none). This lets you filter to failures and immediately see why without opening the details file.

**Example:**
```csv
task,metadata,dockerfile,instructions,tests,solution,anti_cheat,overall,critical_issues
task-name-1,PASS,WARN,FAIL,FAIL,PASS,PASS,FAIL,Untested requirement: output schema; Test-only threshold in tests; grep-only checks
task-name-2,PASS,PASS,PASS,PASS,PASS,PASS,PASS,
task-name-3,WARN,PASS,WARN,PASS,PASS,PASS,WARN,
```

### File 2: `review-details.md`

This is the detailed report for task authors. It should contain:

```markdown
# Terminal-Bench Quality Review — Detailed Findings
_Generated: <timestamp>_

---

## Task: <task-name-1>
**Overall: FAIL**

### Metadata — PASS
No issues.

### Dockerfile — PASS
No issues.

### Instructions — PASS
No issues.

### Tests — FAIL
- **Phantom test:** `test_handles_unicode` (line 45) tests for Unicode handling which is not mentioned or implied in the instructions. Either add this requirement to `instruction.md` or remove the test.
- **Unpinned dependency:** `test.sh` line 3 installs `pytest` without a version pin.

### Solution — PASS
No issues.

### Anti-Cheat — PASS
No issues.

---

## Task: <task-name-2>
...
```

**Formatting rules for `review-details.md`:**
- For PASS sections, just write "No issues." — keep it short.
- For WARN and FAIL sections, list every issue as a bullet with:
  - **Bold label** identifying the check that triggered it
  - Specific location (file, line number if possible)
  - What's wrong and what should be done to fix it
- Separate tasks with horizontal rules for easy scanning.
- At the end of the file, include a "Recurring Issues" section noting patterns across tasks (e.g., "4/6 tasks have unpinned test dependencies").

---

## Execution Notes
- If the user specifies an output directory, write files there. Otherwise write to the root of the provided task folder.
- If only a single task is provided (not a folder of tasks), still produce both files — the table will just have one row.
- If a task uses a non-standard structure, note it in the details but evaluate based on what's present.

---

## Verification Workflow — Running Tasks with `harbor`

### Verification Discipline (hard rules)

Every rule below has been violated in past sessions with measurable cost. They override habits.

1. **Do not report success until BOTH NOP and Oracle have terminated.** Interrupting Oracle mid-run = FAIL, not partial success. If you ran out of patience, the verification did not happen. Re-run, don't summarize.
2. **Polling cadence: `sleep 60` minimum between status checks.** Tighter loops add no information and burn cache. For runs expected >5 min, prefer `ScheduleWakeup` over in-window polling — and once a wakeup is scheduled, do **not** check status until it fires. The 226 wakeup calls + 6 interrupted Oracles in transcripts came from scheduling wakeup *and then polling anyway*.
3. **Never pipe a long-running process through `| head`, `| tee | head`, or any closing reader.** Closing stdout sends SIGPIPE and kills the upstream (this killed PID 11705 mid-PoC). Inspect progress with: `nohup CMD > run.log 2>&1 &`, capture PID, then `tail -20 run.log` on the file.
4. **n=1 gate before batch verification.** Before `harbor run ... -n <large>`, verify on exactly one task end-to-end. If the single-task path has a build-context or env-export bug, fan-out multiplies the bug across the batch. No exceptions for "obvious" cases — see the `/poc-first` skill for the protocol.
5. **Diagnose against the failure taxonomy first** (do not freeform-guess). Common Harbor failures, in order of frequency:
   - Docker build context wrong (file referenced but not in image)
   - Port reuse / container name collision from a prior run
   - Missing env export inside the container
   - `CMD` override hiding the real entrypoint
   - Hint regex artifact left in `instruction.md` after stripping
   - Test references `solution/` (mounted only for the oracle agent)
   - Test fallback `return` causing vacuous pass → nop > 0
6. **Pre-flight SSO before any remote harbor run.** `aws sts get-caller-identity` first. Expired SSO mid-launch has already cost one production run; don't let it cost another.
7. **Evidence format on completion:** a table with `task_id | NOP_reward | Oracle_reward | evidence_log_path`. No prose summaries without the table.

### Concepts

**Oracle agent** — The reference correct solution for a task. In harbor, every task has a `solution/` directory containing `solve.sh` (and optionally helper scripts). When you run harbor with `-a oracle`, it executes `solution/solve.sh` inside the container, then runs the verifier. The oracle should always score **1.0** (all tests pass). If it scores less, the solution or tests are broken.

**Nop agent** — A no-op agent that does nothing. When you run harbor with `-a nop`, it enters the container and immediately exits without taking any action. The nop should always score **0.0** (all tests fail). If it scores above 0.0, tests have a bug where some pass vacuously without any agent work.

**Tests / verifier** — The `tests/` directory is mounted into the container at verification time (not at Docker build time). For most tasks, `tests/test.sh` is the entry point — it runs pytest, custom bash checks, or both. The verifier score is `passed / total` where each check either passes or fails.

### Standard Verification Commands

**Default environment is `modal`.** Only fall back to `docker` when the agent or task has no viable modal equivalent (e.g., requires privileged mode, systemd, or a custom base image not supported on modal).

```bash
# Verify oracle scores 1.0 (task is solvable, solution works)
harbor run -p tasks/<task-name> -e modal -a oracle

# Verify nop scores 0.0 (tests don't pass vacuously)
harbor run -p tasks/<task-name> -e modal -a nop

# Verify a specific task folder with a single command
harbor run -p tasks/ml-task -e modal -a oracle && \
harbor run -p tasks/ml-task -e modal -a nop
```

Only use `-e docker` when:
- The task requires `systemd`, privileged containers, or host-network access
- The modal agent is unavailable or unsupported for the task type
- You are explicitly told to test with docker

Use `--force-build` with `-e docker` whenever:
- You've changed the Dockerfile or any file under `environment/`
- You've changed data generation scripts (e.g., `create_dataset.py`, `create_digits.py`)
- You're unsure if the image is stale

### Parsing Test Output from Harbor Runs

**Always parse output from modal runs.** Only fall back to docker output when modal is unavailable.

Harbor prints a summary line at the end of each run. Look for:

```
Score: 0.833 (10/12 tests passed)
```

Or for individual test results in the verifier output:

```
✓ test_name
✗ test_name — <reason>
```

When checking a run, capture the final score line and the list of passing/failing tests. Do not infer results from mid-run output — always use the final summary.

### How to Fix the Oracle Solution

The oracle solution lives at `tasks/<task-name>/solution/solve.sh`. To fix it:

1. Read `tasks/<task-name>/solution/solve.sh` to understand the current approach
2. Edit the file directly (or edit helper scripts it calls, e.g., `solution/train.py`)
3. Re-run harbor with `--force-build`:
   ```bash
   harbor run -p tasks/<task-name> -e docker -a oracle --force-build
   ```
4. The output shows which tests passed/failed. Fix the solution until you see score = 1.0.

Common oracle failures and fixes:
- **Score < 1.0, tests fail on specific checks** — Read the failing test output, fix the solution logic
- **Score = 0.0, all tests fail** — Solution script likely has a syntax error or wrong file paths
- **Score = 1.0, but nop also scores > 0** — Some tests pass vacuously without agent work; tighten those tests

### Re-Extracting Test Labels After Data Changes

Some tasks generate test labels during Docker build (e.g., `ml-task` generates `test_labels.csv`, `nn-from-scratch` generates `test_y.npy`). If you change the data generation script, you must re-extract and commit the new labels:

```bash
# 1. Build the image locally to extract labels (docker required for cp)
docker build -t <task-name>-new -f tasks/<task-name>/environment/Dockerfile tasks/<task-name>/environment/

# 2. Extract labels from the built image
CID=$(docker create <task-name>-new)
docker cp $CID:/tmp/test_labels.csv tasks/<task-name>/tests/test_labels.csv
docker rm $CID

# For numpy arrays:
CID=$(docker create <task-name>-new)
docker cp $CID:/tmp/test_y.npy tasks/<task-name>/tests/test_y.npy
docker rm $CID

# 3. Verify oracle still passes with new labels (use modal by default)
harbor run -p tasks/<task-name> -e modal -a oracle
```

### Task Directory Structure Reference

```
tasks/<task-name>/
├── task.toml                    # Metadata (difficulty, timeouts, resources)
├── instruction.md               # Problem statement shown to the agent
├── environment/
│   ├── Dockerfile               # Container image definition
│   └── data/
│       ├── create_dataset.py    # Data generation (deleted from final image)
│       └── <data files>         # Input data the agent sees
├── tests/
│   ├── test.sh                  # Verifier entry point
│   └── *.py / *.csv / *.npy    # Test fixtures and labels (mounted at verify time)
└── solution/
    ├── solve.sh                 # Oracle reference solution entry point
    └── *.py                     # Helper scripts for the solution
```

The agent only sees the container built from `environment/Dockerfile`. The `tests/` and `solution/` directories are **never** in the Dockerfile — they are mounted by harbor at verification time only.

---

## Known Anti-Patterns & Lessons Learned

These patterns have been identified from reviewing 50+ tasks and debugging oracle runs. Use them as a checklist when reviewing.

### Test Determinism (T-7)

**1. Never use fixed `sleep` before assertions — use polling loops.**
Bad: `sleep 3 && curl ...` or `time.sleep(3)` then assert.
Good: `for _ in range(N): try connect; break; sleep 1`.
Services may start slower on Modal than locally. Fixed sleeps are the #1 source of flaky oracle runs.

**2. Background process checks (pgrep) need retry loops.**
Bad: Single-shot `pgrep -f process.sh` to verify a daemon is running.
Good: Retry pgrep up to 10-15 times with 1s sleep. The process may have a brief startup delay.

**3. Timing thresholds must account for cloud hardware variance.**
Bad: `time.time() - t0 < 1.0` for I/O operations.
Good: Use generous thresholds (5x local baseline). The point is "fix made it fast" not "exactly how fast." A 1s local threshold may need 3-5s on Modal.

**4. Tests must not depend on other tests' side effects.**
Bad: test_check_6 reads `/tmp/out.log` written by test_check_3.
Good: Each test produces its own artifacts. Pytest doesn't guarantee execution order.

**5. Exact count checks are brittle.**
Bad: `wc -l < file.csv` expecting exact count '201'.
Good: Range checks (`> 100`) or content validation. Minor data changes shouldn't break the test.

**6. Use `scope="session"` for heavy service fixtures.**
Bad: `@pytest.fixture(scope="class")` starting nginx/gunicorn — services restart between classes with port-release race conditions.
Good: `scope="session"` so services start once. Pair with readiness polling that waits for HTTP 200 (not just "port open" or non-000).

### Test Outcome-Driven (T-1)

**7. Source code string checks should be anti-shortcut guards, not primary validation.**
Bad: `grep "iterations = len(text) * 3000" api.py` as the ONLY correctness check.
Good: Keep outcome checks (latency < 5s) as primary. Source code checks are fine as SUPPLEMENTARY anti-shortcut guards alongside outcome tests.
Example of good pattern: capacity-terrain-leak — tests 3-5 check source (anti-shortcut), tests 6-9 check terrain.json values (outcome).

**8. Config file checks and anti-shortcut checks ARE outcome-driven — don't flag these.**
These are commonly mis-flagged as T-1 violations:
- Checking `sensor_resolution: 65` in config.yaml — the config IS a deliverable.
- Checking `def diamond_square` exists — prevents gutting the algorithm and hardcoding output.
- Checking `@wraps` presence — when the bug IS about missing @wraps, checking for it checks the fix.

### Spec / Instruction Design (S-1, S-6)

**9. "Fix the broken X" instructions are unambiguous when the failure is observable.**
"Fix whatever issues are preventing the evaluation script from completing" sounds vague but IS unambiguous — the agent runs the failing command, sees the error, and knows what to fix. The instruction doesn't need to enumerate every bug. Only flag S-1 if the agent genuinely cannot determine the task goal after exploring the codebase.

**10. Output paths don't need to be in the instruction if they're in the codebase.**
If existing code already writes the output (e.g., `run_harness.py` writes `report.json`), the agent's job is to make the code run — not invent the output path. If `workflow.sh` hardcodes `daily_briefing.txt`, the agent discovers it by reading the script. Only flag S-6 if the agent genuinely cannot determine what to produce even after exploring the codebase.

### Rubric Design (L-2)

**11. Prescribing the only correct approach is not over-specification.**
When there's only one correct/canonical way (e.g., sklearn's `handle_unknown='ignore'`, Python's `list[bytes]`), naming it in the rubric is stating the answer, not over-constraining. The test should still verify outcomes where possible.

**12. Expert-calibrated rubric criteria should be left as-is.**
Rubric L-2 flags about prescribing fix location or specific tools are noted but not changed — these are set by domain experts who understand the task constraints.

### Environment / Infrastructure

**13. Every file referenced by Dockerfile COPY must exist.**
`COPY clinical_events.log /var/log/` with a missing file produces a generic "Image build failed" RemoteError on Modal — no clear error message. Always verify every COPY source exists.

**14. Log data files must cover all dates the tests expect.**
If tests expect compromised accounts from Dec 8, verify the log files contain Dec 8 entries. Missing date ranges cause silent data gaps that produce 0-count results.

**15. solve.sh must handle all code paths the tests exercise.**
If tests expect detection of users who logged in via `Accepted publickey` or `Accepted keyboard-interactive`, the solve.sh must parse all authentication methods — not just `Accepted password for`.

**16. test.sh should NOT contain `/solution/solve.sh` blocks.**
Harbor handles solution application separately. The test.sh is purely the verifier. Including a solve.sh block in test.sh is redundant and can cause confusion.

**17. Every task must have a `tests/test.sh` file.**
Without test.sh, the harness silently skips the task — no error, just missing from results. Always verify it exists.

### Time Estimate Units (M-1)

**18. `expert_time_estimate_min` and `junior_time_estimate_min` are in MINUTES, not seconds.**
The `_min` suffix means minutes. A common authoring mistake is writing values in seconds (e.g., `1200.0` meaning "20 minutes") but the field interprets them as minutes (= 20 hours). If you see easy tasks with expert times of 600-3600, they are almost certainly 60x too high.

**TB2 reference ranges (in minutes):**
- `easy`: expert 5-60, junior 20-120
- `medium`: expert 5-180, junior 10-480
- `hard`: expert 300-480, junior 600-19200

**19. Batch-wide unit confusion is a systematic smell.**
If ALL tasks in a batch have times ~60x higher than expected for their difficulty, the entire batch was authored with seconds-as-minutes confusion. Flag the pattern, don't review each task individually — the fix is to divide all values by 60 and then sanity-check against the ranges above.

### Test-Instruction Alignment (TIA)

**20. Anti-cheat constraints must be documented in instruction.md.**
Tests that ban `subprocess`, `os.system`, specific libraries, etc. are legitimate anti-cheat guards — but if instruction.md doesn't mention them, they fail alignment checks. Fix: add the constraint to instruction.md as a short prohibition ("Do not use subprocess or os.system"), NOT remove the test. Keep wording minimal — state what's forbidden, never explain why.

**21. "Discoverable from environment" means CHECK the environment before flagging.**
Many tests that LOOK phantom are testing values present in the container's config files, build scripts, or source code. Before flagging a test as phantom:
1. Read the Dockerfile to find every COPY/ADD
2. Read the copied files for the tested values
3. If the value appears in ANY agent-visible file, the test is NOT phantom — it's discoverable

**22. Tree hash tests for rebase tasks need careful architecture consideration.**
Tree hash comparison (`git rev-parse 'HEAD^{tree}'`) verifies file contents are preserved after rebase. Critical correctness test, BUT:
- The reference must survive Docker build → agent execution → test verification pipeline
- If the oracle resolves conflicts (e.g., `git checkout --theirs`), tree hash comparison is INVALID — the oracle itself alters the tree
- Safe: git tag created during setup scripts (persists in `.git/refs/tags/`), or compare against a branch ref
- Unsafe: hardcoded hashes, or comparing when the solution legitimately alters file contents

**23. Commit message pattern matching must be mutually exclusive.**
When testing commit reordering, each pattern must match EXACTLY ONE commit. If two commits share keywords, add exclusion conditions to disambiguate.

**24. Upper bounds on results must accommodate the oracle.**
When adding upper bound assertions (`len(results) <= N`), ALWAYS verify the oracle's actual output first. Run the oracle, then set bounds with 30-50% margin.

**25. Message/pattern detection must match what the oracle actually fixes.**
If a test checks that certain messages are reworded or removed, verify the oracle solution actually addresses ALL of them. Only assert on items the oracle handles.

**26. Phantom tests fall into distinct categories requiring different fixes.**

| Category | Example | Fix |
|----------|---------|-----|
| **Undocumented anti-cheat** | Subprocess/import bans not in instructions | Add constraint to instruction.md |
| **Hardcoded oracle values** | `EXPECTED_TREE_HASH = "abc123"` | Remove if arbitrary artifact; keep if correctness check |
| **Implementation detail enforcement** | Testing HOW files are written (atomic writes, stamp files) | Remove — tests HOW not WHAT |
| **Code pattern checks** | Grepping source for specific function calls or strings | Remove — tests source code not behavior |
| **Specific data value tests** | Testing for specific log values not enumerated in instructions | Remove — values not in spec |
| **Undocumented structural requirements** | Max files per commit, minimum commit counts | Remove — instructions don't specify limits |
| **Overly strict thresholds** | Exact ranges for approximate guidance ("approximately N") | Widen range or remove |

**27. Time limit mismatches: use cloud-margin multiplier.**
If instruction says N seconds, test with `elapsed < N * 1.67` (round up). Cloud environments (Modal) add latency. Never test at exactly the instruction's limit.

**28. Tolerance contradictions: "exact" means exact.**
If instructions specify exact output (e.g., "2 decimal places"), tests must NOT introduce undocumented tolerance. Use exact comparison for integers, tiny epsilon for floats, or document tolerance in instruction.md if relaxation is intentional.

**29. Content verification tests prevent empty-file exploits.**
After removing phantom structural tests, verify remaining tests catch empty/stub implementations. Common gap: file existence checks pass for empty files. Fix: add content verification (class/function definitions, minimum size, keyword presence). Keep checks flexible — verify content EXISTS, don't check exact implementation.

**30. Binary artifact verification catches build-level bugs.**
For build-system tasks (symbol collision, ODR violations, linking), add tests that inspect binaries with `nm -D --defined-only` or `readelf`. These catch bugs that functional tests miss. Ensure required tools (`binutils`) are in the Dockerfile.

**31. Search/query tasks need deterministic answer grading.**
Semantic tests alone (keywords, author names, minimum counts) cannot verify answer correctness — wrong results with the right metadata pass. Search tasks should have:
1. An expected answers dictionary mapping queries to sets of correct results
2. Exact set comparison tests using the expected answers
3. Semantic tests as SUPPLEMENTARY validation
4. Completeness tests verifying all queries are answered

To populate expected answers: run the oracle, capture output, resolve to canonical identifiers.

**32. Instruction ↔ test mismatch is a review theme, not three separate bugs.**
Roll up recurring feedback ("requirements not stated clearly," "tests add requirements," "tests miss requirements," "only grep-based checks") into one pass: (1) list every hard requirement in `instruction.md`, (2) map each to tests, (3) list every assertion cluster in tests, (4) map each to instruction or environment. Gaps in (2) → untested requirements; gaps in (4) → test-only or phantom requirements; contradictions → FAIL until reconciled.

**33. Static string matching vs runtime validation — decision rule.**
If reviewers report "tests use static string matching instead of runtime validation," apply Sub-Agent 4 §3: grep/source-only suites get **WARN** when they replace behavioral checks; escalate to **FAIL** when essential behavior (service up, correct output artifact, correct DB state) is never executed or parsed from real outputs.

### False Positive Prevention (FP — from reviewing 300+ tasks)

These patterns were repeatedly flagged as WARN/FAIL during review but turned out to be correct on deeper inspection. **Check for these BEFORE flagging.**

**FP-1. Anti-shortcut guards alongside runtime tests are PASS, not WARN.**
When you see grep-based static checks (`test_uses_adamw`, `test_worker_uses_tailable_await`), do NOT immediately flag as "static-only validation." Read the ENTIRE test file first. If the same file contains runtime/outcome tests that verify the same behavior (e.g., `test_accuracy_threshold` runs inference and checks >=90%), the static checks are anti-shortcut guards — they prevent gaming the runtime tests. Mark PASS.
*Pattern to verify:* For each static test, ask "Is there a runtime test that would fail if this code pattern were absent?" If yes → anti-shortcut guard → PASS.

**FP-2. Files referenced by tests that are COPY'd in Dockerfile are not missing.**
When a test references a file like `/tmp/check.py`, do NOT assume it doesn't exist. Search the Dockerfile for `COPY check.py /tmp/check.py` or equivalent. Also check setup scripts, RUN commands, and multi-stage builds. Only flag if the file genuinely doesn't appear anywhere in the build pipeline.
*Pattern to verify:* `grep -n "check.py" environment/Dockerfile` before flagging.

**FP-3. Grep patterns that already handle alternatives are not fragile.**
When you see `grep -E 'sort.*-S'`, check the ACTUAL regex before flagging as "doesn't accept alternatives." Many patterns already include alternatives like `grep -vE 'sort.*(-S|--buffer-size)'`. Read the exact pattern, not just the function name.

**FP-4. Hardcoded expected values from static evidence are deterministic ground truth.**
When evidence data is baked into the Docker image (static files, pre-populated databases), expected values in tests are deterministic — they can never change without rebuilding the image. These are NOT fragile hardcoded values; they ARE the correct expected output. Mark PASS.
*Key question:* "Can the input data change without a Docker rebuild?" If no → hardcoded expected values are correct.

**FP-5. Exhaustive search task test sets are ground truth, not over-rigid.**
When a task requires finding ALL instances (migrate all endpoints, find all encoded variants, replace all occurrences), the test MUST know the complete set. Hardcoded path lists like `EXPECTED_PATHS = {...}` with 29+ entries are correct test design — they verify exhaustiveness. Often complemented by a live `grep -RIl` sweep as negative verification. Mark PASS.

**FP-6. Test coverage that seems "minimal" may be adequate on file read.**
Don't flag "minimal test coverage" from file names alone. Read the actual test functions and map each to an instruction requirement. A task with 7 tests covering all 4 instruction requirements has adequate coverage. Only flag if a SPECIFIC hard requirement has zero test coverage.

**FP-7. "Undocumented" test values that are discoverable from environment.**
Before flagging "tests check values not in instructions," check whether those values appear in config files, source code, schema files, or other agent-visible files in the container. Values in `.json`, `.yaml`, `.env`, `config/`, database init scripts, or source code comments are discoverable — the agent is expected to explore. Only flag if a value appears NOWHERE in the agent-visible environment.
*Pattern to verify:* For each "undocumented" test assertion value, `grep -r "VALUE" environment/` before flagging.

**FP-8. Constraint files in tests/ namespace are not harbor test leaks — but only if BOTH guards exist.**
Some tasks place constraint files at paths like `/app/tests/verify_ota.py` or `/app/tests/run_tests.py`. The "intentional constraint" exception applies **only when both** of the following are verified by grep:
  (a) `instruction.md` explicitly names the file as immutable ("do not modify `<path>`"), AND
  (b) A harbor test enforces integrity (hash check, ref-file diff, or equivalent).
If either is missing, FP-8 does NOT apply. Default to running the cheat-trace (DT-9 below) instead of marking PASS. The cosmetic-naming framing is a *consequence* of the two guards, not a substitute for them.

### Deep-Trace Patterns (DT — from reviewing 1500+ tasks; addresses subtle phantoms missed by FP-7 grep)

These are issues that pass FP-7's surface grep but are still genuine instruction-test misalignments. When a task initially looks PASS, run these checks before signing off.

**DT-1. "Discoverable" means COPY'd into the agent image, not "exists in the task dir".**
The agent only sees files added to the container by `environment/Dockerfile`. A value that appears only in `solution/solve.sh`, `tests/test_outputs.py`, or `solution/*.py` is *not* discoverable — those dirs are mounted at oracle/verifier time only. When verifying FP-7, restrict the grep to files referenced by `COPY`/`ADD` in the Dockerfile, not the whole task tree.

**DT-2. Setup-time vs runtime path materialization.**
A path that exists only after `solve.sh` runs (e.g., `nohup ... > /var/log/foo/bar.log` redirected by the oracle) is a hidden contract. The agent works with the Docker image as built; if a test asserts on a file that the agent must create at a path never mentioned in any agent-visible file, flag as **FAIL** (test-only requirement). Either materialize the path during environment setup, or document it in `instruction.md`.

**DT-3. Cross-file contradictions.**
Read instruction.md, tests, AND solution together — not in isolation. Patterns to catch:
- Instruction forbids modifying function X; X contains a literal that test forbids elsewhere → impossible.
- Instruction lists outcomes A, B, C; test enforces A and a hidden D → instruction incomplete.
- Solution itself violates instruction (e.g., solution lowers a value the instruction says is fixed) → instruction broken or test broken.
A single FP-7 grep pass won't catch these; they require reading the three sources together.

**DT-4. Behavioral runtime sequences are not statically reviewable.**
Tests that depend on Redis-restart RDB-reload, supervisor respawn, kill races, signal handling, or other dynamic state interactions are easy to miss in static review. Watch for:
- `test.sh` restarts a service that reloads persisted state (e.g., redis with RDB dump), undoing the agent's work.
- Tests that `kill -9` a process and expect respawn but no supervisor exists in the image.
- Tests that sleep and check for an event that may not have happened yet.
Flag as fragile under T-7. Require either explicit cleanup in the test setup or idempotent assertions that don't depend on timing.

**DT-5. `set -e` + pytest aborts before reward.txt write (nop-only failure).**
A common test.sh anti-pattern:
```bash
set -e
...
pytest /tests/test_outputs.py -rA
_EXIT_CODE=$?
if [ $_EXIT_CODE -eq 0 ]; then echo 1 > reward.txt; else echo 0 > reward.txt; fi
```
When pytest fails (nop case), `set -e` aborts the script before reward.txt is written. Harbor reports "No reward file found" instead of 0.0. Oracle masks the bug because pytest passes. Always wrap pytest in `set +e ... set -e`:
```bash
set +e
pytest /tests/test_outputs.py -rA
_EXIT_CODE=$?
set -e
```
Also, network-dependent installs under `set -e` (e.g., `curl https://astral.sh/uv/...|sh`) are nop-fragile when the network blips — add `|| true`. Flag this pattern when reviewing test.sh hygiene.

**DT-6. Verifier timeout calibration.**
A task with long-running services, large model imports, or end-to-end pipeline runs needs `verifier.timeout_sec` ≫ 300. Verify that `task.toml` allows enough budget for the worst-case verifier run plus container startup overhead. Symptoms: nop trial errors with "Verifier execution timed out after 300.0 seconds" while oracle passes — because oracle skips heavy paths and nop hits them all.

**DT-7. Snapshot/sync hygiene.**
When duplicating a task into a snapshot dir for harbor verification, always copy the FULL task tree (`cp -R tasks/<n>/* snapshot/<n>/`), not selective files. Partial syncs leave the snapshot missing `task.toml`/`Dockerfile`/etc, and harbor errors with `AddTestsDirError` or empty trial dirs that look like build failures. This is a meta-rule for the fix workflow: never use selective `cp` when constructing a verification snapshot.

**DT-8. Test-only literals in `solution/` are oracle-only — they cannot be referenced by the agent.**
When a test enforces a literal string and the only place that string appears is `solution/solve.sh` or `solution/*.py`, the agent has no way to know it. This is the most common form of "phantom requirement" missed by FP-7 surface scans. Always confirm the literal appears in an agent-COPY'd file before accepting the test. If only in `solution/`, either move the contract into `instruction.md` or convert the literal check to a behavioral check.

**DT-9. Mandatory cheat-trace for any agent-writable file the test suite executes or reads.**
The most consequential anti-cheat failures are not solution leaks — they are agent-side test runners or fixture files that the harbor verifier transitively depends on. When the Dockerfile COPY'd a file into agent-writable space AND the harbor tests invoke it (directly, via `make`, via a service it backs, via shell scripts, or by reading its output as ground truth), do NOT accept the task as PASS until you have completed this trace:

1. **Identify the writable surface.** For every COPY target, decide whether the agent can overwrite it. Defaults: no `USER` directive ⇒ root inside container ⇒ everything is writable unless the Dockerfile sets restrictive perms (`chmod 0555`, root-owned + non-root user). Symlinks, bind mounts, and `/opt` paths are not automatically protected.
2. **Identify the dependency edge.** Trace from each harbor assertion backward to which COPY'd files it depends on (executes, sources, imports, reads as data, or whose output it observes via DB rows / log files / HTTP responses).
3. **Run the cheat-trace.** For each writable file on a dependency edge, ask: *"What is the smallest replacement of this file's contents that satisfies every assertion that depends on it?"* Common minimum-effort swaps:
   - Print the substring the test greps for and exit 0.
   - Write the expected DB rows / files / state directly, bypassing the real pipeline.
   - Generate fresh timestamps / IDs to satisfy freshness or uniqueness checks.
   - Move forbidden literals into the swapped file when the ban only covers a sibling file.
4. **Walk all assertions together.** If the union of swap strategies satisfies the full test suite without exercising the real task logic, the file is an exploitable cheat vector. Flag **FAIL on Anti-Cheat**, regardless of FP-8 framing.
5. **Verify reset hooks.** Pre-test hooks (`pre_run.sh`, `test.sh`, fixture setup) often wipe DB state or restart services but rarely restore agent-writable source files. Read the hooks; do not assume they sanitize the writable surface.

The load-bearing question is *"if the agent edits this file, what breaks?"* — not *"is this file named like a test?"* nor *"is it documented as a constraint?"*. Apply this trace **before** any FP-rule exception. FP rules prevent over-flagging; they do not excuse skipping the cheat-trace. Inversion of that order — pattern-matching to FP-8 because the file lives under `tests/` and stopping there — is the most common review failure mode for tasks with agent-side test runners, fixture generators, or in-image verifiers.

Mitigations that resolve the FAIL:
- Ship a reference copy in harbor `tests/` and add a hash/diff check in `test_outputs.py` (mirroring the pattern used for canonical source files).
- Move the file to a non-writable location (root-owned `chmod 0555` under `/opt/...`) and update callers.
- Convert assertions to runtime/outcome checks against the real downstream system (HTTP, DB rows produced by the legitimate pipeline) so swapping the file no longer satisfies them.
- As a minimum, document "do not modify `<path>`" in `instruction.md` AND add an integrity check — both, not either.

**DT-10. Wrapper-bypass: locking the verifier without locking its invokers is degenerate.**
A common pattern: a "protected" verifier file `X` is integrity-checked (cmp/sha256/diff), but `X` is invoked transitively by an UNPROTECTED wrapper (`/usr/local/bin/run-X`, `start.sh`, a Makefile target, a systemd service, an HTTP entrypoint). The agent leaves `X` byte-identical (cmp passes), then rewrites the wrapper to print the success literal directly and never invokes `X` at all.

When reviewing any cmp-style guard on a verifier file, also enumerate every shell wrapper / Makefile target / service unit that calls it. If any caller is itself unprotected, the integrity guard on `X` is decorative — the cheat lives at the caller layer. Mitigation: extend integrity guards to the smallest closed set of files whose collective execution proves real work was done. Practical heuristic: if a test invokes `wrapper.sh` (or `make X`) and asserts a substring of its output, ensure both `wrapper.sh` AND every script it transitively calls are integrity-checked, OR that some downstream behavioral assertion (DB rows, network response) cannot be faked at the wrapper layer.

**DT-11. State-injection bypass: behavioral output checks without proof-of-execution are forgeable.**
When a test asserts on a side-effect artifact (a JSON file, a row count, a redis key, a Mongo document, a generated PNG) but does NOT verify the artifact was produced by the legitimate pipeline, the agent can write the artifact directly and never invoke the protected pipeline. Common examples:
- Test asserts `verified_count: 5` in `report.json` → agent writes that JSON literally; never runs the verifier that signs the report.
- Test asserts `count(*) = 8` in DB → agent does `INSERT INTO table VALUES (...)` directly; never runs the importer.
- Test asserts `KEYS result:* > 0` in redis → agent does `redis-cli SET result:fake x`; never runs the worker.

For every assertion of the form "output X has property Y," ask: *what proves the legitimate pipeline produced X, vs. an attacker writing X directly?* Add at least one of:
- **Mutate-and-rerun**: at verify time, perturb a known input (append a unique row to a gz / mutate a config / wipe a DB table) and re-invoke the pipeline; assert the perturbation propagates to the output.
- **Cryptographic re-verification**: tests should re-verify any signatures/hashes on the output using a reference key or known-good corpus, not trust the producer's claim.
- **Delete-and-regenerate**: `rm -f /app/output.json && python3 /opt/.../generate.py && [ -f /app/output.json ]` — proves the generator ran during the verifier phase, not before.
- **Side-channel proof-of-execution**: log timestamps, freshness markers, or runtime-only artifacts that only the real pipeline would produce.

A behavioral assertion paired with a proof-of-execution closes the cheat. A behavioral assertion alone is forgeable.

**DT-12. In-image `.bak`/`.orig` cmp guards are degenerate when the agent runs as root.**
The pattern `cmp -s /opt/foo.py /opt/.foo.py.bak` is intended to detect tampering with `/opt/foo.py`. But when the Dockerfile has no `USER` directive (or USER=root), the agent runs as root and can overwrite **both** files with identical attacker content — cmp still passes because the files match each other. The cmp anchors to a writable backup, not to a canonical original.

Always check:
1. Does the Dockerfile drop privileges with `USER`?  If no, every in-image path is agent-writable regardless of `chmod 0555` (root overrides chmod).
2. Where does the integrity reference live? In-image `.bak`/`.orig`/`.backup` (writable) → degenerate. Under `/tests/` (harbor read-only mount) → sound. Computed from a value baked into `test_outputs.py` (e.g., a hardcoded sha256) → sound.

Mitigations (preferred order):
- Move the reference into `tests/<file>.ref` and edit cmp/diff/sha to compare against `/tests/<file>.ref` (mounted read-only at verify time).
- Bake a hash literal into `test_outputs.py` and recompute at verify time.
- As a last resort, drop privileges in the Dockerfile so the in-image `.bak` is genuinely unwritable by the agent.

**DT-13. Modal-vs-docker snapshot boundary: dynamic build-time files may not persist.**
On modal, files written by `RUN` commands during `docker build` are usually preserved (Docker layer commit), but **dynamically-written state from running daemons** during build can be lost. Confirmed examples: MySQL binlog files (`/var/lib/mysql/binlog.NNN`), certain database WAL/redo logs, daemon-managed PID files. These work on `-e docker` but vanish on `-e modal` because of differences in container-snapshot semantics.

When a task's solve path or verifier depends on dynamically-written runtime state surviving a build → runtime boundary, make the dependency explicit:
- **Snapshot at build time** to a static file outside the daemon's data directory. Pattern: stop the daemon at the end of `prestart_setup.sh`, dump the relevant state with a tool like `mysqlbinlog` / `pg_dump` / `redis-cli SAVE`, write the dump to `/opt/<task>/snapshot.<ext>`, then have `solve.sh` read from the snapshot instead of the live daemon files.
- **Test on modal early.** Tasks confirmed `oracle = 1.0` on `-e docker` may still fail on `-e modal` for this reason. Run modal verification before declaring a task fixed.

**DT-14. Verifier-phase service restart hygiene.**
On modal, services started during the agent phase (e.g., HTTP servers spawned by `solve.sh` via `nohup ... &`) may die between agent and verifier phases. The verifier then runs against an empty port and tests that depend on the service all fail with empty curl output.

Defensive `tests/test.sh` should health-check every long-running service the verifier depends on, and restart it if needed:
```bash
if ! curl -s -o /dev/null --max-time 2 http://127.0.0.1:<port>/<healthcheck>; then
    pkill -f '<service-process-name>' 2>/dev/null || true
    nohup <restart-command> > /tmp/<svc>.log 2>&1 &
    for i in $(seq 1 30); do
        curl -s -o /dev/null --max-time 2 http://127.0.0.1:<port>/<healthcheck> && break
        sleep 1
    done
fi
```
Apply this for any task whose tests assert against an HTTP service, DB connection, or other long-running daemon. Symptom of missing this guard: oracle passes intermittently on cached images but fails on fresh `--force-build` because cached state happens to keep the service alive.

**DT-15. Fix-induced regressions: verify solve.sh interaction before applying restoration patterns.**
Before applying the canonical-file-restoration fix template (`cp /tests/<file>.ref <container_path>` in `tests/test.sh`), confirm that `solve.sh` does NOT itself modify the file at `<container_path>`. If it does, the restore clobbers the agent's legitimate edit and the oracle regresses.

Mandatory pre-fix check for every "restore from /tests/" pattern:
```bash
grep -n "<container_path_basename>" solution/solve.sh
```
If solve.sh writes to that path with `cat >`, `sed -i`, `echo >>`, `cp`, `mv`, or `tee`, the file is part of the legitimate fix surface — it should be reclassified as **FP** (the verifier file IS a deliverable, not an exploit vector). Do not apply the restoration fix.

This check would have caught `cascading-lock-failure` before the restoration fix broke its oracle: solve.sh:40 does `sed -i 's/item\["amount"\]/item\["claim_amount"\]/g' verify_locks.py` — restoring to canonical erases the agent's bug fix.

**DT-16. Anti-stub: verifier scripts in agent-writable paths invoked by tests for stdout substrings.**
The most common anti-cheat hole. Dockerfile `COPY verify.py /app/verify.py` (or `validate*.py`, `mock*.py`, `oracle.py`); `test_outputs.py` invokes the path and asserts `'SUCCESS' in stdout`. A root agent can overwrite with `print('SUCCESS'); exit(0)`.

**Detection regex:** Dockerfile COPYs `*verify*.py|*validate*.py|*mock*.py|*oracle*.py|*grader*.py|*judge*.py|*checker*.py` into `/app|/opt|/usr|/var|/tmp|/root` AND test_outputs.py invokes the same absolute path AND solve.sh does NOT modify the script (DT-15 check).

**Fix template (Pattern A):**
1. Copy the canonical script to `tests/.truth/<name>` (verifier-mounted only).
2. Edit the test invocations to use `python3 /tests/.truth/<name>` (NOT bare path — `/tests/` is mounted without the executable bit; bare invocation fails with `Permission denied`).
3. Optional: keep the agent-readable copy if task design wants it as reference; verifier no longer trusts it.

**False-positive guard:** if the test also asserts on a real side effect (DB rows, file hashes, HTTP responses) that a stub can't fake, the substring check is just a tripwire — mark FP, not FAIL.

**DT-17. Verify-time truth generation pattern.**
When a baked truth file diverges between host and container, the fix is rarely "regenerate truth on host more carefully" — it's to move generation to verify time inside the same container that built the agent image.

**Drivers of host-vs-container drift:**
- zlib/gzip output: different library versions emit different deflate bytes for the same input.
- `os.path.realpath`: host filesystem layout differs from container.
- `random.seed` missing or set late: implicit ordering changes between Python versions.
- gzip header MTIME: defaults to wall-clock → different bytes between two runs of the same container.

**Fix template:**
1. Extract truth-generation logic from `environment/setup_commands.sh` / `generate_data.py` into a standalone script.
2. Commit to `tests/.truth/generate_truth.py` (verifier-mount only).
3. `tests/test.sh` runs it before pytest: `mkdir -p /tmp/_truth && python3 /tests/.truth/generate_truth.py --out /tmp/_truth/`.
4. Update `test_outputs.py` paths from `/tests/.truth/truth.json` → `/tmp/_truth/truth.json`.
5. Remove all truth-baking from the Dockerfile (the agent must never see truth).

**Determinism preconditions:**
- `random.seed(<int>)` explicit at top of generator.
- `gzip.compress(..., mtime=0)` to suppress wall-clock in header.
- `json.dumps(..., sort_keys=True, separators=(',', ':'))` for canonical JSON.
- Avoid `os.path.realpath` of agent inputs unless necessary.

**DT-18. Pinned hash literal beats `.sha256`/`.md5` sentinel files.**
DT-12 family. `RUN sha256sum file > /opt/file.sha256` then test does `sha256sum -c /opt/file.sha256`. A root agent can rewrite both the file and the sentinel → check passes against tampered content.

**Fix:** compute the hash once (deterministically), hardcode the literal in `test_outputs.py`, and replace `sha256sum -c` with:
```python
import hashlib
EXPECTED = "3c120d26..."
assert hashlib.sha256(open(path, 'rb').read()).hexdigest() == EXPECTED
```

When the file is non-deterministic across builds (generated keys, compiled binaries), pinning fails — fall back to a **behavioral assertion** (run the binary with a probe input, check structural properties via `nm`/`readelf`/HTTP probe) instead of hashing.

**DT-19. Inline-encoded constants for agent-runtime tools with secret bounds.**
When a runtime tool (e.g., a probe binary or seed checker) MUST work at agent solve time but its bounds/answers shouldn't be readable, plaintext side files like `/var/lib/<tool>/bounds.json` are an obvious cheat (root agent `cat`s them).

**Fix:** embed bounds inline as base64- or hex-encoded constants:
```python
import base64, struct
_B = base64.b64decode("AAAAAAAW42AAAAAAABhqAA==")
LOW, HIGH = struct.unpack(">QQ", _B)
```
Decompiling to recover constants is materially harder than reading a JSON file. Pair with a small profile-selector byte (e.g., `/etc/<tool>_profile` = `0x00` default, `0x01` alt) for tasks that need multi-mode behavior (e.g., mutate-and-rerun).

**DT-20. Trim large baked truth corpora — ship the generator, not the output.**
If `tests/.truth/hidden_corpus/` holds thousands of fixture files (tens of MB), the repo bloats and re-syncs are slow. The generator is usually a few KB.

**Fix:** ship only `tests/.truth/generate_corpus.py`. In `tests/test.sh`:
```bash
mkdir -p /tmp/_corpus
python3 /tests/.truth/generate_corpus.py --seed 42 --count 10000 --out /tmp/_corpus/
```
Update test paths from `/tests/.truth/hidden_corpus/` → `/tmp/_corpus/`. DT-17 determinism preconditions apply.

**DT-21. Cross-repo sister diff before scanning a vendor copy.**
When two vendor repos share the same Terminal-Bench task pool, the cheap first move is `diff -rq` per overlapping task. Byte-identical → inherit prior triage; differing → review the delta first (usually cosmetic Dockerfile drift; occasionally missing anti-cheat that needs to be ported across).

**Workflow:**
```bash
comm -12 <(ls repoA/tasks) <(ls repoB/tasks)
for t in <overlap>; do diff -rq repoA/tasks/$t repoB/tasks/$t | head; done
```
If repoA has anti-cheat hardening that repoB lacks, sync repoA → repoB before any new triage.

**DT-22. Timing-sensitive recovery tests need cloud-margin timeouts.**
SIGKILL-recovery, supervisor-respawn, and "wait for output to reach N bytes" patterns embedded inside `timeout 15 bash -c '...'` are routinely flaky on modal even when oracle passes locally. T-3 prescribes 5× local baseline; for SIGKILL-recovery and similar timing-sensitive checks, **default to 60s timeouts** unless oracle margin has been measured. Decode any base64-encoded shell commands when bumping — flakiness fixes often live inside base64 blobs.

**DT-23. Build-time substitution requires post-substitution reference bytes.**
When the Dockerfile modifies a script at build time (e.g., `sed -i 's/EXPECTED_HASH_PLACEHOLDER/{computed}/' /opt/run.py`) AND a test does `cmp /opt/run.py /tests/run.py.bak`, the committed `.bak` must reflect the POST-substitution state, not the source `environment/run.py`. Pre-substitution bytes → cmp always fails even when the agent did nothing wrong.

**Fix:** recompute the same substitution deterministically (its inputs are usually pure functions of seeds/integer ranges) and regenerate the committed reference file to byte-match the runtime state.

**DT-24. zlib/gzip determinism caveats.**
- `gzip.compress(data)` writes wall-clock to MTIME header → non-deterministic. Always pass `mtime=0`.
- Different zlib library versions can produce different deflate output for identical input. Two runs inside the SAME container are stable; host vs container is not. If a test asserts on `sha256(zlib.compress(...))`, you cannot regenerate truth host-side and expect a match — use DT-17 (verify-time regen).
- gzip 1.12+ on some BuildKit setups escalates the "file timestamp out of range" warning to exit 2. `COPY`'d files inherit BuildKit's normalized mtime (often pre-1970 or post-2106). Mitigation: `RUN touch <file> && gzip -n <file>` — `touch` resets mtime to current; `-n` strips the timestamp from gzip's header.

**DT-25. BuildKit heredoc syntax directive is mandatory.**
Dockerfiles using `RUN cat <<'EOF' > /file` heredocs need `# syntax=docker/dockerfile:1.4` as the very first line. Without it, the classic builder parses heredoc body as Dockerfile instructions and fails with `unknown instruction: <first word of heredoc body>`.

**DT-26. ZIP/zipapp file timestamps must be ≥ 1980.**
Python `zipapp.create_archive` and `zipfile.ZipFile.write(filename)` read filesystem mtime and refuse pre-1980 (raises `ValueError: ZIP does not support timestamps before 1980`). BuildKit's normalized COPY mtimes can land at or before 1970. Mitigation: `RUN find <copy_target_dir> -exec touch {} +` before invoking zipapp/zipfile.

---

## Fix Workflow — Remediation After Review

After the review phase identifies issues, follow this workflow. Key principle: **every test change must be verified with harbor oracle and nop before considering it done.**

### Fix Taxonomy

| Action | When | How |
|--------|------|-----|
| **REMOVE** | Test checks undiscoverable, undocumented, non-anti-cheat behavior | Delete the test function |
| **DOCUMENT** | Anti-cheat guard that's reasonable but undocumented | Add constraint to `instruction.md`; keep test unchanged |
| **FIX** | Wrong threshold, contradictory logic, tolerance mismatch | Edit assertion to match instruction spec |
| **ADD** | Instruction specifies requirement with zero test coverage | Add new test function |
| **KEEP** | Test is discoverable from environment or properly documented | No change |

### Decision Tree for "Is This a Phantom Test?"

```
Test checks value/behavior X
├── Is X stated in instruction.md?
│   ├── YES → KEEP
│   └── NO → Continue...
├── Is X discoverable from environment/ files?
│   ├── YES → KEEP
│   └── NO → Continue...
├── Is X a reasonable anti-cheat guard?
│   ├── YES → DOCUMENT in instruction.md, KEEP test
│   └── NO → Continue...
├── Is X an implementation detail (HOW not WHAT)?
│   ├── YES → REMOVE
│   └── NO → Continue...
└── Is X a hardcoded oracle artifact?
    ├── YES, correctness check → KEEP, verify oracle produces it
    └── YES, arbitrary → REMOVE
```

### Parallel Team Execution Strategy

For large batches (20+ tasks), split into parallel agent teams of ~5 tasks each. Each team:
1. Reads ALL files for their tasks before making any changes
2. Makes changes independently (tasks don't share files)
3. Reports summary of changes

Split teams by task category (algorithm, build system, git, infrastructure, data processing, etc.) since tasks in the same category share patterns.

### Post-Fix Audit Protocol

After all fixes, run a coverage audit before harbor verification:

1. **For every test REMOVED**: verify remaining tests still catch incorrect implementations
2. **For every test ADDED**: verify the oracle solution would pass it
3. **For rebase/history tasks**: verify content preservation tests exist
4. **For search/query tasks**: verify deterministic answer grading exists
5. **For build tasks**: verify functional outcome tests remain (not just structural checks)

### Mandatory Harbor Verification

**After ANY test or instruction.md change, run both oracle and nop verification.**

#### Batch Verification

```bash
# Oracle: expect all tasks score 1.0
harbor run -p /path/to/tasks -e modal -a oracle -n <count> --job-name "oracle-verify" --force-build

# Nop: expect all tasks score 0.0
harbor run -p /path/to/tasks -e modal -a nop -n <count> --job-name "nop-verify" --force-build
```

Use `--force-build` when you changed any file under `environment/`, setup scripts, or on first run.

#### Reading Results

```python
import json
with open("jobs/<job-name>/result.json") as f:
    d = json.load(f)
    stats = d["stats"]["evals"]
    key = list(stats.keys())[0]
    rewards = stats[key]["reward_stats"]["reward"]
    for score, tasks in rewards.items():
        if score != "1.0":  # For oracle (use "0.0" for nop)
            print(f"FAILED ({score}): {tasks}")
```

#### Debugging Failed Trials

```bash
# Which tests failed
cat jobs/<job-name>/<trial-name>/verifier/test-stdout.txt | grep -E "FAILED|PASSED"

# Full error details
cat jobs/<job-name>/<trial-name>/verifier/test-stdout.txt | grep -B2 -A25 "FAILED"
```

Common oracle failure causes after QC fixes:
- **Upper bound too tight**: Oracle returns more results than your cap. Raise with margin.
- **Pattern matching too strict**: Keywords don't match oracle's actual output. Broaden or add alternatives.
- **Reference not available**: Git tag/ref from build not surviving to test time. Add fallback or graceful skip.
- **Pattern list mismatch**: Test checks for items the oracle doesn't actually fix. Remove un-addressed items.
- **Tolerance too tight**: Oracle has rounding differences. Use appropriate epsilon.

Common nop > 0 causes:
- Test checks file existence for a file baked into the Docker image
- Test has a fallback `return` that silently passes when data is missing
- Test uses `if not condition: return` instead of `assert condition`

### Harbor Architecture Constraints for Test Changes

1. **Tests CANNOT reference `solution/` at runtime** — only used by oracle agent
2. **Tests CAN reference files baked into the Docker image** — anything from `docker build` persists
3. **Tests CAN reference `/tests/` paths** — harbor mounts `tests/` at verification time
4. **Git tags/refs from setup scripts persist** — stored in `.git/refs/` inside the image
5. **Tests should gracefully handle missing references** — use `pytest.skip()` or fallback logic
6. **Verify tool availability** — if tests use `nm`, `readelf`, `curl`, ensure Dockerfile installs them

---

## Self-Healing: Recording Newly Discovered Anti-Cheat Patterns

When a review uncovers an anti-cheat or leakage pattern that is **not** already described in this skill, record it so future reviews catch it automatically. Append a single entry to the **Learned Anti-Cheat Patterns** log below — do not rewrite or reorder existing rules.

Rules for appending:
- **Novel only.** Confirm the pattern is not a rephrasing of an existing rule before adding it.
- **Generic only.** State the pattern abstractly: no task names, no specific values, no run-specific commentary. It must read as a rule that applies to any task.
- **One detection cue + one fix**, 1–3 lines, using the format below.
- **Dedupe** against existing entries.
- **Cap.** If the log exceeds ~40 entries, fold related entries into the main rules above (e.g. Sub-Agent 6 / 6 item 5) and prune the log.

### Learned Anti-Cheat Patterns (append-only log)

<!-- Format: - <pattern name> | detection cue | fix.  Generic only — no task names or values. -->

- Build-time write to the absolute `/tests/` path | Dockerfile or setup script writes to `/tests/...` (`>`, `cp`/`mv`, heredoc, `open(...,'w')`, base64-decode, `COPY x /tests/x`); the verify-time mount shadows it so it is baked into the agent image | relocate to committed source `tests/` or `tests/.truth/`, remove the build write, re-run the oracle.
- Anti-hardcode generator left agent-readable | a mutation/regeneration script (`mutate*`, `generate_mutated*`, `*_more`, `ref_updater*`) is readable by the agent, letting it learn the mutation scheme and hardcode post-mutation outputs | move the generator to a committed source path the agent cannot read at solve time.
- Verifier dependency hidden behind base64 | a verifier invokes a `/tests/` helper via a base64-encoded shell string, so a plain grep reports the baked file as unread | decode base64 (and resolve variable/relative paths) before concluding a baked file is dead.
- Non-exploitable bake with latent shadow failure | a file baked into `/tests/` has no committed source copy and no `rm`; it passes only via the baked copy and breaks once the source mount shadows `/tests/` | commit it under source `tests/` even when the leak itself is harmless.
- Grading artifact baked outside `/tests/` | a build step generates a truth/hash/expected file into another agent-visible path (`/root/...`, `/app/*truth*`, `/opt/**/golden*`, `*_hash.txt`) | scan all agent-visible paths for generated grading artifacts, not just `/tests/`.
- Multiple bakes in one task | a task writes more than one artifact into `/tests/` (e.g. a verifier script plus a fixtures dir, or a label plus a generator); fixing only the first-flagged path leaves a residual leak | enumerate and fix every build-time `/tests/` write in the task, not just the flagged one.
- Nondeterministic generator vs frozen truth | a data generator iterates an unordered `set`/`dict` upstream of RNG consumption, so the generated corpus (and any frozen truth derived from it) differs across platforms/hash-seeds; the committed truth then drifts from what the image produces and the oracle fails non-reproducibly | sort every unordered collection before it feeds the RNG, then re-extract and freeze truth from the built image; confirm identical output across `PYTHONHASHSEED` values.
- `__file__`-relative verifier truth read resolves wrong | a relocated truth read written as `open(os.path.join(os.path.dirname(__file__), '.truth/x'))` resolves to `/app` (the harness copies `test_outputs.py` to `/app` before running), not `/tests`, so it FileNotFounds at verify time | read relocated truth via the absolute verify-time mount path `/tests/.truth/<name>`, not a `__file__`-relative path.
- Not-a-leak: hash of the agent's own required output | the verifier stores a preimage-resistant digest (sha256) of the artifact the agent must itself produce; knowing the digest gives no way to reconstruct the content | treat as non-exploitable, leave it (relocating is optional hygiene only).
- Not-a-leak: anti-cheat consistency-hook hash | a hash of an agent-visible source file consumed by an agent-side bootstrap hook that reverts unauthorized edits (the verifier never reads it) | defeating the hook is the intended task; do not "fix" it — that destroys the eval signal.
- Verifier truth derived from an agent-writable input | when fixing a baked-truth leak, the verifier is changed to re-derive the expected value at verify time from an input path the agent can write (e.g. the seeded manifest the agent edits), so a tampered-but-self-consistent input moves the "truth" to match the agent's output | re-derive truth only from IMMUTABLE seeded state the agent cannot alter (the raw data files on disk, or a committed verifier-only `tests/.truth/` copy) — never from an agent-writable path.
- Non-idempotent cached attribute on a stubbed type | a determinism shim (e.g. a fake `uuid.uuid4()` whose `.hex` recomputes `getrandbits` on every access) advances global RNG per read and returns different values, desyncing generated data from frozen truth if any path reads it twice | compute the value once in `__init__` and cache it so the attribute is stable; keep RNG consumption identical to the original call pattern.
- Flaky preemption/partial-progress test: forking poll loses the race | a harness that backgrounds a pipeline and polls a progress file with `tr|grep|awk` (forking ~ms/iteration) to SIGTERM mid-run can be slower than the whole pipeline on a fast host, jumping past the partial window to a completed run ("expected partial, got <full count>") | make the poll fork-free (bash `$(<file)` + `[[ =~ ]]`, microsecond sampling) so it reliably catches the first unit of progress before completion; escalate to event-driven `inotifywait` if even that races.
- Flaky test: bash job-control message leaks into captured output | a harness backgrounds then `kill`s a process and the verifier asserts exact-match on captured stdout+stderr; bash asynchronously prints a `Killed` job-control notice that intermittently lands in the capture | `set +m` (disable monitor mode), run the killable child via `setsid`, and wrap `kill`/`wait` in a stderr-suppressed block so the notice never reaches the captured output.
- Flaky test: unlocked read-modify-write lost update | a concurrent harness/worker reads a shared count/file without holding the lock and observes a transient zero/empty state (between create/truncate and write), dropping an increment → intermittent off-by-one | acquire the exclusive lock BEFORE the read-modify-write and only act once the locked state is committed; retry under lock rather than reading optimistically.