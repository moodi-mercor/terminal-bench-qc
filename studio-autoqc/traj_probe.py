#!/usr/bin/env python3
"""Read-only feasibility probe for a subject_kind=trajectory AutoQC module.

The trajectory analog of the A2 task-FS probe. Confirms a real rollout's
trajectory_output exposes what a Verifier-Audit judge would need to stage:
the solution DIFF, per-test STATUSES, and the FINAL SCORE. GET-only.
"""
import json
import sys
import requests

API = "https://api.studio.mercor.com"
CAMP = "camp_4e196b1414a1499db54b43233104b0a7"
COMP = "comp_2fa4115109d741cd94a3c409ed89e61f"
ACCT = "acct_85b680d4c5ba49a29f19c173672aebea"
BATCH = "batch_c5e617e48b0f41eaa13337976014e396"
ROOT = "/Users/mahmoodmapara/Desktop/terminal-bench-qc"


def key():
    for line in open(f"{ROOT}/.env"):
        if line.startswith("RLS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")


H = {"Authorization": f"Bearer {key()}", "X-Campaign-Id": CAMP, "X-Company-Id": COMP,
     "X-Account-Id": ACCT, "User-Agent": "curl/8.7.1"}


def get(path, **params):
    r = requests.get(f"{API}{path}", headers=H, params=params or None, timeout=120)
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)


def main():
    print("== 1. Batch reachable + score spread ==")
    st, data = get(f"/trajectories/batch/{BATCH}", limit="40", offset="0")
    rows = data.get("trajectories", []) if isinstance(data, dict) else []
    print(f"  GET /trajectories/batch -> {st}, {len(rows)} rows (first page)")
    if not rows:
        print("  FAIL: no trajectories"); sys.exit(1)
    # pick a few completed ones, prefer a mix of score 0 and 1
    comp = [r for r in rows if r.get("trajectory_status") == "completed"]
    zero = [r for r in comp if (r.get("final_score") or 0) == 0][:2]
    one = [r for r in comp if (r.get("final_score") or 0) == 1][:2]
    sample = (zero + one) or comp[:3]
    print(f"  sampling {len(sample)} trajectories (score-0:{len(zero)} score-1:{len(one)})")

    print("\n== 2. Per-trajectory staging check (what a trajectory module would read) ==")
    NEED = ["solution(diff)", "test_statuses", "final_score"]
    all_ok = True
    for r in sample:
        tid = r.get("trajectory_id")
        st, t = get(f"/trajectories/{tid}")
        out = (t.get("trajectory_output") or {}) if isinstance(t, dict) else {}
        diff = out.get("solution") or ""
        ts = out.get("test_statuses") or {}
        score = t.get("final_score", r.get("final_score")) if isinstance(t, dict) else r.get("final_score")
        print(f"\n  {r.get('task_name')} [{tid}]  model={r.get('orchestrator_llm_model')} score={score}")
        print(f"     solution(diff)  : {'OK' if diff else 'MISSING'}  ({len(diff)} chars)")
        print(f"     test_statuses   : {'OK' if ts else 'MISSING'}  ({len(ts)} checks: {list(ts)[:6]})")
        print(f"     final_score     : {'OK' if score is not None else 'MISSING'}  ({score})")
        print(f"     tests_passed/failed: {out.get('tests_passed')}/{out.get('tests_failed')}")
        if not (diff and ts and score is not None):
            all_ok = False

    print("\n== Verdict ==")
    if all_ok:
        print("  GO: trajectory_output exposes diff + per-test statuses + score on this campaign.")
        print("      A subject_kind=trajectory judge module can stage exactly what trajectory-audit Stage 3 reads.")
    else:
        print("  PARTIAL: some fields missing on some trajectories — inspect above before authoring.")


if __name__ == "__main__":
    main()
