# Terminal-Bench Task Generation and Cleanup

Framing: More clients are asking for Terminal-Bench. How do we generate enough tasks to hit delivery goals, and make sure they are high quality and similar to TB3?

Two threads: (A) generate more, (B) clean up what we have.

---

## 1. Client demand

- Clients asking: Reflection (committed), xAI, Google, ByteDance (incoming)
- Reflection ramp alone: 1k to 35k cumulative by Aug 26, 5.5k/wk peak
- Total estimate: 50k to 100k tasks
- Each client has its own difficulty goals. Examples:
  - Reflection: every task must have average@8 <= 0.5, measured with Opus 4.8 or GPT-5.4 (roughly equal on terminal tasks) using the Terminus-2 agent on Harbor. Settings: 128k max completion tokens, reasoning on (GPT-5.4 xhigh / Opus adaptive thinking, max effort). Cheap-model prefiltering is allowed (tasks Sonnet or Haiku solve easily are unlikely to be hard for Opus), but the final measurement must use Opus 4.8 or GPT-5.4.
  - Pass-rate window: pass in [1/8, 6/8] on v9, or 0/8 on v9 but >0 on Opus-4.8 xhigh (proven solvability)
  - Content filters: add-feature tasks, devops/infra/db/backend/auth knowledge, Go, patch touches 5+ files / 80+ relevant lines

## 2. Generation approach: shared pool with difficulty buckets

We do not generate per client. We generate at volume, measure every task, and each task lands in a difficulty bucket. Client specs then select from the buckets.

- Generate a large pool across categories, languages, and objectives
- Cheap prefilter first (Sonnet/Haiku pass@k) to remove easy tasks before spending frontier compute
- Measure the survivors: pass@8 with the frontier models and agents the specs name
- Every task ends up with a difficulty profile:
  - easy: solved by cheap models (dropped or reused as seeds)
  - mid band: 1/8 to 6/8 on target model
  - hard band: avg@8 <= 0.5 on Opus 4.8 / GPT-5.4
  - unsolved but proven solvable: 0/8 on target, >0 on Opus xhigh
  - unsolved and unproven: held until an oracle run or a stronger model proves solvability
- One task can satisfy several clients if it sits in the right bucket and passes their content filters

## 3. Expected yields

Generation output is never all usable. We plan around measured yields, not raw counts. Working assumptions (from our 13.4k-task history, to be re-validated in week 1):

| Stage | Yield | Of 10,000 raw |
|---|---|---|
| Raw generated | 100% | 10,000 |
| Survive AutoQC (quality) | ~65% | 6,500 |
| Survive cheap prefilter (not easy) | ~65% of QC pass | 4,200 |
| Land in mid band (1/8 to 6/8) | ~30% of QC pass | 2,000 |
| Land in hard band (avg@8 <= 0.5) | ~30% of QC pass | 2,000 |

- Rule of thumb: 1 raw task ≈ 0.2 deliverable hard tasks. To deliver 5,500 hard tasks in a week we need roughly 28k raw.
- The mid band is not waste: sizing generation for Reflection's hard band automatically produces a similar-sized mid-band pool for the pass-rate-window clients.
- QC failures are not all waste either: the eng-fix loop has recovered defective tasks before (1,083 audited, fixes were genuine). Recoverable buckets go back through QC.

## 4. Weekly output plan

One shared pool, one weekly raw number. Clients draw from the pool; nothing is generated for a specific client. Reflection is simply the largest committed draw, so we size the pool at 25 to 30% above what its draw requires. Raw generation runs 2 weeks ahead of delivery because QC plus measurement takes time.

| Delivery week | Total raw gen | Hard band out (~20%) | Reflection draw | Hard left for others | Mid band out (~20%) |
|---|---|---|---|---|---|
| Jul 1 | 6,500 | 1,300 | 1,000 | 300 | 1,300 |
| Jul 8 | 16,000 | 3,200 | 2,500 | 700 | 3,200 |
| Jul 15 | 22,500 | 4,500 | 3,500 | 1,000 | 4,500 |
| Jul 22 | 26,000 | 5,200 | 4,000 | 1,200 | 5,200 |
| Jul 29 | 26,000 | 5,200 | 4,000 | 1,200 | 5,200 |
| Aug 5 | 29,000 | 5,800 | 4,500 | 1,300 | 5,800 |
| Aug 12 | 29,000 | 5,800 | 4,500 | 1,300 | 5,800 |
| Aug 19 | 35,500 | 7,100 | 5,500 | 1,600 | 7,100 |
| Aug 26 | 35,500 | 7,100 | 5,500 | 1,600 | 7,100 |

