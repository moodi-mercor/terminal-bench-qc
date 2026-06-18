# Terminal Bench QC — Action Items

## Goal: Flag QC issues in Terminal Bench OTS tasks
- How many defects are there out of our dataset?
- What is the distribution of defects (sort into high-level categories)?
- EOD tomorrow for Terminal Bench

## Action Items — Owner: Moodi Mapara

### 1. Build QC skill based off of all threads of feedback
- [ ] Make them as deterministic as possible
- [ ] Make it detailed and contain a lot of subcomponents
- [ ] Get existing QC skills from Studio / from Keya
- [ ] Start with **functional checks** (e.g. all files are present; oracle validation) before moving to **semantic checks** over prompt quality, prompt–verifier alignment, etc.
- [ ] Derive QC from the public Terminal Bench benchmark too
- [ ] Find the publicly available Terminal Bench + Terminal Bench 2 tasks online and use those as "golden examples"
- [ ] Look into: https://github.com/Mercor-Intelligence/code-delivery-scripts-skills/tree/main/skills/terminal-bench-review

### 2. Run QC over tasks
- [ ] Grab ~50 tasks and iterate with QC skill + manual checks until QC skill has 100% recall
- [ ] Expand sample to 100 → 200 tasks until we're confident to apply it across the entire dataset
- [ ] Calculate a concrete precision + recall rate for the QC skill
  - **Precision** = Percentage of tasks flagged as defects that are actually defects
  - **Recall** = Percentage of real defects that were flagged as defects
