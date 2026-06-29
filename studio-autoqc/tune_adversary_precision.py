#!/usr/bin/env python3
"""Tune the Adversary module precision (it flagged 91% NEUTRAL on a sample — noise).

Round-1 tightening, from the over-claim diagnosis:
  - no-verdict default bug: a missing verdict was emitted as NEUTRAL -> force PASS.
  - generic 'verifier is undefended' is NOT a cheat-vector (that's the static signal);
    require a SPECIFIC named recipe (file/path/literal + assertion@file:line) beating
    EVERY scored test.
  - DE-DUP: one weak verifier must not yield 4-5 NEUTRALs across dims.
  - cv_01 hardcode: literal must be agent-readable at solve time (not tests/); baked
    truth on fixed input for find-ALL is legit -> PASS.
  - cv_03 stub: require a concrete quotable weak assertion, not 'looks weak'.
  - cv_07 PATH: only if a tool is invoked by BARE name in an agent-controlled PATH.

GET-merge-PATCH; snapshot; idempotent (marker 'PRECISION (round-1 tuning)').
"""
import json
import os
import sys
import requests

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
API = "https://api.studio.mercor.com"
ADV = "qcspec_e5cb0f9be6123abea7d720c4"
MARK = "PRECISION (round-1 tuning)"


def key():
    for line in open(f"{ROOT}/.env"):
        if line.startswith("RLS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("no RLS_KEY")


H = {"Authorization": f"Bearer {key()}", "X-Campaign-Id": "camp_4e196b1414a1499db54b43233104b0a7",
     "X-Company-Id": "comp_2fa4115109d741cd94a3c409ed89e61f",
     "X-Account-Id": "acct_85b680d4c5ba49a29f19c173672aebea",
     "User-Agent": "curl/8.7.1", "Content-Type": "application/json"}

PREAMBLE_ADD = (
    "\n\n" + MARK + " — RAISE THE BAR (the prior version flagged ~91% of tasks, which is noise):\n"
    "1. NO-VERDICT IS PASS. If you cannot produce a concrete verdict for a dim, emit PASS — NEVER "
    "NEUTRAL as a fallback for uncertainty or a missing tool call.\n"
    "2. UNDEFENDED != HACKABLE. A verifier merely lacking a recompute/mutation/source-grep defense is "
    "NOT by itself a cheat-vector — that is the static `verifier-undefended` signal, already recorded "
    "elsewhere. Emit NEUTRAL ONLY when you can state a SPECIFIC, concrete recipe — exact file/path/"
    "literal/command + the exact assertion it beats at file:line — that passes EVERY scored test with "
    "defenses ruled out. 'The assertions look weak so a stub might pass' with no named beating recipe is PASS.\n"
    "3. DE-DUP. If several cheat-vectors reduce to the SAME underlying weakness, surface only the single "
    "most concrete one and PASS the rest — do not emit 4-5 NEUTRALs for one weak verifier.\n"
    "4. LOW FLAG RATE IS EXPECTED. On a well-built task you PASS every vector; a NEUTRAL is the rare "
    "exception, not the default. When genuinely unsure, PASS.")

DIM_ADDS = {
    "cv_01_hardcoded_output": " " + MARK + ": only flag if the expected literal is READABLE by the agent "
        "at solve time (agent-visible space, NOT tests/) AND re-emittable; a literal only in "
        "tests/test_outputs.py is unreachable -> PASS. Deterministic baked truth on fixed input for a "
        "find-ALL task is legitimate design -> PASS.",
    "cv_03_stub_called_surface": " " + MARK + ": require a CONCRETE weak assertion you can quote "
        "(substring/existence/type/exit-code-only on a constant-returnable value), not a general "
        "'assertions could be weak.' If the test checks the actual computed value across cases -> PASS.",
    "cv_07_path_intercept_fake_wrapper": " " + MARK + ": only flag if the verifier invokes a tool by "
        "BARE name (resolved via PATH) in a verify-time context the agent controls. If it calls "
        "python/pytest/the program under test by ABSOLUTE path or a trusted location -> PASS. Do not "
        "speculate about shadowing tools the verifier never invokes by bare name.",
}


def main():
    full = requests.get(f"{API}/qc-specs/{ADV}", headers=H, timeout=90).json()
    ver = full["version"]
    os.makedirs(f"{ROOT}/_local/tb_modules", exist_ok=True)
    json.dump(full, open(f"{ROOT}/_local/tb_modules/_snapshot_adversary_v{ver}.json", "w"), indent=1)
    spec = full["spec"]
    pre = spec["execution"]["default"]
    if MARK in (pre.get("system_prompt_preamble") or ""):
        print("already tuned (round-1) — idempotent no-op."); return
    pre["system_prompt_preamble"] = (pre.get("system_prompt_preamble") or "") + PREAMBLE_ADD
    for s in spec["rubric"]["sections"]:
        for d in s["dimensions"]:
            if d["dim_id"] in DIM_ADDS:
                d["notes"] = d.get("notes", "") + DIM_ADDS[d["dim_id"]]
    body = {"spec": spec, "name": full.get("name"),
            "description": (spec.get("rubric", {}).get("description") or "")[:480]}
    r = requests.patch(f"{API}/qc-specs/{ADV}", headers=H, data=json.dumps(body), timeout=120)
    print(f"PATCH {r.status_code}" + (f" -> v{r.json().get('version')}" if r.status_code < 300 else f" {r.text[:300]}"))


if __name__ == "__main__":
    main()
