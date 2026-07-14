# Nvidia ATCB x Terminal-Bench 3: Alignment Call Questions

**Context:** Nvidia proposed adopting TB3's verification flow (deliverables to set paths, verification in a new container) for the 5,000-task ATCB extension. TB3 changes about 10 things. They asked for one. These questions cover the rest.

**Opening line:** "You asked for the verifier change. TB3 changes several other things too. For each one, do you want it or not? Some are cheap, others change scope and AHT."

---

## Core questions

### 1. QC / acceptance gates
> Should we adopt TB3's automated acceptance checks (pinned deps, no installs at verify time, oracle must pass, empty agent must fail)? Want the results delivered per task?

### 2. Contamination / canary
> TB3 hides a canary string in every task to detect training-data leaks. Want canaries? Your string or ours?

### 3. Internet policy
> TB3 requires open internet on every task. Training tasks are usually air-gapped. On, off, or per task?

### 4. Difficulty bar
> TB3's bar: frontier models solve under 30%. Yours: the GLM 5.2 pass window. Should any slice also meet the TB3 bar, or is GLM the only criterion?

### 5. Prompt format
> TB3 prompts follow a fixed format: time budget + no-cheating line at the end, output schema in the prompt, absolute paths. Adopt it, or keep ATCB style?

### 6. Golden solution standard
> TB3 rejects solutions that hardcode the answer; the solution must compute it. Adopt that rule? Want a short solution write-up per task?

### 7. Test style
> TB3 tests check behavior by running things, never by grepping the agent's code. Hold all new tasks to that?

### 8. Deliverable paths and reward output
> Should each task declare fixed output paths like TB3? And should the verifier emit one pass/fail score or per-test results?

### 9. Task format compatibility
> Will these run on the TB3/Harbor harness or your own? Decides whether we match TB3's format exactly or just its behavior.

### 10. Metadata and taxonomy
> TB3 tags each task with domain, expert time estimate, and notes on difficulty and verification. Want that metadata?

### 11. Environment richness
> TB3 environments are 2-3x bigger than TB2's (multi-file projects, sometimes multi-service). Want that scale or keep current size?

### 12. Adversarial hardening
> TB3 runs an agent that tries to cheat each verifier before acceptance, then hardens the task. Add that QC layer?

### 13. Scope of the transition
> All 5,000 tasks TB3-style, or a portion (e.g. the 1,000 hardest)? Forward-only, or retrofit delivered tasks?

### 14. GPU tasks
> Want a GPU slice (kernels, training, inference)? Whose compute runs the evals?

---

## Domain distribution

### 15. Domain mix
> The proposal targets the TB2 mix. TB3 adds Operations, Hardware, Media, and more Science. Move toward TB3's distribution? What weights?

### 16. New domains vs. long-horizon criteria
> Do new domains still need to hit the GLM token and pass windows, or do those only apply to core software/ML?

---

## If there's only five minutes

Ask **#13 (scope), #9 (harness), #8 (deliverables/reward), #4 (difficulty bar), #3 (internet)**.

## Cost signal (our side only)

- **Cheap:** prompt format (5), canary (2), metadata (10), test style (7), golden solution rule (6)
- **Moderate:** CI gates (1), deliverable/reward plumbing (8)
- **Changes scope or AHT:** frontier difficulty (4), environment richness (11), adversarial hardening (12), GPU (14), retrofit (13)
