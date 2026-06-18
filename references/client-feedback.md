# Terminal Bench OTS — Client Feedback Compilation

A consolidated view of feedback, requirements, and acceptance criteria across all Terminal Bench OTS clients, drawn from the delivery threads, spec docs, and the internal Terminal Bench Task Quality Standards.

> **Recurring cross-client theme:** every client, in some form, has flagged that **verifiers/tests don't reliably require the fix** (no-op passes, reward leakage, brittle/unfair verifiers) and that **oracle validation is not consistently run as a pre-delivery gate**. This is the single most important quality signal across all four accounts.

---

## Clients at a glance

| Client | SKU / Scope | Primary POC(s) | Status / Nature of feedback |
|---|---|---|---|
| **NVIDIA** | "Terminal Bench OTS", 1,000 tasks | Ellie Evans | Contractual acceptance criteria (pre-delivery) |
| **Microsoft AI (MAI)** | `mai-ots` Terminal Bench delivery | Boxuan Li (researcher) | Post-delivery defect flags (no-op + flaky) |
| **Google DeepMind (GDM)** | 5k delivery; 10k Mina OTS; Real-World Evals | Nandita Sethi; Jo Kerrick | Spec requirements + QC retrospective |
| **Reflection** | 100 OTS tasks + inventory readout (~$10M pipeline) | Tom; Britton | Pre-sale requirements + diversity/QC bar |

*Note: "MAI" is inferred to be Microsoft AI based on the `microsoft-ai.enterprise.slack.com` source domain. "Mina" appears to be GDM's internal project/model name.*

---

## 1. NVIDIA (Ellie Evans)

Feedback here took the form of **contractual acceptance criteria** embedded in the deal for 1,000 Terminal Bench OTS tasks.

**Delivery & format**
- 1,000 tasks; delivery Friday EOD.
- Third-party repos/APIs must be disclosed and pre-approved by NVIDIA.
- Deliver container images as `.sif` artifacts for both `linux/arm64` and `linux/amd64`, plus Dockerfiles, at delivery.

**Quality / content**
- Materially representative of the provided sample data (format, complexity, diversity, difficulty).
- Free of duplicate items and personal data.
- Must pass all checks in NVIDIA's validation pipeline ("Terminal Bench Task Quality Standards").
- No contamination with public benchmarks (Terminal Bench, SWE Bench, LCB, Aider).
- No fake/mock APIs, no non-existent libraries, no dependencies on third-party/external network services.
- Must be formatted so NVIDIA can evaluate GPT-5, Qwen3-Coder-30B, and Claude Sonnet 4 end-to-end.

**Build & oracle requirements**
- Build with Docker v24.0.9 on `linux/amd64` and Terminal Bench v0.2.4; standard launch must emit `pre-agent.txt` showing the container loaded.
- Oracle agent must succeed (`results.json` shows `"is_resolved": true`).
- Tasks that fail to build or whose oracle fails are deleted and redelivered with equivalent-difficulty replacements.

**Metadata & reporting at delivery**
- Difficulty bucketing, task-category distribution, language distribution.
- Eval results on GLM-5.1, an unreleased Nemotron checkpoint, Minimax-2.7, and Claude Opus.
- Report **mean@8**; deliver all trajectories.
- Results across harnesses: Terminus-2, Codex, OpenCode, optionally Stirrup.
- Fixed inference/eval protocols for consistent comparison across models and deliveries.
- Iteration efficiency (edits/turns per task).

---

## 2. Microsoft AI / MAI (Boxuan Li)

Post-delivery defect flags on the `mai-ots` Terminal Bench delivery. High-value account ("evaluating all our data… can spend a lot").

**No-op / non-verifying tests**
- Customer flagged **29 tasks that pass their unit tests with a no-op** — i.e., tests pass without the solution being applied, so they aren't actually verifying the fix. All 29 in `mai-ots/terminal-bench`.

**Flaky oracle solutions (~148 tasks)**
- Oracle solutions don't consistently pass on the customer's infra. Customer noted this isn't necessarily a task bug.
- Internal root-cause (on re-run): mostly environment, not task content —
  - **ENTRYPOINT/CMD handling:** customer infra always overrides container startup (replaces with `sleep infinity`). Tasks relying on a startup `CMD` (e.g., supervisord, services) fail because services never come up.
  - **Resource caps:** customer infra strictly enforces ~1 CPU / 4 GB; tasks needing more pass on Modal but fail there.
  - A small number (4) were genuine task bugs requiring fixes.

**Resulting infra requirement (now applied):**
- Dockerfiles should **not** include `ENTRYPOINT`; use `CMD` only (so infra can override startup cleanly).

---

## 3. Google DeepMind / GDM (Nandita Sethi; Jo Kerrick)

Two streams: the **5k delivery spec** (requirements) and the **Real-World Evals retrospective** (post-use feedback). Adjacent: 10k Mina OTS.

**Delivery requirements (5k spec)**
- 5,000 tasks on the Harbor harness, delivered as a private GitHub repo.
- Includes Gemini Flash 3.5 pass@3 eval results. Excludes SOTA eval results, loss analysis, and trajectories/JSON.
- **No overlap** between the 5k delivery and the 10k OTS (Mina) delivery — 69 overlaps found and swapped out (95 new tasks uploaded, 69 used as replacements).

