# Terminal-Bench task defects — prevention guide for the generation pipeline

**Audience:** the team that owns the task-generation pipeline (top-of-funnel).
**Purpose:** every defect class below was found and fixed during a full-corpus QC pass
(~5,900 tasks triaged across 4 defect buckets). For each, we give the **root cause**, a
**real example** from the fixes, the **generation-time guardrail** that would have stopped
it entering the corpus, and the **automated gate** that catches it if it slips through.

> **Headline finding first:** across all four "defective" buckets, **~85% of the
> QC-flagged tasks were false positives** — the tasks were actually fine. The real defect
> rate was small. This has two implications for the pipeline:
> 1. The upstream *detectors* that produced these labels over-flag badly (static
>    heuristics that don't build the image). Don't gate generation on them alone.
> 2. The only signals that were reliable were **behavioral, on a freshly-built image**
>    (see "The one gate that matters"). Bake those into the pipeline instead.

---

## The one gate that matters (add this to the pipeline)

Two checks on the **actual built Docker image**, run per task before it enters the corpus.
They caught every real defect and produced almost no false positives:

1. **Oracle gate (fresh container):** build the image → run `solution/solve.sh` → run the
   verifier. **Must score 1.0.** Run it in a *fresh* container that has NOT run the tests
   first (see pitfall below). A fail = broken/unsound task.
2. **No-op gate:** build the image → run the verifier on the **untouched** container.
   **Must score 0.** A pass = the verifier is vacuous/gameable.

Two pitfalls we hit building this gate — avoid them:
- **Don't run no-op → solve → verify in one container.** Test suites that mutate state
  during the no-op leg poison the oracle leg → false "broken oracle." Use a **fresh
  container per trial**. (This single bug made ~97% of the "broken oracle" bucket look
  broken when it wasn't.)
- **Native arch matters.** Emulated amd64 on Apple Silicon *false-passed* a genuinely
  broken task. Gate on the target arch (we used Modal, native amd64).

Everything below is a *specific* rule that makes those two gates pass by construction.

---

## Defect class 1 — Leaked answer in the image (reward-hack)

**Symptom:** the agent can read the expected answer without doing the work.

**Root cause (the single dominant one):** a build/setup script writes the graded answer
into **agent-readable space** (`/app`, `/data`, `/opt`, …) and leaves it there. The harness
mounts `tests/` and `solution/` **only at grade time** — they are NOT in the image the agent
explores — but `environment/` and anything a build script writes **is** in the image.

**Real examples fixed:**
- `maritime-stream-audit`: `data_generator.py` wrote the ground-truth sidecar
  `/data/…/*.truth.json` (exact expected counts + violation IDs) into the image.
- `compliance-batch-verification`: the answer-key scripts `verify_*.py` were `COPY`'d into
  agent-readable `/app/tests/`.
- Recurring filenames: `*.truth.json`, `expected_*.json`, `golden_*.parquet`,
  `verify_*.py`, `corpus_truth.json`, `expected_ledger.txt`.

**Fix pattern:** move the answer into `tests/` (grade-time mount) or regenerate it
deterministically at grade time in `tests/test.sh`; delete it from agent-readable space.

**➜ Gen-pipeline guardrail:**
1. **Hard rule:** no expected-output/answer/oracle/verifier artifact may live under any
   path that survives into the image. Generators write answers to `tests/` only.
2. **Build-aware leak scan (automatable):** after building the image, in the running
   container, search agent-visible dirs (exclude `/tests`, `/solution`, and library dirs
   like `site-packages`/`venv`/`.julia`) for (a) the verifier's distinctive expected
   string/number tokens and (b) answer-signature filenames (`*truth*`, `*expected*`,
   `golden_*`, `verify_*`). A hit blocks the task.
   *Caveat:* values legitimately *derived from input*, genuine input files, and
   instruction-declared worked examples are NOT leaks — this scan flags candidates for a
   quick human/LLM confirm, it is not a hard reject on its own (that's why our first
   static grep over-flagged ~85%).

---

## Defect class 2 — Gameable / vacuous verifier

**Symptom:** a no-op or trivial solution passes.

**Root cause:** the verifier only checks that a file exists, that a command runs, or
asserts something the instruction doesn't actually require — so "no work" scores ≥ pass.

**Fix pattern:** add the substantive assertions the instruction implies; never by trivial
means.

**➜ Gen-pipeline guardrail:**
- **No-op gate (above)** is the definitive check: empty container must score 0.
- **Coverage requirement:** the generator should emit, per task, a mapping of each
  instruction requirement → the assertion(s) that check it. Missing mappings = coverage gap.

---

## Defect class 3 — Broken oracle (reference fails its own tests)

**Symptom:** `solution/solve.sh` does not pass the task's verifier.

**Root causes we actually saw (all preventable at gen time):**
| Root cause | Real example | Prevention |
|---|---|---|
| **Time-bomb fixture** — a hardcoded date that is now in the past | `rail-yard-handoff-compiler`: cert `hazmat_cert_exp=2026-06-15` → expired → oracle marks a violation the test doesn't expect | Ban absolute dates in fixtures; generate relative to build time |
| **Memory limit + glibc arenas** — verifier runs solver under `ulimit -v 2GB`; DuckDB/glibc reserve >2 GB *virtual* memory | `hpc-ledger-reconciler`: `reconcile.py` OOMs; fixed with `mallopt(M_ARENA_MAX,1)` + `threads=1` | If a test imposes `ulimit -v`, the reference must set `MALLOC_ARENA_MAX`/thread caps; gen template should include it |
| **Service lifecycle mismatch** — solve sets state in a service the grader restarts fresh | `sat-compaction-profiler`: solve sets `max_txid` in a default redis; `test.sh` starts its own redis from `/etc/redis/redis.conf` → value gone | Solve and test must agree on service startup; persist (`SAVE`) or use the same config |
| **Missing runtime dir** — solve references a dir it never creates | `hpc-ledger-reconciler`: DuckDB `temp_directory` never `mkdir`'d | `mkdir -p` before use; lint solve.sh for referenced-but-uncreated paths |
| **Non-reentrant / order-dependent tests** (also causes flakes) | several: mutate state then later assert initial state | see class 4 |

**➜ Gen-pipeline guardrail:** the **oracle gate (fresh container, native arch)** above.
It is the single highest-value addition — it would have caught 100% of these.

---

## Defect class 4 — Semantic verifier defects

**Symptom:** the verifier is unsound even though the task builds and the oracle "mostly"
passes.

**Sub-types fixed, with prevention:**
- **wrong-expected-value** — test asserts a value the correct solution doesn't produce.
  *Prevent:* derive expected values from the reference solution's actual output at gen
  time, never hand-author them.
- **nondeterministic-verifier** — pass/fail depends on wall-clock, dict/set **ordering**,
  unseeded randomness, or concurrency. *Prevent:* require deterministic verifiers — sort
  before compare, pin seeds, no timing asserts; run the verifier **twice** at gen time and
  reject if results differ; run tests in **randomized order** (e.g. `pytest-randomly`) and
  require pass either way.
- **coverage-gap** — verifier omits a requirement the instruction states. *Prevent:*
  instruction→assertion mapping (class 2).
- **instruction-test-mismatch** — test checks something the instruction doesn't ask.
  *Prevent:* same mapping, reviewed against the instruction as source of truth.
- **bad-tolerance** — numeric tolerance too tight/loose. *Prevent:* tolerances must be
  justified against expected numerical error, not guessed.

---

## Defect class 5 — Build failures (Dockerfile hygiene)

**Symptom:** the image doesn't build (or builds nondeterministically).

**Root causes we saw:**
- **`RUN /path/script.sh` on a non-executable COPY'd script** → exit 126. `COPY` preserves
  the source mode; a 644 script isn't executable. Seen in ~22 tasks. *Prevent:* generate
  `RUN bash /path/script.sh` (never bare `RUN /script.sh`); or `chmod +x` in the Dockerfile.
- **Network-dependent builds** — `wget`/`npm`/`maven` fetching remote artifacts at build
  time → flaky/unbuildable offline. *Prevent:* vendor build inputs; no network in build.
- **Missing `COPY` source** — Dockerfile copies a file not in the build context.
  *Prevent:* lint every `COPY` source exists.
- **UTF-8/encoding assumptions** — (this one bit our *tooling*, not tasks) don't byte-slice
  logs; decode leniently.

**➜ Gen-pipeline guardrail:** a Dockerfile linter (no bare `RUN /*.sh`, all `COPY` sources
exist, no network fetches in build) + the build must succeed on the **target arch**.

---

## Recommended top-of-funnel changes (priority order)

1. **Add the two behavioral gates** (oracle=1 on fresh container, no-op=0), on the **target
   arch**, as a hard pre-corpus gate. Highest ROI — catches classes 2, 3, and most of 5.
2. **Enforce the `image` vs `tests/` boundary** in generation: answers/verifiers/generators
   that emit answers live only in `tests/`; add the build-aware leak scan (class 1).
3. **Require deterministic verifiers:** double-run + randomized-order test at gen time;
   ban wall-clock/ordering/unseeded-random asserts (class 4).
4. **Dockerfile linter:** `RUN bash …`, `COPY` sources exist, no build-time network,
   create referenced dirs, set `MALLOC_ARENA_MAX` when a test imposes `ulimit -v` (class 5, 3).
5. **Ban time-bomb fixtures:** no absolute past dates; generate relative to build (class 3).
6. **Instruction→assertion coverage manifest** emitted per task and reviewed (class 2, 4).
7. **Do NOT gate on the old static detectors alone** — they produced ~85% false positives.
   Use them only to *prioritize* the behavioral gates, and always confirm on a built image.

---

## Appendix — this cycle's numbers (evidence base)

| Bucket (as labeled) | Flagged | Real defects fixed | Confirmed fine (false pos.) | Culled (unfixable) |
|---|---|---|---|---|
| broken-oracle | 2,509 | 21 | ~2,462 | 2 |
| reward-hack-leak | 1,471 | ~200 | ~1,198 | 30 |
| brittle-verifier | 593 | 124 | 449 | 1 |
| semantic-defect | 678 | 89 | ~573 | 1 |
| **Total** | **~5,250** | **~434** | **~4,680** | **~34** |

Every "real defect fixed" was verified on a freshly-built image, native arch. The large
"confirmed fine" columns are why recommendation #7 matters: the labels themselves were the
biggest defect.
