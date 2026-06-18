# Checklist Coverage Map

How each dimension of `qc-checklist.md` (and the `qc-gate-reference.md` gates,
derived from `client-feedback.md`) is implemented in this skill. "Determinism"
notes whether the check is a script (deterministic) or sub-agent judgement.

| Checklist dimension | Implemented by | Layer | Deterministic |
|---|---|---|---|
| 1. Metadata (`task.toml`) | `check_metadata.py` | 0 | yes |
| 2. Dockerfile / environment | `check_structure.py` (shape) + `check_leakage.py` (`dockerfile-copies-*`) | 0/1 | yes |
| 3. Instructions | semantic sub-agent (`semantic-review-prompt.md`) | 3 | no |
| 4. Tests | semantic sub-agent + `behavioral_gates.py` (no-op proves tests require the fix) | 2/3 | partial |
| 5. Solution | `behavioral_gates.py` (oracle=1) + semantic sub-agent (hardcoding/approach) | 2/3 | partial |
| 6. Anti-cheat & global | `check_leakage.py` (bakes/leaks) + semantic cheat-trace + adversarial pass | 1/2/3 | partial |
| 7. Test brittleness | semantic sub-agent (brittle/weak adjudication, FP/DT rules) | 3 | no |
| Structure / files present | `check_structure.py` | 0 | yes |

## qc-gate-reference gates → component

| Gate | Component |
|---|---|
| Gate 0 run discipline (3× logged, Docker+Modal) | `behavioral-runbook.md` + `behavioral_gates.py --runs 3 --env {modal,docker}` |
| Gate 1 verifier integrity (no-op=0, oracle=1, reward-iso, adversarial, fair, no over-constraint, 2-way coverage) | `behavioral_gates.py` (no-op/oracle) + runbook (reward-iso, adversarial) + semantic sub-agent (fairness, over-constraint, coverage) |
| Gate 2 anti-cheat / leakage (tests/solution not in image, no hints, post-setup cleanup) | `check_leakage.py` + semantic cheat-trace |
| Gate 3 instruction–reward alignment | semantic sub-agent (bidirectional alignment) |
| Gate 4 environment & reproducibility (no ENTRYPOINT, resource caps, determinism, offline) | `check_metadata.py` (resource caps) + `behavioral_gates.py` (flaky/oracle-flaky) + runbook (ENTRYPOINT/CMD, offline) |
| Gate 5 content quality & metadata | `check_metadata.py` + semantic hygiene |
| Gate 6 dataset-level (decontam, overlap, near-dup, diversity, difficulty) | `decontaminate.py` (contam + near-dup) + `defect-distribution.md` |
| Gate 7 eval & reporting | out of QC scope (delivery/eval pipeline) |

## Client-feedback → concrete check

- **MAI no-op (29 tasks)** → `behavioral_gates.py` no-op=0 gate.
- **MAI ENTRYPOINT/CMD + 1-CPU/4-GB caps** → `check_metadata.py` `cpus-above-client-cap` / `memory-above-client-cap`; runbook ENTRYPOINT rule; Docker-env run.
- **GDM oracle validation (27/220 broken, 12 invisible to static)** → `behavioral_gates.py` oracle=1 gate (the decisive layer static can't replace).
- **Reflection brittle/over-constrained verifiers** → semantic brittleness adjudication (Layer 3) + adversarial pass.
- **Reflection setup-cleanup leak / ground-truth left behind** → `check_leakage.py` truth-bake detectors (`truth-baked-verifier-reads`, `tests-bake-verifier-reads`).
- **NVIDIA/Reflection decontamination** → `decontaminate.py` vs the public TB corpus.
- **GDM 69 cross-delivery overlaps** → `decontaminate.py` near-duplicate mode.
