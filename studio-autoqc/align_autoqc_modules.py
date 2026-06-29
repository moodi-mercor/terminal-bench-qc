#!/usr/bin/env python3
"""Align the live AutoQC modules with the current hardened detectors + QC_GUIDE.

From the coverage audit (detectors vs the 4 live qc-specs), apply:
  STATIC (qcspec_7e5dbd46):
    + ss_11_brittle_verifier  (literal-only / wall-clock / filename-encodes / self-consistent — NEUTRAL)
    + ss_12_build_isolation   (chmod-not-a-guard / verifier-helper-in-environment — NEUTRAL)
    ~ ss_04 notes: reward-pre-created, skipped-scored-test, empty-parametrize (advisory)
    ~ preamble: list the new gates
  ADVERSARY (qcspec_e5cb0f9b):
    ~ cv_05 notes + preamble: re-exec is NOT a defense for grader-overwrite (matches reconcile HARD set)
    ~ cv_06 notes: config/spec target file is task input, not leaked truth
  REVIEWER (qcspec_7bddfd70):
    ~ solution_correctness: reference-solve-reads-truth (solve reads the verifier's truth path)

GET-merge-PATCH on each LIVE spec; snapshots first; idempotent (marker-guarded).
Harness gates the PATCH — approve each.
"""
import json
import os
import sys
import requests

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
API = "https://api.studio.mercor.com"
MODDIR = os.path.join(ROOT, "_local", "tb_modules")
STATIC = "qcspec_7e5dbd46cf6de18e0a08d2a6"
ADVERSARY = "qcspec_e5cb0f9be6123abea7d720c4"
REVIEWER = "qcspec_7bddfd703a12994dbc31fd1b"


