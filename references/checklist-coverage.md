# Checklist Coverage Map

How each dimension of `qc-checklist.md` (and the `qc-gate-reference.md` gates,
derived from `client-feedback.md`) is implemented in this skill.

**Scope:** this skill covers the **static + semantic + dataset** QC pass. The
**behavioral** oracle/no-op runtime gate is run separately at the delivery stage
(harbor + Modal on the client's infra) and is intentionally out of scope here —
rows below mark it "delivery stage".

| Checklist dimension | Implemented by | Deterministic |
|---|---|---|
| 1. Metadata (`task.toml`) | `check_metadata.py` | yes |
| 2. Dockerfile / environment | `check_structure.py` (shape) + `check_leakage.py` (`dockerfile-copies-*`) | yes |
| 3. Instructions | semantic sub-agent (`semantic-review-prompt.md`) | no |
| 4. Tests | semantic sub-agent (coverage, phantom, brittle, weak) | no |
| 5. Solution | semantic sub-agent (hardcoding/approach vs spec) | no |
| 6. Anti-cheat & global | `check_leakage.py` (bakes/leaks) + semantic cheat-trace | partial |
| 7. Test brittleness | semantic sub-agent (brittle/weak adjudication, FP/DT rules) | no |
| Structure / files present | `check_structure.py` | yes |
| — Verifier actually requires the fix (no-op=0) / solve path works (oracle=1) | **delivery stage** (oracle/no-op gate) | — |

## qc-gate-reference gates → component

| Gate | Component |
|---|---|
| Gate 0 run discipline (3× logged, Docker+Modal) | **delivery stage** |
| Gate 1 verifier integrity — no-op=0, oracle=1, reward-iso, adversarial | **delivery stage** |
| Gate 1 verifier integrity — fairness, no over-constraint, 2-way coverage | semantic sub-agent (Layer 2) |
| Gate 2 anti-cheat / leakage (tests/solution not in image, no hints, post-setup cleanup) | `check_leakage.py` + semantic cheat-trace |
| Gate 3 instruction–reward alignment | semantic sub-agent |
| Gate 4 environment & reproducibility — resource caps | `check_metadata.py` (`cpus/memory-above-client-cap`) |
| Gate 4 environment & reproducibility — ENTRYPOINT/CMD, determinism, offline | **delivery stage** |
| Gate 5 content quality & metadata | `check_metadata.py` + semantic hygiene |
| Gate 6 dataset-level (decontam, overlap, near-dup, diversity, difficulty) | `decontaminate.py` + `defect-distribution.md` |
| Gate 7 eval & reporting | out of QC scope |

## Client-feedback → concrete check

- **MAI no-op (29 tasks)** → delivery-stage no-op=0 gate.
- **MAI 1-CPU/4-GB caps** → `check_metadata.py` `cpus-/memory-above-client-cap`.
- **GDM oracle validation (27/220 broken)** → delivery-stage oracle=1 gate.
- **Reflection brittle/over-constrained verifiers** → semantic brittleness adjudication (Layer 2).
- **Reflection setup-cleanup leak / ground-truth left behind** → `check_leakage.py` truth-bake detectors (`truth-baked-verifier-reads`, `tests-bake-verifier-reads`).
- **NVIDIA/Reflection decontamination** → `decontaminate.py` vs the public TB corpus.
- **GDM 69 cross-delivery overlaps** → `decontaminate.py` near-duplicate mode.
