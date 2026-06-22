# studio-autoqc — deploy the QC layers into RL Studio

Operational tooling (not part of the offline skills) that ports the three QC layers
into RL Studio's **modular AutoQC** as live, server-side `qc-spec` / `qc-audit`
modules on the **[OTS] Terminal Bench** campaign. The skills in `skills/` are the
source of truth for the *logic*; these scripts deploy, smoke-test, and re-tune the
Studio modules that mirror it.

**Four live modules** (subject in parentheses):
1. **Task Quality Review** (task) — Layer 1 semantic reviewer. Reflection-aligned
   (v6): + agentic/non-trivial, valid-constraints, no-misleading-distractor, and
   tolerance-calibration dims (NEUTRAL-default).
2. **Reward-Hack / Adversary QC** (task) — Layer 1 adversary; a surviving cheat is a
   NEUTRAL candidate, never an auto-fail. Reflection-aligned (v2): + cv_07 PATH-
   interception / fake-wrapper / monkey-patch cheat vector.
3. **Static Structural QC** (task) — Layer 1's nine gates as judge dims (the local
   Python detectors stay the precise source).
4. **Verifier Audit** (trajectory) — Layer 2 trajectory judge: reads the rollout diff
   + test statuses + final score, judges blind-to-score.

Decontamination (Layer 1 dataset) and behavioral (Layer 3) stay offline — they need
a corpus / Docker that a per-subject Studio module can't host.

## Auth & state

- **`RLS_KEY`** is read from the repo-root `.env` (gitignored). Requests send
  `Authorization` + `X-Campaign-Id` / `X-Company-Id` / `X-Account-Id`.
- Module specs and deployed ids live under **`_local/tb_modules/`** (gitignored,
  machine-local): authored JSON specs, `_deployed_*_id.json`, eval sweep results.
- Scripts hardcode an absolute `ROOT` to this checkout — adjust it if you clone
  elsewhere. They are personal operational scripts, not a portable package.

## Scripts

| Group | Script | Does |
|---|---|---|
| **Preflight** | `preflight.py` | read-only go/no-go: can an autograder see the verifier + ref solution in the staged FS? |
| **Deploy** | `deploy.py` | PATCH the existing reviewer + POST the static & adversary task modules (additive) |
| | `deploy_traj.py` | POST the trajectory Verifier Audit module (additive) |
| | `patch_static.py` / `patch_review.py` | PATCH a deployed module to a new calibration |
| | `patch_reflection_align.py` | GET the live reviewer + adversary, add the Reflection-alignment dims (reviewer Check-6: agentic / valid-constraints / no-distractor / tolerance-calibration; adversary cv_07 PATH-intercept), snapshot + PATCH. Idempotent; rolls back from the saved `_snapshot_*`. |
| **Smoke** | `smoke.py` / `smoke_traj.py` | trigger audits on a few tasks/trajectories, poll, print per-dim verdicts |
| **Eval** | `eval.py` / `eval_v3.py` | run the task modules over the Studio-auditable slice of the 200-row eval set |
| | `eval_sweep.py` | run the trajectory module across all rollouts of the OTS eval tasks |
| | `reeval_static.py` / `reeval_traj.py` | re-audit only the tasks/trajectories a prior version flagged, recompute P/R |
| **Retune** | `retune_traj.py` | tighten the trajectory FN dim to cut lone over-claims, then PATCH-redeploy |
| **Inspect** | `diag.py` / `findrun.py` / `poll.py` | read-only: why audits are pending, find a completed run, dump raw audit shape |
| | `traj_probe.py` | read-only feasibility probe for a `subject_kind=trajectory` module |
| | `report.py` | render `_smoke_results.json` into Markdown (no API calls) |

## Note

Studio writes (POST/PATCH `qc-specs`, POST `qc-audits`) are gated by the harness
auto-mode classifier and need explicit approval each run. The modules were authored
offline with the AQC Modular Setup builder; see the workspace memory for module ids
and calibration history.
