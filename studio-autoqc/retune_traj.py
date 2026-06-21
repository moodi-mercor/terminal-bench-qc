#!/usr/bin/env python3
"""Tighten the Verifier Audit (trajectory) FN dim to cut lone over-claims, then PATCH-redeploy.

Change: FAIL only when the unfair check is STRUCTURALLY brittle and readable in tests/
(you can SEE the assertion demand exact-byte/hash/order/etc.). If concluding the solution is
correct requires the judge to reason out / infer the expected answer, that is NEUTRAL, not FAIL.
Adds a calibration clause to the preamble. FP + consistency dims unchanged.
"""
import json
import sys
import requests

API = "https://api.studio.mercor.com"
CAMP = "camp_4e196b1414a1499db54b43233104b0a7"
COMP = "comp_2fa4115109d741cd94a3c409ed89e61f"
ACCT = "acct_85b680d4c5ba49a29f19c173672aebea"
ROOT = "/Users/mahmoodmapara/Desktop/terminal-bench-qc"
SID = "qcspec_ece2ca798fd2580188abd82c"
F = f"{ROOT}/_local/tb_modules/04_verifier_audit_trajectory.json"

NEW_FAIL = (
    "FAIL only when the unfairness is PROVABLE from the staged files WITHOUT inferring the hidden "
    "oracle's expected answer, with ALL holding: (1) one or more test_statuses entries are 'fail' "
    "(or final_score < 1.0); (2) you can READ, in tests/, the exact failing assertion and SEE that it "
    "demands something the instruction never pinned as a requirement -- exact-byte / hash / md5 equality, "
    "trailing-newline or whitespace match, line/key ORDERING or tie-break, JSON-key or float-format "
    "canonicalization, dedup, or an environment/locale/timing assumption; AND (3) the diff plainly "
    "produces a VALID output of that kind that the brittle assertion would reject. Name the EXACT check "
    "(tests/test_outputs.py::test_check_N), QUOTE the brittle assertion from tests/ AND the diff hunk that "
    "satisfies the intent. HARD RULE: if your conclusion that the solution is correct rests on YOU computing, "
    "reasoning out, or assuming the expected result (rather than reading a brittle assertion in tests/), do "
    "NOT fail -- emit NEUTRAL. You see only ONE attempt; never infer anything from, or assume the existence "
    "of, other models' runs."
)
NEW_NEUTRAL = (
    "NEUTRAL is the DEFAULT for any false-negative suspicion you cannot prove without the hidden oracle. "
    "Emit NEUTRAL when: the diff plausibly meets the intent but confirming it would require the verify-time "
    "reference / expected value the judge cannot see or must infer; OR the failing assertion is a normal "
    "correctness check (not a readable brittle one); OR the trajectory content (diff/test_statuses/score) is "
    "not staged; OR the diff is too large/truncated to judge. A NEUTRAL is a candidate to surface for the "
    "cross-model confirm + behavioral oracle gate, NOT a verdict."
)
CALIB = (
    "\n\nCALIBRATION UPDATE (v2): You judge ONE trajectory in isolation and you do NOT have the hidden "
    "oracle output. Do not turn 'I reasoned that this diff looks correct' into a FALSE-NEGATIVE verdict -- "
    "that is guessing the answer key. Only emit a false-negative FAIL when the unfairness is readable in "
    "tests/ as a structurally brittle assertion (exact-byte/hash/order/whitespace/canonicalization/dedup/"
    "timing) that would reject an output your diff plainly produces. Otherwise emit NEUTRAL (a candidate). "
    "Cross-model agreement and the behavioral oracle run happen downstream -- your job is to be a precise, "
    "low-false-alarm candidate generator, not the final word."
)


def key():
    for line in open(f"{ROOT}/.env"):
        if line.startswith("RLS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")


H = {"Authorization": f"Bearer {key()}", "X-Campaign-Id": CAMP, "X-Company-Id": COMP,
     "X-Account-Id": ACCT, "User-Agent": "curl/8.7.1", "Content-Type": "application/json"}


def main():
    s = json.load(open(F))
    changed = False
    for sec in s["rubric"]["sections"]:
        for d in sec["dimensions"]:
            if "false_negative" in d["dim_id"]:
                d["fail_criteria"] = NEW_FAIL
                d["neutral_criteria"] = NEW_NEUTRAL
                changed = True
    assert changed, "FN dim not found"
    ex = s["execution"]["default"]
    if "CALIBRATION UPDATE (v2)" not in (ex.get("system_prompt_preamble") or ""):
        ex["system_prompt_preamble"] = (ex.get("system_prompt_preamble") or "") + CALIB
    json.dump(s, open(F, "w"), indent=2)
    print("patched local spec (FN dim + preamble calibration)")

    r = requests.patch(f"{API}/qc-specs/{SID}", headers=H,
                       data=json.dumps({"spec": s, "name": "Verifier Audit",
                                        "description": (s.get("rubric", {}).get("description") or "")[:480]}),
                       timeout=120)
    try:
        resp = r.json()
    except Exception:
        resp = r.text
    print(f"PATCH /qc-specs/{SID} -> {r.status_code}")
    if r.status_code >= 300:
        print("BODY:", str(resp)[:600]); sys.exit(1)
    print(f"OK new version={resp.get('version') if isinstance(resp,dict) else '?'}")


if __name__ == "__main__":
    main()
