# Reflection-35k — Open Issues

*After PR #8. Set = 1,054 tasks.*

## P0 — Blocking

**1. Difficulty not measured**
- 1,038 / 1,054 still `avg_at_8 = 0.0` (placeholder). Eval never run on the original 1,000.
- Fix: run avg@8 (8× on Opus-4.8 or GPT-5.4 + Terminus-2), drop > 0.5, write real scores.
- *Reflection → Difficulty: avg@8 ≤ 0.5. Metadata: no placeholders.*

**2. `task_fc601249` leak**
- Build bakes the `/tests/mutated_input` grading corpus into the agent image; agent can read the test inputs.
- Fix: generate `mutated_input` in `test.sh` at verify time, not in the build.
- *Reflection → Security: no exposed ground truth.*

## P1

**3. Base images not digest-pinned** (0 / 1,054)
- Tasks use moving tags (`FROM ubuntu:24.04`) → the image can change over time, breaking reproducibility.
- Fix: pin to the approved hash, e.g. `FROM public.ecr.aws/docker/library/ubuntu:24.04@sha256:0d39…`.
- Catch: spec pins one version per language (Python = 3.13), so `python:3.11` tasks may need re-testing on the approved image.
- *Reflection → Dockerfile: always pin FROM by digest; only the 10 listed refs approved.*

**4. Diversity floors** (6 categories under 5%)
- file-media 4.9 · systems 3.8 · build-dep 2.9 · debugging 1.8 · data-querying 1.1 · hardware 0.1
- Fix: bigger OTS backfill into these; **drop Hardware** (no OTS supply).
- *Reflection → Diversity: every category 5–20%.*

## P2 — hygiene
- Unpinned pip · broad `chmod -R` · verifier deps in agent image · `solve.sh` heredocs.

---
**Order:** difficulty eval → `fc601249` → digest-pin → backfill.
