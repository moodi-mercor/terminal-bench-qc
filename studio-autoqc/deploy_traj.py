#!/usr/bin/env python3
"""Deploy Module 4 — Verifier Audit (Trajectory). POST-new, additive. Verify after."""
import json
import sys
import requests

API = "https://api.studio.mercor.com"
CAMP = "camp_4e196b1414a1499db54b43233104b0a7"
COMP = "comp_2fa4115109d741cd94a3c409ed89e61f"
ACCT = "acct_85b680d4c5ba49a29f19c173672aebea"
ROOT = "/Users/mahmoodmapara/Desktop/terminal-bench-qc"
NAME = "Verifier Audit"


def key():
    for line in open(f"{ROOT}/.env"):
        if line.startswith("RLS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")


H = {"Authorization": f"Bearer {key()}", "X-Campaign-Id": CAMP, "X-Company-Id": COMP,
     "X-Account-Id": ACCT, "User-Agent": "curl/8.7.1", "Content-Type": "application/json"}


def get(path, **p):
    r = requests.get(f"{API}{path}", headers=H, params=p or None, timeout=90)
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)


def main():
    spec = json.load(open(f"{ROOT}/_local/tb_modules/04_verifier_audit_trajectory.json"))
    st, specs = get("/qc-specs/", campaign_id=CAMP)
    mods = specs.get("specs", []) if isinstance(specs, dict) else (specs or [])
    names = {m.get("name") for m in mods if m.get("is_latest", True)}
    if NAME in names:
        sys.exit(f"ABORT: name collision on {NAME!r}")
    body = {"campaign_id": CAMP, "scope_type": "campaign", "scope_id": CAMP,
            "subject_kind": "trajectory", "name": NAME,
            "description": (spec.get("rubric", {}).get("description") or "")[:480], "spec": spec}
    r = requests.post(f"{API}/qc-specs/", headers=H, data=json.dumps(body), timeout=120)
    try:
        resp = r.json()
    except Exception:
        resp = r.text
    print(f"POST /qc-specs/ (subject=trajectory) -> {r.status_code}")
    if r.status_code >= 300:
        print("BODY:", str(resp)[:800]); sys.exit(1)
    sid = resp.get("qc_spec_id") if isinstance(resp, dict) else None
    print(f"OK deployed id={sid} version={resp.get('version') if isinstance(resp,dict) else '?'}")
    json.dump({"verifier_audit_trajectory": sid}, open(f"{ROOT}/_local/tb_modules/_deployed_traj_id.json", "w"), indent=2)


if __name__ == "__main__":
    main()
