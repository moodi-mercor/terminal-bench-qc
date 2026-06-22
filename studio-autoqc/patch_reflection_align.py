#!/usr/bin/env python3
"""Patch the live reviewer + adversary AutoQC modules to the Reflection-aligned set.

Adds the semantic criteria the offline skill gained (QC_GUIDE Check 6 + the adversary
PATH/wrapper hack class) to the LIVE Studio modules, so the deployed modules match the
repo's prompts.

Safety:
  - reads the CURRENT live spec (GET) and adds to THAT — never patches from a possibly
    stale authored file (live v4 already diverged from the authored v2 JSON).
  - snapshots the live spec before every PATCH (-> _local/tb_modules/_snapshot_*).
  - idempotent: a dim_id already present is skipped, so re-running is a no-op.
  - prints a before/after dim count and the new version.

Run from anywhere; reads RLS_KEY from the repo-root .env. Studio writes are gated by
the harness — approve each PATCH.
"""
import json
import os
import sys
import requests

API = "https://api.studio.mercor.com"
CAMP = "camp_4e196b1414a1499db54b43233104b0a7"
COMP = "comp_2fa4115109d741cd94a3c409ed89e61f"
ACCT = "acct_85b680d4c5ba49a29f19c173672aebea"
ROOT = "/Users/mahmoodmapara/Desktop/terminal-bench-qc"
MODDIR = f"{ROOT}/_local/tb_modules"
REVIEWER_ID = "qcspec_7bddfd703a12994dbc31fd1b"
ADVERSARY_ID = "qcspec_e5cb0f9be6123abea7d720c4"


def key():
    for line in open(f"{ROOT}/.env"):
        if line.startswith("RLS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("no RLS_KEY in .env")


H = {"Authorization": f"Bearer {key()}", "X-Campaign-Id": CAMP, "X-Company-Id": COMP,
     "X-Account-Id": ACCT, "User-Agent": "curl/8.7.1", "Content-Type": "application/json"}

# ---- new reviewer dimensions (Check 6) — NEUTRAL-default, FAIL only clear-cut ----
REVIEWER_ADDS = {
    "Realism": {
        "dim_id": "task_agentic",
        "name": "Agentic / Non-Trivial",
        "description": "FAIL only if the task is clearly solvable by a single command, a simple transcription, or zero-shot code generation with no exploration.",
        "pass_criteria": "Solving requires meaningful multi-step terminal work — exploration, debugging, file manipulation, or iteration against the environment.",
        "fail_criteria": "The task can be one-shotted with a single command or trivial transcription, requiring no investigation of the environment.",
        "neutral_criteria": "Default NEUTRAL if you cannot tell from the files whether real work is required, or instruction.md is not staged.",
        "notes": "Litmus: could a competent dev one-shot this WITHOUT reading the environment? Judge whether it needs investigation, not whether it merely looks small. Do not over-call.",
    },
    "Instruction Quality": {
        "dim_id": "instruction_valid_constraints",
        "name": "Valid (Non-Arbitrary) Constraints",
        "description": "FAIL only if a formatting/precision/tool/process constraint has no real purpose and no anti-cheat value and exists only to inflate difficulty.",
        "pass_criteria": "Every stated constraint maps to a real task requirement or prevents a shortcut/cheat.",
        "fail_criteria": "An arbitrary hoop with no purpose ('use exactly 3 spaces', 'you must use awk', 'round to 7 decimals' for no reason) added only to make the task harder.",
        "neutral_criteria": "Default NEUTRAL when a constraint's purpose is unclear but plausible.",
        "notes": "Inverse of over-specification: over-spec hands over the solution; an arbitrary constraint adds a pointless hoop. A constraint that genuinely blocks a shortcut is valid.",
    },
    "Task Coherence": {
        "dim_id": "no_misleading_distractor",
        "name": "No Misleading Distractors",
        "description": "FAIL only if extraneous environment content would actively misdirect a reasonable agent AND the task is not an explicitly-reviewed instruction-alignment/distractor task.",
        "pass_criteria": "Any extra files are incidental and not misleading; the intended path stays discoverable.",
        "fail_criteria": "Decoy configs, red-herring code, or planted dead ends that would actively steer a reasonable agent wrong, on a task not designed as a distractor task.",
        "neutral_criteria": "Default NEUTRAL for incidental unused files — those are not distractors.",
        "notes": "Incidental unused files are not distractors. Reserve for content that actively misleads.",
    },
    "Test Quality": {
        "dim_id": "tolerance_calibration",
        "name": "Tolerance Calibration",
        "description": "FAIL only if a numeric tolerance / fuzzy match / range assertion is clearly miscalibrated — too loose (admits a wrong answer) or too tight (rejects a correct alternative).",
        "pass_criteria": "Any tolerance/threshold is justified: a correct solution lands inside it and a plausible wrong one does not.",
        "fail_criteria": "A tolerance that lets a clearly-wrong output pass, or one so tight it would reject a correct alternative solution.",
        "neutral_criteria": "Default NEUTRAL if no numeric tolerance/fuzzy match is used, or its calibration can't be judged from the files.",
        "notes": "Check BOTH directions: too-loose passes wrong answers; too-tight rejects correct ones.",
    },
}

# ---- new adversary cheat-vector (PASS/NEUTRAL, never FAIL — matches the cv style) ----
ADVERSARY_ADD = {
    "section": "Reward-Hack Cheat Vectors",
    "dim": {
        "dim_id": "cv_07_path_intercept_fake_wrapper",
        "name": "Intercept via PATH / Fake Wrapper / Monkey-Patched Library",
        "description": "NEUTRAL if a tool the verifier shells out to (the program under test, a checker, python) can be replaced via PATH or a fake wrapper, or a library the verifier imports can be monkey-patched, so the verifier reads a forged result.",
        "pass_criteria": "PASS — the verifier invokes tools by absolute path or from a trusted location the agent can't shadow, does not import agent-writable modules, and a fake binary/wrapper/monkey-patch on PATH or sys.path cannot influence its result.",
        "fail_criteria": "Never emit fail for this dim.",
        "neutral_criteria": "NEUTRAL (candidate) if prepending a dir to PATH, dropping a fake binary/wrapper, or monkey-patching an imported library would let a tool the verifier calls return the expected result without the real work. Name the tool and where it is invoked (file:line). Rule out: verifier calls by absolute path, runs in a clean PATH, or re-derives the result so a forged tool still fails.",
        "notes": "Reflection's named shortcuts: replacing tools, fake wrappers, PATH interception, modifying the test harness. Distinct from cv_05 (overwriting the grader itself) — this shadows a tool the grader depends on.",
    },
}


def get_spec(sid):
    r = requests.get(f"{API}/qc-specs/{sid}", headers=H, timeout=90)
    r.raise_for_status()
    return r.json()


def patch_spec(sid, spec, name):
    body = {"spec": spec, "name": name,
            "description": (spec.get("rubric", {}).get("description") or "")[:480]}
    r = requests.patch(f"{API}/qc-specs/{sid}", headers=H, data=json.dumps(body), timeout=120)
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)


