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
2. **Behavioral run** (oracle=1 / no-op=0 / auto-cheat) on the non-passing set — start with
   the 749 P0 + 1,761 fixable, then the review pile. Feed it the `gen_cheat_harness.py`
   probes. Drop the results as `behavioral_signals.json` so `aggregate.bucketize` promotes:
   - genuinely broken → `qc_status=defective-hard`, `qc_confidence=confirmed`
   - cheat passes → confirmed reward-hack
   - else → confirmed fixable / clears the review candidate.

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
