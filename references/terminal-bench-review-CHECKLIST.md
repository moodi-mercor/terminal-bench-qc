# Terminal-Bench Review — Detailed Checklists

Complete checklists for each review category. Use these when you need full detail on specific checks.

## Sub-Agent 1: Metadata Review (`task.toml`)

Read `task.toml` and check:

### Required Fields Present

All of the following must exist (values must be non-empty):
- `difficulty`
- `category`
- `tags`
- `expert_time_estimate` (or equivalent key like `expert_time`)
- `junior_time_estimate` (or equivalent key like `junior_time`)
- `verifier_timeout`
- `agent_timeout`
- Environment requirements (e.g., `memory`, `storage`, `cpus` — at least one must be specified)

### Category Validity

The category should be a reasonable, specific label that matches the task described in `instruction.md`. Flag if it's overly generic (e.g., just "programming") or seemingly mismatched with the task content.

### Tags Quality

Tags should be specific but not absurdly narrow. Flag if:
- Any tag is overly broad (e.g., "general", "code", "task")
- There are no tags
- Tags don't relate to the actual task content

### Time Estimates Sanity

The `expert_time_estimate_min` and `junior_time_estimate_min` fields are in **minutes** (the `_min` suffix means minutes, not "minimum"). `junior_time_estimate` should be >= `expert_time_estimate`. Both should be positive. Flag if expert time is 0 or if junior time is less than expert time.

**Time-difficulty alignment (TB2 reference ranges):**
- `easy`: expert 5-60 min, junior 20-120 min
- `medium`: expert 5-180 min, junior 10-480 min
- `hard`: expert 300-480 min, junior 600-19200 min

Flag as FAIL if time estimates fall outside the range for the declared difficulty. Common anti-pattern: values written as if they were seconds (e.g., expert=1200 for an easy task = 20 hours, when it should be ~10 min). If all values in a batch are 60x too high, they were likely recorded in seconds instead of minutes.

### Timeouts Sanity

`verifier_timeout` and `agent_timeout` should be positive integers. `agent_timeout` should generally be >= `verifier_timeout`.

---

## Sub-Agent 2: Dockerfile Review

Read the `Dockerfile` and cross-reference with the full task directory structure:

### No Solution Leakage

Scan all `COPY`, `ADD`, and `RUN` commands. Verify that:
- Solution files (from `solution/` or any file clearly containing a reference solution) are NOT copied into the agent's working environment. Note: in harbor-based tasks, `solution/` and `tests/` are mounted at verification time only — they should never appear in the Dockerfile.
- Test files (`test.sh`, test directories, unit test files) are NOT copied into the agent's working environment.
- Also check `data/` directory contents — flag if any file in `data/` appears to contain solutions or test answers.
- Watch for `COPY data/ /app/` (wildcard copies) — these pull in everything under `data/`, including any subdirectories like `expected_output/` that shouldn't be agent-visible. Prefer explicit `COPY data/logs/ /app/logs/` style copies.

### No Implementation Hints in Agent-Visible Files

For every file the Dockerfile copies into the container, verify it does not contain:
- Step-by-step solution guides or numbered "recommended approach" sections
- Exact field indices, column numbers, or awk/grep one-liners that solve the parsing problem
- Named entities (specific usernames, IPs, account names) that the agent should discover from the data
- Exact output format prescriptions beyond what's in `instruction.md`
- "Common issues" or debugging guides that reveal what mistakes to avoid (and thus what the solution looks like)
- Any `PREVIOUS ATTEMPTS` or similar sections that reveal the solution approach

Reference files (e.g., threshold configs, pattern lists) are fine — they define *what* the task needs, not *how* to implement it. The line to flag is when a file describes implementation details an agent should figure out themselves.

Pay special attention to documentation-style files (markdown, text) copied into the container — these are highest risk for containing complete solution paths disguised as "reference docs". For each such file, verify it contains ONLY policy contracts (what rules apply) and NOT implementation guidance (how to implement them, exact config values to set, step-by-step procedures).

### Test Dependencies

**pytest and standard testing frameworks installed in the Dockerfile are acceptable and should NOT be flagged.** Many tasks legitimately include pytest in the image because the task's own CI/test suite (distinct from the harbor verifier) uses it. Only flag test-only dependencies if they are clearly exotic or heavyweight libraries installed solely for the verifier (not the task's own test infrastructure).

### General Dockerfile Quality

Flag if:
- No base image is specified
- The Dockerfile is empty or trivially minimal for a task that clearly needs dependencies
- Obvious syntax errors exist

---