def section_dim_ids(spec):
    return {s.get("name"): [d.get("dim_id") for d in s.get("dimensions", [])]
            for s in spec.get("rubric", {}).get("sections", [])}


def add_reviewer_dims(spec):
    added = []
    for sec in spec.get("rubric", {}).get("sections", []):
        new = REVIEWER_ADDS.get(sec.get("name"))
        if not new:
            continue
        ids = [d.get("dim_id") for d in sec["dimensions"]]
        if new["dim_id"] in ids:
            continue
        sec["dimensions"].append(json.loads(json.dumps(new)))  # deep copy
        added.append(new["dim_id"])
    return added


def add_adversary_dim(spec):
    for sec in spec.get("rubric", {}).get("sections", []):
        if sec.get("name") != ADVERSARY_ADD["section"]:
            continue
        ids = [d.get("dim_id") for d in sec["dimensions"]]
        if ADVERSARY_ADD["dim"]["dim_id"] in ids:
            return []
        sec["dimensions"].append(json.loads(json.dumps(ADVERSARY_ADD["dim"])))
        return [ADVERSARY_ADD["dim"]["dim_id"]]
    return []


def run_one(label, sid, augment, authored_out):
    print(f"\n== {label} ({sid}) ==")
    full = get_spec(sid)
    ver = full.get("version")
    spec = full.get("spec") if isinstance(full.get("spec"), dict) else full
    snap = f"{MODDIR}/_snapshot_{label}_v{ver}.json"
    os.makedirs(MODDIR, exist_ok=True)
    json.dump(full, open(snap, "w"), indent=2)
    print(f"  snapshot live v{ver} -> {snap}")
    before = section_dim_ids(spec)
    added = augment(spec)
    if not added:
        print("  already aligned (no new dims) — skipping PATCH.")
        return
    print(f"  adding dims: {added}")
    st, resp = patch_spec(sid, spec, full.get("name") or label)
    if st >= 300:
        print(f"  PATCH FAILED {st}: {str(resp)[:400]}")
        return
    newver = resp.get("version") if isinstance(resp, dict) else "?"
    print(f"  PATCH OK -> {st}, new version v{newver}")
    json.dump(spec, open(f"{MODDIR}/{authored_out}", "w"), indent=2)
    print(f"  authored spec updated -> {MODDIR}/{authored_out}")
    print(f"  dims/section now: { {k: len(v) for k, v in section_dim_ids(spec).items()} }")


def main():
    print("Reflection-alignment PATCH for the live reviewer + adversary modules.")
    run_one("review", REVIEWER_ID, add_reviewer_dims, "01_task_quality_review_v3.json")
    run_one("adversary", ADVERSARY_ID, add_adversary_dim, "02_reward_hack_adversary_v2.json")
    print("\nDone. Re-run is a no-op (idempotent). Roll back with the saved _snapshot_*.json.")


if __name__ == "__main__":
    main()
