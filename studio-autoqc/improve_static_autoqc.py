#!/usr/bin/env python3
"""Improve the live Static Structural QC module (qcspec_7e5dbd46...) with the detector
hardening learned from the full-corpus run:

  1. NEW dim ss_10_pycache_leak (Environment Fairness section) — compiled-bytecode
     leakage: a build-time generator leaves __pycache__/*.pyc that survives `rm` of its
     .py source; the agent can decompile it to recover the generation logic / baked
     answer. (~6,652 tasks world-wide — the biggest gap, entirely missing before.)
  2. ss_03 leakage — add the config/spec FP rule (a config/spec file the verifier reads
     as the TARGET is task input, not leaked truth; don't FAIL it).
  3. ss_04 reward-hack — add the defended-suppress rule (agent-writable-verifier is NOT
     a working hack when the verifier has an independent recompute/mutated/source-grep
     defense) + the concrete brittle-verifier classes as advisory.

GET-merge-PATCH on the LIVE spec (idempotent — skips if ss_10 already present),
snapshots before patching. Auth via RLS_KEY. Harness blocks the PATCH under the
auto-mode classifier — approve it.
"""
import json
import os
import sys
import requests

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
API = "https://api.studio.mercor.com"
SID = "qcspec_7e5dbd46cf6de18e0a08d2a6"
MODDIR = os.path.join(ROOT, "_local", "tb_modules")


def key():
    for line in open(f"{ROOT}/.env"):
        if line.startswith("RLS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("no RLS_KEY in .env")


H = {"Authorization": f"Bearer {key()}", "X-Campaign-Id": "camp_4e196b1414a1499db54b43233104b0a7",
     "X-Company-Id": "comp_2fa4115109d741cd94a3c409ed89e61f",
     "X-Account-Id": "acct_85b680d4c5ba49a29f19c173672aebea",
     "User-Agent": "curl/8.7.1", "Content-Type": "application/json"}

PYCACHE_DIM = {
    "dim_id": "ss_10_pycache_leak",
    "name": "Compiled-Bytecode Leak (__pycache__/.pyc)",
    "description": "FAIL if a .pyc / __pycache__ is shipped in the agent image, or the build runs a python generator and removes its .py source without cleaning the compiled bytecode.",
    "pass_criteria": "No .pyc file or __pycache__ directory is shipped into the agent image (under environment/ or any COPY'd path). If the build runs a python data/answer generator, it either keeps no source OR cleans the bytecode (rm -rf __pycache__ / find -name '*.pyc' -delete) or sets PYTHONDONTWRITEBYTECODE=1.",
    "fail_criteria": "A .pyc / __pycache__ is present in agent-readable space (environment/ or COPY'd into the image) — especially the compiled form of a generator/answer/solution script (generate*/gen_*/mutate*/verify*/solve*). OR the Dockerfile/setup runs `python <generator>.py` and then `rm`s that .py source but never removes __pycache__/*.pyc and does not set PYTHONDONTWRITEBYTECODE — the compiled bytecode of the hidden generator survives and is decompilable by the agent.",
    "neutral_criteria": "Neutral only if environment/Dockerfile and its setup scripts were not staged.",
    "notes": "The .pyc is the sibling of leftover-generator (gate 5): removing the .py but leaving __pycache__/*.pyc still leaks the logic — uncompyle6/decompyle3 recover near-original source. Cite the .pyc path or the generate+rm-without-cleanup line. tests/ and solution/ are verify-time mounts — a .pyc there is not agent-visible, do not flag it.",
}


def main():
    os.makedirs(MODDIR, exist_ok=True)
    full = requests.get(f"{API}/qc-specs/{SID}", headers=H, timeout=90).json()
    ver = full.get("version")
    json.dump(full, open(f"{MODDIR}/_snapshot_static_v{ver}.json", "w"), indent=2)
    print(f"live Static module v{ver} snapshot saved")
    spec = full["spec"]
    secs = spec["rubric"]["sections"]
    have = {d["dim_id"] for s in secs for d in s["dimensions"]}
    if "ss_10_pycache_leak" in have:
        print("ss_10_pycache_leak already present — idempotent no-op.")
        return
    for s in secs:
        for d in s["dimensions"]:
            if d["dim_id"] == "ss_03_leakage":
                d["notes"] += (" FP rule: a CONFIG/SPEC file the verifier reads to obtain the "
                               "TARGET (a version/threshold/expected config — *.json/*.yaml/*.toml "
                               "not named like an answer) is legitimate task input, not leaked "
                               "truth; do not FAIL it.")
            if d["dim_id"] == "ss_04_reward_hack":
                d["notes"] += (" Defended-suppress: agent-writable-verifier is NOT a working hack "
                               "when the verifier ALSO has an independent defense (recompute/hash, "
                               "mutated-rerun, or a source-grep against a tests/-resident reference) "
                               "— in that case do not FAIL. Brittle-verifier smells (only compares "
                               "to baked literals with no recompute; asserts on wall-clock elapsed "
                               "time; decides validity from a filename; reads 'expected' from the "
                               "agent's own output tree) are advisory -> NEUTRAL, not FAIL.")
        if s["name"].startswith("Environment Fairness"):
            s["dimensions"].append(json.loads(json.dumps(PYCACHE_DIM)))
    body = {"spec": spec, "name": full.get("name"),
            "description": (spec.get("rubric", {}).get("description") or "")[:480]}
    r = requests.patch(f"{API}/qc-specs/{SID}", headers=H, data=json.dumps(body), timeout=120)
    print("PATCH", r.status_code)
    if r.status_code < 300:
        nv = r.json().get("version")
        print(f"  OK -> v{nv}; dims {len(have)} -> {len(have)+1} (+ss_10_pycache_leak)")
        json.dump(spec, open(f"{MODDIR}/static_v{nv}_authored.json", "w"), indent=2)
    else:
        print("  FAILED:", r.text[:400])


if __name__ == "__main__":
    main()
