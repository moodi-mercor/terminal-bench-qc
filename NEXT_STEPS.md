# Terminal Bench QC — Next Steps

**State (2026-06-27):** the full **13,433-task** Canonical Tasks world is statically
QC'd and labeled live in RL Studio (`tasks.custom_fields.qc_*`), fields registered in
the world schema, and **5 SQL dashboards** live in the sidebar. Everything is
`qc_confidence=candidate` (static only).

**Headline numbers:** 1,761 fixable (749 P0 leak/hack) · 11,647 needs-review · 5 passing ·
**~6,652 `__pycache__` build-residue leaks (~50%)** · 0 confirmed `defective-hard` (needs behavioral).

Detectors/labelers committed on `main` (`a208e0b`…`27d3d0e`). Tools: `studio_label.py`,
`full_corpus_qc.py`, `gen_cheat_harness.py`, `assert_classify.py`.

---

## Phase 1 — Close the labeling loop
1. Let `tb-fullqc-monitor` reach 13,433/13,433, then **disable the monitor**.
2. **Behavioral run — CLOUD path (confirmed feasible, not local builds).** Lighthouse/Harbor
   grades on Modal; signals live on trajectories, not task custom_fields.
   - **Batch 1 — oracle + no-op (749 P0 first):** `POST /orchestration/trajectories/batch`
     with the `validate_patch` agent `agent_ec6f92015c4447d3a62f3dbf0f341a93`
     (grades empty→expect 0, golden `solution/solve.sh`→expect 1; $0 LLM tokens, ~2 min/task,
     ~25 concurrent CPU-hrs for 749). Get P0 IDs via
     `POST /querier/task-ids {SQL: qc_priority='P0'}` (749, confirmed). Each entry needs
     task_id + orchestrator_id/version + agent_id/version; **world default_orchestrator_ids is
     EMPTY → fetch a campaign orchestrator first.** Batch create is rate-limited 5/min, max
     50k/batch. Monitor `GET /trajectory-batches/{id}` + `GET /trajectories/batch/{id}`; read
     `trajectory_output.validation_passed / empty_score / golden_score`.
   - **Batch 2 — cheat confirmation (DESTRUCTIVE, subset only):** stage the auto-cheat into the
     snapshot (`POST /snapshots/task/{id}/update`, file `filesystem/solution/solve.sh`) then run
     an oracle-only agent (`harbor_agent: oracle`, `validate_patch:false`); `score=1.0` = confirmed
     reward-hack. Mutates the golden ref → do on clones/scratch or re-upload original after. Scope
     to the static reward-hack flags only.
   - Map results into `behavioral_signals.json` → `aggregate.bucketize` promotes broken→
     `defective-hard`+`confirmed`, cheat-pass→confirmed reward-hack, else clears the candidate.
   - Note: golden=1 reuse is NOT available (tasks have empty final_score/has_gt_grade — no prior
     golden runs stored); must generate via Batch 1.

## Phase 2 — Remediate (where the QC pays off)
3. **Pycache systemic fix (~6,652 — biggest win):** bulk-add `ENV PYTHONDONTWRITEBYTECODE=1`
   (or a post-build `find / -name '*.pyc' -delete`) to affected task images. Scriptable as a
   batch over the `qc_remediation`-flagged tasks.
4. **P0 relocate queue (1,266):** move grader / truth / `.pyc` out of the agent image into
   `tests/` (verify-time mount). Mechanical; templatable.
5. **Strengthen queue (495):** add recompute / mutated-rerun / functional assertions to the
   weak/brittle verifiers (literal-only, source-match, wall-clock, etc.).

## Phase 3 — Workflow + visibility
6. Drive real **`Fail QC` status transitions** — only on the Phase-1 confirmed-broken set
   (the statuses are frozen + non-deliverable, so candidate-only is unsafe).
7. Add a **`qc_top_defect`** field (+ a `qc_pycache` flag) so we can build class-level
   dashboards (pycache / truth-baked / agent-writable). Quick relabel + register.
8. Upgrade the 6 `qc_*` schema fields from `text` → `select` for dropdown filters in RLS.

## Phase 4 — Make it continuous (not a one-shot)
9. Wire the hardened detectors into the **RLS AutoQC qc-spec modules** so new/edited tasks
   get auto-QC'd + labeled on the fly.
10. Schedule a periodic full-corpus re-run as the world grows; trend the defect rate on the
    dashboards.

## Detector backlog (minor)
- Use Phase-1 behavioral ground truth to **tighten the review→fixable boundary** (87% review
  is high — some are real defects, some advisory noise).
- Optional: saved RLS `custom_views_config` views (schema undocumented; build in UI for now).