## Sub-Agent 3: Problem Statement Review (`instruction.md`)

Read `instruction.md` thoroughly:

### Testable Requirements

Extract every requirement, constraint, and expected behavior mentioned or strongly implied. For each, note whether it appears to be tested (cross-reference with test files). Flag any requirement that has no corresponding test.

### Data Schemas Explicit

If the task requires writing data to files, databases, or specific formats, the expected schema/format MUST be explicitly defined in the instructions. Flag if the agent would have to guess the schema.

### File References — Discoverability Over Explicitness

File paths, config values, service ports, passwords, and other specific values do NOT need to be spelled out in the instructions if they are **discoverable** from the container filesystem (config files, scripts, logs, error messages, source code, database contents). The agent is expected to explore the codebase. Only flag if a required file/path/value is truly undiscoverable — i.e., it exists nowhere in the agent-visible environment and cannot be inferred from available files. Common discoverable patterns that should NOT be flagged:
- Config values in `.json`, `.yaml`, `.toml`, `.conf`, `.env` files
- File paths referenced in scripts, systemd units, or config files
- Database schemas visible via `sqlite3 .schema`, `\d` in psql, or init SQL files
- Service ports in config files or startup scripts
- Passwords/secrets in config files, environment variables, or setup scripts
- Error conditions reproducible by running the broken service

### Hard Requirements Use Hard Language

Any behavior that is enforced by a test must be stated as a hard requirement ("must", "must not", "required") in the instructions. Flag if tested behavior is described with soft language like "likely", "should consider", "may want to" — agents may skip optional-sounding rules, causing failures that look like task defects but are actually instruction ambiguity.

### No Classification Leakage

Flag if the instructions list specific expected output values (e.g., `(HIT vs MISS/RefreshHit/Error)` in an enumeration) in a way that accidentally reveals how edge cases should be classified. The instructions should define the rule, not enumerate the answer.

### Spelling and Grammar

Flag any obvious spelling errors, grammatical issues, or malformed markdown/LaTeX. Be reasonable — minor style differences are fine, but confusing or incorrect language should be flagged.

### Clarity and Completeness

Flag if the problem statement is ambiguous enough that a competent developer would have to guess about major aspects of the task. Note: terse/vague instructions are acceptable when the agent can fill in details by exploring the environment. Only flag if the task goal itself is unclear.

### No Over-Specification (Difficulty Reduction)

The instruction should describe WHAT must be true (goals, output schemas, constraints) but NOT HOW to achieve it. Flag any of these patterns that reduce task difficulty:

- **Enumerated fix lists**: Listing every bug with its exact file, function, or line to fix. The instruction should state observable symptoms and let the agent discover root causes.
- **Step-by-step recipes**: Numbered procedures walking the agent through exact commands or fixes. State end-state requirements instead.
- **Exact bug-location references**: Telling the agent precisely which files contain bugs and what to change. The agent should discover buggy files through investigation. Exception: references to INPUT files the agent must read or OUTPUT files the agent must produce are fine.
- **Enumerated expected values**: Listing every expected decision or output value that the agent should derive from analysis.
- **Answer-key tables**: Tables mapping specific inputs to their exact expected outputs.

The instruction should make a competent developer think "I know what success looks like, now I need to investigate how to get there."

### Discoverability of Requirements

Every value or behavior that tests check MUST be either (a) stated in the instruction, or (b) discoverable from files present in the Docker container (source code, config files, tool output, existing data, running services, error messages). **Do NOT flag as "undocumented" if the value is discoverable by exploring the environment.** Only flag (as FAIL) if a test checks for a truly phantom value that appears nowhere the agent can find it and cannot be inferred from any available context. When reviewing, actually check the environment/ directory for the value before flagging.

---

## Sub-Agent 4: Tests Review

**Critical architectural note:** `tests/` and `solution/` directories are mounted by harbor at **verification time only**. The agent executes in a container built solely from `environment/Dockerfile` — it **cannot read test files or solution files during task execution**. This has important implications for what constitutes a "cheating vector":

- **Hardcoded values in test assertions** (forensic IPs, passwords, TOTP secrets, computed quantities): NOT a FAIL for anti-cheat. The agent cannot read these values. This is a **WARN** test quality concern (the oracle could trivially pass if it read tests, and the test over-constrains to specific values). Mark as WARN, not FAIL.
- **A real FAIL** requires the value to also appear in a file COPY'd into the container by the Dockerfile — i.e., agent-visible at runtime.

Read `test.sh` and all test files:

### No Phantom Tests