**GDM QC skill (their formal checklist)**
- Metadata correctness (language, domain, etc.).
- Dockerfile: answer not visible to the model, tests not visible, no hidden hints/guides in accessible files.
- Instructions: every graded rule stated; not so detailed it hands over the solution; no vague language; no spelling/clarity errors.
- Tests: each maps to something instructions asked for; verify the solution actually works (not keyword matching); **don't pass without the model doing work**; reliable/not flaky.
- Solution: golden solution actually solves it; doesn't hardcode or peek at tests; looks like real developer work.
- Anti-cheat/consistency: nothing leaks the answer; instructions/tests/solution agree; not trivially googleable.

**Real-World Evals retrospective (Jo Kerrick)** — feedback from the Real-World Eval set (Terminal Bench, Agentic, SWE Bench tasks)
- Task **definitions were fundamentally sound** — not a task-design problem.
- Almost every issue was an **environment / verifier / Harbor-conversion defect**.
- A single **oracle-validation pass** (run the reference solution through its own verifier) plus a small **static lint gate** would have caught nearly all issues pre-delivery.
- Oracle validation alone found **27 broken tasks out of 220 (12.3%)**.
- **12 of those 27 were invisible** to the structural/other checks then in use.
- Feedback deliberately **excluded expert modifications/additions** — isolating issues originating from the OTS tasks themselves.

---

## 4. Reflection (Tom; Britton)

Pre-sale evaluation for a Terminal Bench purchase — a **~$10M pipeline deal** that had been lost once and revived (leadership RCA). Hard cutoff: Monday AM London time.

**Explicit asks (CODE-660)**
- Categorization of all diversity levers usable for static analysis.
- Mercor's decontamination methodology (embedding approach, cosine-similarity thresholds/distribution, near-duplicate / template-reuse analysis).
- **pass@8** against GPT-5.4 and Opus 4.8.
- 100 of the highest-quality OTS tasks.

**Tom's quality bar (sample priorities)**
- Static, model-independent task diversity.
- Decontamination & coverage.
- TB3-aligned scope ("get ahead").
- Hardened anti-reward-hacking.
- Short, realistic prompts + fair verifiers.

**Anti-reward-hacking protocol they expect (now adopted internally)**
- **Null-agent check:** verifier run against the untouched starting container must score 0.
- **Reward-file isolation:** agent cannot write the reward directly (e.g., echoing a pass value must not produce a pass).
- **Adversarial exploit agent:** frontier model prompted to game the verifier (~3 rollouts); any exploit found gets an assertion added, then oracle re-run to confirm no false negative.

**Difficulty bar**
- Select tasks where **pass@8 ≤ 0.5** on the harder of GPT-5.4 / Opus 4.8; final set targeted ≤0.25 on the best model (beats the TB3 ≤0.30 target).

**Outcome (100-task ship)**
- Dropped 25: 9 removed outright (2 broken + 2 reward leaks + 5 unfair verifiers), 6 too easy, 10 verifier-fairness bugs (brittle stdout-match / state-isolation) replaced rather than fixed.
- Added 16 replacements from a 168-task pool (oracle = 1 on Docker and Modal, no-op = 0, fairness-clean).
- Increased diversity (added SQL/pgvector, game-race, seccomp, nginx, webhook-HMAC, HPC/forecasting/compiler work — less ingest-pipeline concentration).

---

## 5. Cross-client themes (what to fix systemically)

1. **Verifier integrity is the dominant complaint.** No-op passes (MAI), reward leakage and unfair/brittle verifiers (Reflection), and non-verifying tests (GDM) are all variants of the same root issue.
2. **Oracle validation must be a hard pre-delivery gate** — and run/logged consistently (e.g., 3x with saved logs). Both GDM and MAI surfaced defects that prior checks missed.
3. **Environment/Harbor-conversion defects** (ENTRYPOINT/CMD overrides, resource caps, determinism, network dependence) cause "flaky" failures that look like task bugs but aren't.
4. **Anti-reward-hacking** should be explicit: null-agent, reward-file isolation, adversarial exploit pass (Reflection's protocol is the most complete template).
5. **Decontamination & no-overlap** are explicit client requirements (GDM cross-delivery overlap; Reflection decontamination methodology).

---

## 6. The underlying standard: Terminal Bench Task Quality Standards

The shared quality doc (referenced contractually by NVIDIA) is the baseline these feedback events measure against. Summary:

- **Instruction–Reward Alignment:** an agent reading only the instructions must be able to pass; tests can't demand undocumented names/formats/signatures, can't be non-deterministic, game-able, or silently locale/encoding/shell-dependent.
- **General Quality:** two-way instruction↔test coverage; informative test docstrings; anti-cheating (no test/solution access, no leaked git history); documented schemas; pinned Python deps; no typos; `tests/`+`solution/` excluded from image; test-only deps in the runner; no hardcoded solutions; output paths documented.
- **Task Meaningfulness:** not hopelessly vague, trivially simple, or degenerate.
- **Environment & Reproducibility:** deterministic, stable offline setup, no external fragility, reasonable runtime.
