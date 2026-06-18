# Terminal-Bench QC — What This Review Checks For

_The quality review evaluates each task across 6 core dimensions plus a test-brittleness axis. Every dimension gets a verdict: **PASS** (clean / trivial cosmetic), **WARN** (minor, non-blocking), or **FAIL** (critical, must fix). A task's overall verdict is the worst of its dimensions._

---

## 1. Metadata (`task.toml`)
- **Required fields present & non-empty:** difficulty, category, tags, expert + junior time estimates, verifier timeout, agent timeout, and at least one env resource (cpus / memory / storage).
- **Category** is specific and matches the task (not generic like "programming").
- **Tags** are specific but not absurdly narrow; no overly broad tags ("general", "code"), and they relate to the actual task.
- **Sanity:** junior_time ≥ expert_time > 0; timeouts positive; agent_timeout ≥ verifier_timeout.

## 2. Dockerfile / Environment
- **No solution leakage** — no `COPY`/`ADD`/`RUN` brings `solution/` files into the agent environment.
- **No test leakage** — `test.sh`, `test_outputs.py`, and `tests/` are not baked into the image; no answers embedded in `data/` or setup scripts.
- **Test-only deps not baked in** — pytest / assertion libs belong in `test.sh`, not the Dockerfile.
- **General quality** — base image specified, not empty/trivial for a task that needs deps, no syntax errors.

## 3. Instructions (`instruction.md`)
- **Testable requirements** — every stated/implied requirement maps to a test.
- **Explicit data schemas** — any file/DB/format output has its schema defined; the agent never has to guess.
- **Explicit file references** — paths to read/write are named in the instructions, not only discoverable from tests.
- **Spelling / grammar / markdown / LaTeX** are clean.
- **Clarity** — no major ambiguity a competent developer would have to guess about.

## 4. Tests (`test.sh` + test files)
- **No phantom tests** — nothing checks behavior the instructions never state or imply.
- **Full coverage** — every stated requirement has ≥ 1 test.
- **Test quality** — clear names, reasonable structure, sound assertions.
- **Dependencies pinned** — versions pinned in `test.sh` (e.g. `pytest==9.0.3`).
- **Reliability** — no flakiness from fixed-`sleep` timing, unmocked network, unseeded randomness, or race conditions.

## 5. Solution (`solution/solve.sh`)
- **Correctness** — satisfies the instructions and would pass all tests.
- **No hardcoding** — implements real logic; doesn't return literals matching test expectations, read test files, or use lookahead bias.
- **Reasonable approach** — what a competent developer would write, not a shortcut that only works because of test structure.

## 6. Anti-Cheat & Global
- **Tests & solution not accessible at agent runtime** — no ground-truth files left world-readable, no truth baked into the image the agent can read.
- **Not trivially searchable** — if internet is allowed, the task isn't a famous/textbook problem solvable by search.
- **Cross-component consistency** — instructions, tests, and solution agree on I/O formats, file paths, and expected behavior.
- **Environment adequacy** — resources (cpu/memory/gpu) are sufficient for the task.

---

## 7. Test Brittleness (merged-in axis)
Beyond coverage, each verifier *check* is judged for over-fitting. Two opposite failure modes:

- **Brittle (false-reject)** — the check rejects a *correct* solution because it asserts **how** code is built, not **what** it produces. Common forms:
  - **source-code grep** — greps the solution for function names, libraries, or tokens.
  - **exact-string / file-content match** — pins literal output strings, config keys, paths, or whitespace the spec never fixed.
  - **forbidden-literal / structural guards** — over-scoped bans, or file-count / directory-layout assertions.
- **Weak (false-accept)** — the check is too permissive and lets a *wrong* solution pass. Common forms:
  - asserts a substring in output but ignores the return code.
  - checks only the *format* of a value (e.g. hash shape) or bare file existence, not the actual value/content.
  - validates one pairwise case instead of the full mandated rule.

Each flagged check is adjudicated against the spec (does the instruction pin it?) and, for brittle checks, by constructing a concrete correct solution that would wrongly fail.

---

## Verdict roll-up
- **FAIL** if any dimension is FAIL (e.g. solution leak, untested requirement, phantom/hardcoded test, harness coupling that rejects correct layouts, or multiple brittle checks).
- **WARN** if any dimension is WARN and none FAIL.
- **PASS** only if all dimensions pass.

**Outputs:** `review-ssot.csv` (one row per task, per-dimension verdicts + `brittleness` rating + critical issues) and `review-ssot.md` (per-task findings with locations and fixes).