Every test should correspond to a requirement that is either (a) stated or clearly implied in `instruction.md`, OR (b) discoverable from the container environment (config files, source code, logs, scripts, database contents). A test is only a "phantom" if the checked value/behavior cannot be found anywhere the agent can look. Before flagging, verify the value isn't in any environment/ file. Common false positives: config values in .json/.yaml files, paths in scripts, schema in init SQL, passwords in setup files — these are all discoverable and should NOT be flagged.

### Full Coverage

Cross-reference with the requirements extracted from `instruction.md`. Every stated requirement should have at least one test.

### Test Quality

Check that:
- Tests have clear names or comments explaining what they verify
- Tests are reasonably structured (not a single giant test doing everything)
- Test logic is sound (no obvious bugs in test assertions)
- Expected values are derived dynamically from the container data rather than hardcoded (WARN if hardcoded — test quality concern)

### Dependencies Pinned

Any dependencies installed in `test.sh` should have pinned versions (e.g., `pip install pytest==7.4.0`, not just `pip install pytest`). Flag unpinned dependencies.

### Test Reliability

Flag if any tests appear flaky (e.g., depend on timing, network calls without mocking, random values without seeds, race conditions).

### Oracle-Compatible Agent-Rerun Tests

If tests re-run the agent's solution script (e.g., to verify it's not hardcoded), they must use `pytest.skip()` — not `assert` — when the solution file is absent. The oracle agent runs the reference solution directly without creating a solution script, so a hard `assert` will fail the oracle and produce a false 0.0 reward. Pattern to enforce:

```python
def _find_solution_script():
    candidate = Path("/app/solution.sh")
    if not candidate.exists():
        pytest.skip("solution script not found — skipping re-run test")
    return str(candidate)
```

---

## Sub-Agent 5: Solution Review

Read the reference solution and cross-reference with `instruction.md` and tests:

### Correctness

Does the solution follow the requirements in `instruction.md`? Would it pass all the tests? Flag any inconsistencies between the solution, the problem statement, and the tests.

### No Hardcoding

The solution should implement actual logic, not hardcode expected outputs. Flag if:
- The solution returns literal values that match test expectations without computation
- The solution reads test files to extract expected answers
- The solution exhibits lookahead bias (uses information only available from tests)

### Reasonable Approach

The solution should represent what a competent developer or agent would write. Flag if the solution uses an unreasonable shortcut that only works because of specific test structure.

---

## Sub-Agent 6: Anti-Cheat & Global Review

**Key architectural constraint:** In harbor tasks, `tests/` and `solution/` are **never** in the `environment/Dockerfile`. They are mounted at verification time only. The agent's container is built solely from `environment/Dockerfile`. Therefore:
- Hardcoded values in `tests/` files are **NOT** accessible to agents — this is a WARN (test quality), not a FAIL
- Hardcoded values in `solution/` files are **NOT** accessible to agents — this is a WARN (solution quality), not a FAIL
- **FAIL-level anti-cheat concern** = values that are accessible via Dockerfile COPY'd files in the container (e.g., a hint file in `data/`, a wildcard COPY that includes answer-key data)

Review all components holistically:

### No Cheating Vectors

Verify:
- Tests are not accessible to the agent at runtime (in harbor tasks, tests are mounted by the verifier at verification time — they must NOT appear in the Dockerfile)
- Solution is not accessible to the agent at runtime (in harbor tasks, `solution/` is oracle-only — must NOT appear in the Dockerfile)
- If internet access is permitted (check `task.toml` or Dockerfile), consider whether the task/solution could be trivially found via web search. Flag if this is a well-known problem (e.g., a famous LeetCode problem, a textbook exercise) that an agent could solve by searching.

### Agent-Visible Files Audit

For every file copied into the container by the Dockerfile, check for unintended hints:
- Step-by-step implementation guides masquerading as "notes" or "analysis" files
- Exact field/column indices that solve the parsing problem
- Named internal entities (specific service account names, IP ranges, expected output values) the agent should derive from data
- Any file named `analysis_notes.txt`, `notes.txt`, `hints.txt`, or similar — these are highest risk for over-hinting and deserve close scrutiny
- Reference documents are acceptable (thresholds, classification rules, format specs) as long as they define *what*, not *how*

### Cross-Component Consistency

Verify that `instruction.md`, tests, and solution all agree on:
- Input/output formats
- File paths and names
- Expected behavior and edge cases

### Environment Adequacy

Do the resource requirements in `task.toml` seem sufficient for the task? Flag if a task clearly needs GPU but none is specified, or if memory seems too low.
