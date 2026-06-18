# Terminal Bench OTS — Pre-Delivery QC Gate (Reference)

A standing checklist of QC checks to run **before every delivery**, derived from defects clients have actually caught. Each check notes *what it catches* and *who caught it the hard way* so the rationale stays attached to the rule.

**How to read the "Source" column:** the client/incident that exposed the gap. If we'd run the check, that escalation wouldn't have happened.

> **The single most important gate:** run the reference solution through its own verifier (oracle), and run the verifier against an untouched container (no-op / null-agent). Almost every escalation across NVIDIA, MAI, GDM, and Reflection traces to one of these two not being run, or not being run consistently with saved logs.

> **Repeat-offense warning:** Reflection flagged quality as "once again disappointing" *after* a re-QC'd 100-task delivery — with brittle/over-constrained verifiers as their largest issue and ground-truth-left-by-setup as a second. The skills in use were the same ones used for NVIDIA and Cognition. Takeaway: **the gap is enforcement and trajectory-level inspection, not the existence of a checklist.** Static checks alone missed these; the fixes below lean on reading agent trajectories and adversarial sub-agent probing.

---

## Gate 0 — Run discipline (process, not a per-task check)

| Check | Pass criteria | Source |
|---|---|---|
| Oracle + no-op run **3x with saved logs** | Logs stored for every delivery; no delivery without them | MAI, GDM (defects slipped past one-off runs) |
| Run on **both Docker and Modal** (or client's target infra) | Task passes on the environment the client actually uses | MAI (56 tasks passed Modal, failed Docker) |
| Results stored centrally (re-introduce code-eval-results / SSOT) | Validation + eval results queryable, not re-derived each time | Internal pattern repeating across deliveries |

---

## Gate 1 — Verifier integrity (the #1 complaint area)

| Check | What it catches | Source |
|---|---|---|
| **No-op / null-agent check** — verifier on the untouched starting container scores 0 | Tests that pass without the fix being applied | MAI (29 no-op tasks), Reflection |
| **Oracle check** — reference solution scores 1 (`is_resolved: true` / reward = 1) | Broken solve path; verifier that rejects the correct answer | NVIDIA (contractual), GDM, Reflection |
| **Reward-file isolation** — agent cannot write the reward directly | Verifiers gameable by writing/echoing a pass value | Reflection |
| **Adversarial exploit pass** — frontier model prompted to game the verifier (~3 rollouts); add an assertion per exploit, re-run oracle | Hardcoded outputs, weak assertions, leaked git/test state | Reflection |
| **Fair verifier** — rewards any valid approach (no false negatives), un-gameable (no false positives) | Brittle stdout-match / method-specific tests that fail correct solutions | Reflection (×2), GDM |
| **No literal/string over-constraint** — tests must not assert on specific strings, hardcoded characters, or fixed literals/functions that over-constrain the solution path | Brittle tests that only pass for one exact implementation; flaky across runs | **Reflection (repeat — flagged as their single largest issue)** |
| **Two-way coverage** — every tested behavior is documented, every documented behavior is tested | Tests checking undocumented behavior; documented behavior left untested | Quality Standard, GDM QC skill |

---

## Gate 2 — Anti-cheat / leakage

| Check | What it catches | Source |
|---|---|---|
| `tests/` and `solution/` not copied into the image | Agent reading the answer | Quality Standard, GDM |
| No hidden hints/guides in agent-accessible files | Leaked solution steps | GDM QC skill |
| git-clone images don't expose future commits | Solution leaked via git history | Quality Standard |
| Nothing in the environment accidentally reveals the answer | Incidental leakage | GDM, Reflection (confirmed reward leaks) |
| **Post-setup cleanup** — ground-truth / build artifacts removed from the agent-visible filesystem after environment setup; agent's starting context = exactly what the README defines, nothing more | Ground truth copied into the agent environment because setup left it behind | **Reflection (repeat — explicitly flagged)** |
| Not trivially solvable by googling (if internet allowed) | Contamination / shortcut paths | GDM QC skill |

---

## Gate 3 — Instruction–reward alignment

| Check | What it catches | Source |
|---|---|---|
| Agent can pass from instructions alone | Tests requiring names/formats/signatures/return values not stated | Quality Standard |
| No example-only wording enforced as a hard requirement | "for example" turned into a strict test | Quality Standard |
| Required output filenames/paths stated in `task.toml` / `instruction.md` | Tests expecting undocumented output locations | Quality Standard |
| Structured-output schemas fully documented | Tests enforcing an undocumented JSON/API shape | Quality Standard |
| Instructions describe *what success looks like*, not *how to get there*, and aren't vague | Solution handed over; or no reasonable path | GDM QC skill |

---

## Gate 4 — Environment & reproducibility (the "flaky" bucket)

| Check | What it catches | Source |
|---|---|---|
| **No ENTRYPOINT in Dockerfile; use CMD only** | Startup overridden by infra → services never come up → cascade failures | MAI (root cause of many "flaky" tasks) |
| Task runs within standard resource caps (~1 CPU / 4 GB, or client's caps) | Tasks that pass only with extra resources | MAI (11 tasks failed under Docker caps) |
| Deterministic environment — randomness (ports, seeds, timestamps, generated data) fixed/controlled | Non-deterministic pass/fail | Quality Standard, MAI |
| Stable offline setup — setup succeeds from clean container without network (unless that's the task) | Failures from live services / online installs at runtime | Quality Standard, NVIDIA (no third-party API deps) |
| No external fragility — no live services, expiring URLs, changing sites, remote APIs | Hidden flakiness from the outside world | Quality Standard, NVIDIA |
| Reasonable runtime within timeout budget | Correct-but-too-slow tasks | Quality Standard |
| Builds with the client's pinned toolchain (e.g., Docker v24.0.9, Terminal Bench 0.2.4; emits `pre-agent.txt`) | Build/launch mismatches at the client | NVIDIA |

---

## Gate 5 — Content quality & metadata

| Check | What it catches | Source |
|---|---|---|
| Metadata correct & complete (category, language, task type, domain, difficulty) | Wrong/missing tags | GDM QC skill, NVIDIA (distribution reporting) |
| Pinned Python dependencies; no non-existent libraries | Unreproducible installs / fake packages | Quality Standard, NVIDIA |
| No fake/mock/placeholder APIs pretending to be a service | Non-real environments | NVIDIA |
| No typos in filenames, variables, instructions | Sloppy/ambiguous tasks | Quality Standard, GDM |
| Task is meaningful — not vague, trivial, or degenerate | Low-value or malformed tasks | Quality Standard |

---

## Gate 6 — Dataset-level checks (run across the delivery, not per task)

| Check | What it catches | Source |
|---|---|---|
| **Decontamination** vs public benchmarks (Terminal Bench, SWE Bench, LCB, Aider) with stated embedding approach + cosine thresholds | Benchmark contamination | NVIDIA, Reflection (methodology requested) |
| **Cross-delivery overlap / dedup** between task sets going to the same client | Same tasks shipped twice across deliveries | GDM (69 overlaps found) |
| Near-duplicate / template-reuse analysis | Low diversity from template reuse | Reflection |
| **Diversity levers** documented and balanced — metadata (category/language/type), instruction (length, explicit constraints), environment (repo size, file count, dependency count/type) | Concentration in one task shape | Reflection, GDM |
| **Difficulty distribution** reported; difficulty bar enforced where required (e.g., pass@8 ≤ 0.5 on the harder model; TB3 ≤0.30) | Too-easy tasks slipping in | Reflection, NVIDIA |
| No duplicates, no personal data | Duplicate items / PII | NVIDIA |

---

## Gate 7 — Eval & reporting (delivery metadata)

| Check | What it catches | Source |
|---|---|---|
| Evals run on agreed models with **fixed inference/eval protocols** | Inconsistent comparisons across models/deliveries | NVIDIA |
| Correct metric (mean@8 / pass@8 / pass@3 as contracted); trajectories delivered if required | Wrong or missing reporting | NVIDIA, Reflection, GDM |
| Harness compatibility confirmed per model (e.g., Codex won't run non-GPT models; offline tasks limit to native-support agents) | Committing to eval combos that can't run | NVIDIA/MAI (Codex + custom-model incompatibility) |
| Confirm model/infra access *before* committing turnaround (rate limits, throughput) | Timeline commitments on inaccessible models | NVIDIA (unreleased Nemotron checkpoint) |

---

## Suggested ordering for a single task

1. Builds on target toolchain → 2. No-op = 0 → 3. Oracle = 1 → 4. Anti-cheat/leakage clean → 5. Instruction-alignment review → 6. Determinism/resource/offline checks → 7. Adversarial exploit pass → 8. Metadata complete.

A task only ships if it clears 1–8 on the client's target infra, with logs saved. Dataset- and eval-level gates (6 and 7) run once the per-task set is locked.

---

## Appendix A — Ready-to-run deep-QC routine (trajectory-aware)

A single deep-dive pass over a `tasks/` folder, run per-task by a QC agent. Captures the checks above in a form that has actually surfaced repeat-client defects. Run this *in addition to* static checks — the trajectory reading and adversarial sub-agent steps are what static screening missed.

1. **Instruction–verifier alignment** — everything in the prompt is tested, and everything tested is in the prompt.
2. **Reward-hackable criteria / test cases** — read the `evals` folder and the agent trajectories. Flag any case where the agent benefits from access it shouldn't have, uses disallowed approaches, or hardcodes solutions. Even if the trajectory looks clean, spawn an adversarial sub-agent that tries to cheat / hack the problem outside a genuine developer approach.
3. **Comprehensive test cases** — rubric and/or unit tests verify all parts of the instruction across both correctness and optimal-solution routes. Flag brittle tests that go flaky across runs, or that over-constrain the path via hardcoded literals / functions / strings.
4. **Hygiene** — grammar, typos, etc.
5. **Golden-patch correctness** — confirm the golden solution mirrors the happy path and scores 100%. In chain-of-thought, first identify the underlying algorithm/method the task calls for, then check top solutions against the provided golden patch.
6. **Task realism** — `instruction.md` aligns with a real developer workflow plausibly found in coding-agent data.
7. **Task fairness + cheating potential** — deep-dive the environment across **setup, runtime, and cleanup** to confirm the agent's context is exactly what the README defines; spawn an adversarial sub-agent that tries to reach other parts of the environment to confirm it can't.

## Appendix B — Staged QC methodology (delivery-level shape)

The workflow these checks slot into, as used on the Reflection 100-task package:

- **Stage 1 — Broad screen:** select hard tasks (e.g., GPT-5.4 or Opus 4.8 < 50% accuracy); run static/behavioral checks for format, verifier quality, leaks, reward bypasses, Docker issues, solve/verifier coupling.
- **Stage 2 — Deep audit:** anti-cheat, verifier fairness, checklist compliance, model pass rates, diversity. (On the 109-task pass: 75 pass / 32 borderline / 2 defects; main issues were verifier fairness, a few leak/gameability defects, and too much structural similarity.)
- **Stage 3 — Remediation:** remove/replace broken or leaky tasks, fix verifiers, keep evidence per decision.
- **Stage 4 — Replacement QC:** same static/AI + anti-cheat + fairness + oracle/no-op + pass-rate + checksum + uniqueness gates; accept a replacement only if the final package closes at the target difficulty, oracle = 1 on Modal **and** Docker, no-op = 0, checksum match, and uniqueness confirmed.

> **Process lesson from the repeat flag:** Stages 1–4 existed and were run, yet brittle verifiers and a setup-cleanup leak still shipped. The deep routine in Appendix A — especially trajectory reading (#2) and the setup/runtime/cleanup fairness probe (#7) — is the layer to make mandatory, not optional, on high-value deliveries.