- Hard and mid columns use the funnel yields from section 3 (~20% of raw each)
- The headroom (hard left over plus the whole mid band) fills other clients' orders as they land, absorbs bad generation weeks, and builds the buffer
- Hold a 1 to 2 week inventory buffer of measured, ready-to-ship tasks per bucket so a bad week does not miss a delivery
- Weekly rhythm: generate and QC continuously; measure in daily batches; route and diversity-balance at week end; deliver on the ramp dates
- Track weekly: raw generated, QC yield, bucket distribution, buffer depth per bucket, delivered per client. Yield drift is the early warning that the generator or QC needs attention.

## 5. Quality target: TB3 comparison

What TB3 does that we should match. All tasks use the same Harbor format; the differences are in 3 files:

| Part | TB3 | Ours today |
|---|---|---|
| Prompt | Reads like a real job, requires judgment, anti-cheat line included | Long generated spec |
| Golden solution | Reference implementation plus an oracle that computes ground truth | One large generated solve.sh |
| Verifier | Written per task, hidden fixtures, sensible tolerances, no LLM judge | Generic reused pytest, exact match, nothing hidden |

- "Similar to TB3" in practice means: oracle-backed golden solutions, hardened verifiers, prompts that require judgment

## 6. Current task inventory

- ~13.4k tasks QC'd, ~35% had a defect
- Buckets: broken oracle 2,510 / reward-hack or leak 775 / brittle verifier 653 / semantic 776 / healthy-hard 2,787
- Already fixed: conftest hole in ~9,011 tasks, patched with --noconftest
- Still to fix: allow_internet=true everywhere, unpinned base images, missing environment_mode isolation, opaque task names
- The cleanup recovers ~4.7k defective tasks and brings the rest up to client specs
- The 2,787 healthy-hard tasks are immediate Reflection inventory and cover the first weeks of the ramp

## 7. Pipeline steps

spec > synth-gen > AutoQC > calibrate > audit > ship

1. Generation targets: categories, languages, objectives, volume
2. Synth-gen: generate the full Harbor package (prompt, environment, solution, tests)
3. AutoQC gates: static checks, LLM semantic review, behavioral run (oracle scores 1, no-op scores 0, planted reward scores 0)
4. Difficulty measurement: cheap prefilter, then pass@8 on the frontier models the specs name; assign bucket
5. Trajectory audit: recheck real attempts for unfair fails and cheated passes
6. Route and deliver: match buckets and content filters to each client, balance diversity, ship batches

## 8. Generator improvements

Upgrade the generator so quality is built in rather than filtered in afterwards:

- Golden solutions: emit a reference implementation with an oracle, not one big solve.sh
- Verifiers: per-task tests with hidden fixtures and tolerances, --noconftest and environment_mode = "separate" from day one
- Prompts: add a time budget and anti-cheat line, reduce over-specification where the spec allows judgment
- Metadata: pinned images, allow_internet=false by default, meaningful names, difficulty/solution/verification explanations
- Every point of yield gained here cuts raw generation volume: 65% to 80% QC yield cuts the raw requirement by roughly a fifth

## 9. Next steps and asks

- Cleanup track: finish conftest rollout, patch config gaps, re-verify the 4.7k defective bucket, ship the 2,787 healthy-hard as early Reflection inventory
- Generation track: generator upgrades (section 8), scale the pool, hold the 2-week generation lead
- Asks and risks:
  - Difficulty measurement compute: pass@8 with frontier models across the whole pool is the biggest cost line; the cheap prefilter is how we contain it
  - Per-client orchestrators in Studio (we hit this with GLM-5.2; we need v9 equivalents to measure against client models)
  - Diversity gaps: debug/fix/refactor objectives are under-represented (tracker shows most need +50 to 100 tasks each)
  - Yield assumptions in section 3 are from the old generator; week 1 and 2 measurements set the real planning numbers