def key():
    for line in open(f"{ROOT}/.env"):
        if line.startswith("RLS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("no RLS_KEY")


H = {"Authorization": f"Bearer {key()}", "X-Campaign-Id": "camp_4e196b1414a1499db54b43233104b0a7",
     "X-Company-Id": "comp_2fa4115109d741cd94a3c409ed89e61f",
     "X-Account-Id": "acct_85b680d4c5ba49a29f19c173672aebea",
     "User-Agent": "curl/8.7.1", "Content-Type": "application/json"}

BRITTLE_DIM = {
    "dim_id": "ss_11_brittle_verifier",
    "name": "Brittle / Weak Verifier (literal-only / wall-clock / filename / self-consistent)",
    "description": "NEUTRAL (advisory, WARN-level) when the verifier is brittle in a named, decidable way.",
    "pass_criteria": "The verifier derives or executes to obtain the expected value (recompute/hash/re-exec/mutated rerun) OR the single canonical serialization is pinned by instruction.md; it does not gate on measured wall-clock time, does not decide validity from a filename, and does not read its 'expected' from agent-writable space (/app,/workspace,/data,cwd).",
    "fail_criteria": "Never emit fail. These are WARN-severity advisory in the deterministic pipeline.",
    "neutral_criteria": "NEUTRAL naming the pattern + file:line: every scored assertion compares only to hardcoded literals with no functional/recompute signal (literal-only-verifier); the test measures time.time()/perf_counter elapsed and asserts a bound on it (wall-clock-dependent-verifier); pass/validity is decided from os.path.basename/filename matched against a validity word, not content (filename-encodes-answer); or the expected/reference value is read via open() from the agent's writable tree (verifier-self-consistent). Also NEUTRAL if tests/ were not staged.",
    "notes": "PRECISION: a bare time.time() for a filename/seed or a startup sleep is NOT wall-clock dependence — require an elapsed-bound assert. A recompute (sum/Counter/hashlib/comprehension) is NOT literal-only even if a literal also appears. The single genuinely-canonical serialization named in the spec is NOT brittle. Deterministic baked truth for a find-ALL task is legitimate, not literal-only.",
}
BUILD_ISO_DIM = {
    "dim_id": "ss_12_build_isolation",
    "name": "Build Isolation (decorative chmod / verifier helper in agent image)",
    "description": "NEUTRAL (advisory) when verifier/truth isolation relies on the agent NOT being root, or verifier logic is baked into agent-writable space.",
    "pass_criteria": "Truth and grader logic live under tests/ (verify-time mount) or are regenerated at verify time; file permissions are not the isolation boundary; no verifier/grader-named helper (verify*/grader*/check*/judge*/scorer*/oracle*) is baked under environment/ into the agent image.",
    "fail_criteria": "Never emit fail. Build-isolation hygiene is WARN-severity advisory.",
    "neutral_criteria": "NEUTRAL naming the pattern + file:line: the build does chmod 400/000/440/200 on a truth/grader file (truthy or verifier-named) while the Dockerfile has no non-root USER drop, so root reads it regardless and can restore the mode (chmod-not-a-guard); OR a verifier/grader-named helper is baked under environment/ AND referenced by tests/test.sh or test_outputs.py (verifier-helper-in-environment). Also NEUTRAL if the Dockerfile/environment were not staged.",
    "notes": "A chmod under /tests or .truth is the verify-time mount — do not flag. SUPERSEDED by ss_04's agent-writable-verifier FAIL: if ss_04 already FAILed the same copied grader, do not also flag it here. A helper the reference solve.sh regenerates is a real artifact, not a leak.",
}


def get_spec(sid):
    return requests.get(f"{API}/qc-specs/{sid}", headers=H, timeout=90).json()


def patch(sid, full, why):
    spec = full["spec"]
    body = {"spec": spec, "name": full.get("name"),
            "description": (spec.get("rubric", {}).get("description") or "")[:480]}
    r = requests.patch(f"{API}/qc-specs/{sid}", headers=H, data=json.dumps(body), timeout=120)
    ok = r.status_code < 300
    print(f"  PATCH {sid} ({why}): {r.status_code}" + ("" if ok else f" {r.text[:200]}")
          + (f" -> v{r.json().get('version')}" if ok else ""))
    return ok


def snap(full, label):
    os.makedirs(MODDIR, exist_ok=True)
    json.dump(full, open(f"{MODDIR}/_snapshot_{label}_v{full.get('version')}.json", "w"), indent=1)


def dim(spec, did):
    for s in spec["rubric"]["sections"]:
        for d in s["dimensions"]:
            if d["dim_id"] == did:
                return d
    return None


def main():
    # ---- STATIC ----
    f = get_spec(STATIC); snap(f, "static"); spec = f["spec"]; have = {d["dim_id"] for s in spec["rubric"]["sections"] for d in s["dimensions"]}
    changed = False
    for s in spec["rubric"]["sections"]:
        if s["name"].startswith("Leakage") and "ss_11_brittle_verifier" not in have:
            s["dimensions"].append(json.loads(json.dumps(BRITTLE_DIM))); changed = True
        if s["name"].startswith("Environment Fairness") and "ss_12_build_isolation" not in have:
            s["dimensions"].append(json.loads(json.dumps(BUILD_ISO_DIM))); changed = True
    d04 = dim(spec, "ss_04_reward_hack")
    if d04 and "reward-pre-created" not in d04["notes"]:
        d04["notes"] += (" Also surface (advisory -> NEUTRAL): a reward.txt baked in environment/ or "
                         "written/COPY'd by the Dockerfile so the task starts already passed "
                         "(reward-pre-created); a scored test decorated @skip/@skipif/@xfail "
                         "(skipped-scored-test); a @parametrize over an EMPTY list so zero cases run "
                         "(empty-parametrize).")
        changed = True
    pre = spec["execution"]["default"]
    if "ss_11" not in (pre.get("system_prompt_preamble") or "") and pre.get("system_prompt_preamble"):
        pre["system_prompt_preamble"] += ("\n10. Compiled-Bytecode Leak — FAIL on a shipped .pyc/__pycache__ "
            "or a `python -m`/local-import generator removed without bytecode cleanup (ss_10).\n"
            "11. Brittle / Weak Verifier (advisory -> NEUTRAL) — literal-only, wall-clock-elapsed assert, "
            "filename-encodes-validity, or expected-read-from-agent-tree (ss_11).\n"
            "12. Build Isolation (advisory -> NEUTRAL) — decorative chmod of a truth/grader while root, or a "
            "verifier-named helper baked under environment/ (ss_12).")
        changed = True
    if changed:
        patch(STATIC, f, "ss_11+ss_12+ss_04+preamble")
    else:
        print("  STATIC already aligned")

    # ---- ADVERSARY ----
    f = get_spec(ADVERSARY); snap(f, "adversary"); spec = f["spec"]; changed = False
    cv05 = dim(spec, "cv_05_tamper_scoring_path")
    if cv05 and "Re-exec is NOT a defense" not in cv05["notes"]:
        cv05["notes"] += (" Re-exec is NOT a defense here: when the grader the verifier invokes IS the "
                          "agent-writable in-image script, the verifier re-executing the agent's program is "
                          "precisely the hole — overwriting it forces the pass. Only an INDEPENDENT defense the "
                          "agent cannot forge (recompute/hash, mutated/regenerated-input rerun, or a source-grep "
                          "against a tests/-resident reference) makes this PASS. Do not count re-exec-agent.")
        changed = True
    cv06 = dim(spec, "cv_06_echo_leaked_truth")
    if cv06 and "non-truthy CONFIG/SPEC" not in cv06["notes"]:
        cv06["notes"] += (" A non-truthy CONFIG/SPEC file the verifier reads as the TARGET to satisfy "
                          "(a version/threshold/expected config — *.json/*.yaml/*.toml not named like an answer) "
                          "is task INPUT, not leaked ground truth — PASS, do not surface it.")
        changed = True
    pre = spec["execution"]["default"]
    pp = pre.get("system_prompt_preamble") or ""
    marker = "the verifier RE-EXECUTES the agent's produced program (not just reads its output);"
    if marker in pp and "EXCEPT for cv_05" not in pp:
        pre["system_prompt_preamble"] = pp.replace(marker, marker +
            " (EXCEPT for cv_05 grader-overwrite: re-exec does NOT defend when the re-executed program is itself the agent-writable grader);")
        changed = True
    if changed:
        patch(ADVERSARY, f, "cv_05 re-exec + cv_06 config + preamble")
    else:
        print("  ADVERSARY already aligned")

    # ---- REVIEWER ----
    f = get_spec(REVIEWER); snap(f, "reviewer"); spec = f["spec"]; changed = False
    sc = dim(spec, "solution_correctness")
    if sc and "reference-solve-reads-truth" not in sc.get("fail_criteria", ""):
        sc["fail_criteria"] = sc.get("fail_criteria", "") + (" Also FAIL if solve.sh reads the exact "
            "truth/golden/expected path the verifier compares against (and does not itself PRODUCE that path) "
            "— the reference 'solves' by reading the answer key (reference-solve-reads-truth); a path the "
            "instruction references, or one the reference writes as its deliverable, is not this defect.")
        changed = True
    if changed:
        patch(REVIEWER, f, "solution_correctness reference-reads-truth")
    else:
        print("  REVIEWER already aligned")


if __name__ == "__main__":
    main()
