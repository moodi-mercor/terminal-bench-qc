#!/usr/bin/env python3
"""Deploy the 3 TB AutoQC modules. Additive: PATCH the existing reviewer, POST two new.

Order: snapshot existing -> reconfirm no name collision -> PATCH M1 -> POST M2 -> POST M3
-> verify each. Prints status for every write. Reads RLS_KEY from .env.
"""
import json
import os
import sys
import time
import requests

API = "https://api.studio.mercor.com"
CAMP = "camp_4e196b1414a1499db54b43233104b0a7"
COMP = "comp_2fa4115109d741cd94a3c409ed89e61f"
ACCT = "acct_85b680d4c5ba49a29f19c173672aebea"
EXISTING_ID = "qcspec_7bddfd703a12994dbc31fd1b"   # live "Task Quality Review"
ROOT = "/Users/mahmoodmapara/Desktop/terminal-bench-qc"
MODDIR = f"{ROOT}/_local/tb_modules"


def key():
    for line in open(f"{ROOT}/.env"):
        if line.startswith("RLS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("no RLS_KEY")


H = {"Authorization": f"Bearer {key()}", "X-Campaign-Id": CAMP, "X-Company-Id": COMP,
     "X-Account-Id": ACCT, "User-Agent": "curl/8.7.1", "Content-Type": "application/json"}


def get(path, **params):
    r = requests.get(f"{API}{path}", headers=H, params=params or None, timeout=90, allow_redirects=True)
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)


def write(method, path, body):
    fn = getattr(requests, method)
    r = fn(f"{API}{path}", headers=H, data=json.dumps(body), timeout=120, allow_redirects=True)
    if r.status_code in (301, 307, 308) and "location" in r.headers:
        r = fn(r.headers["location"], headers=H, data=json.dumps(body), timeout=120)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text


def load(fn):
    return json.load(open(f"{MODDIR}/{fn}"))


def desc(spec):
    return (spec.get("rubric", {}).get("description") or "")[:480]


def main():
    print("== 0. Reconfirm + snapshot ==")
    st, specs = get("/qc-specs/", campaign_id=CAMP)
    mods = specs.get("specs", []) if isinstance(specs, dict) else (specs or [])
    latest = [m for m in mods if m.get("is_latest", True)]
    names = {m.get("name"): m.get("qc_spec_id") for m in latest}
    print(f"  existing latest modules: {names}")
    assert EXISTING_ID in names.values(), "PATCH target not found among latest!"
    for newname in ("Reward-Hack / Adversary QC", "Static Structural QC"):
        if newname in names:
            sys.exit(f"  ABORT: name collision on {newname!r} -> {names[newname]}")
    st, full = get(f"/qc-specs/{EXISTING_ID}")
    snap = f"{MODDIR}/_snapshot_existing_{EXISTING_ID}_v{(full.get('version') if isinstance(full,dict) else '?')}.json"
    json.dump(full, open(snap, "w"), indent=2)
    print(f"  snapshot saved -> {snap}")

    print("== 1. PATCH Module 1 (Task Quality Review) ==")
    m1 = load("01_task_quality_review.json")
    st, resp = write("patch", f"/qc-specs/{EXISTING_ID}",
                     {"spec": m1, "name": "Task Quality Review", "description": desc(m1)})
    print(f"  PATCH -> {st}")
    if st >= 300:
        print(f"  BODY: {str(resp)[:600]}"); sys.exit(1)
    new_ver = resp.get("version") if isinstance(resp, dict) else "?"
    print(f"  OK new version={new_ver} id={resp.get('qc_spec_id') if isinstance(resp,dict) else '?'}")

    posted = {}
    for fn, name in [("02_reward_hack_adversary.json", "Reward-Hack / Adversary QC"),
                     ("03_static_structural.json", "Static Structural QC")]:
        print(f"== 2. POST {name} ==")
        spec = load(fn)
        body = {"campaign_id": CAMP, "scope_type": "campaign", "scope_id": CAMP,
                "subject_kind": "task", "name": name, "description": desc(spec), "spec": spec}
        st, resp = write("post", "/qc-specs/", body)
        print(f"  POST -> {st}")
        if st >= 300:
            print(f"  BODY: {str(resp)[:800]}"); sys.exit(1)
        sid = resp.get("qc_spec_id") if isinstance(resp, dict) else None
        posted[name] = sid
        print(f"  OK id={sid} version={resp.get('version') if isinstance(resp,dict) else '?'}")
        time.sleep(0.5)

    print("== 3. Verify (GET /qc-specs/) ==")
    st, specs = get("/qc-specs/", campaign_id=CAMP)
    mods = specs.get("specs", []) if isinstance(specs, dict) else (specs or [])
    for m in mods:
        if m.get("is_latest", True):
            print(f"  - {str(m.get('name')):28s} v{m.get('version')} ({m.get('qc_spec_id')})")
    json.dump({"patched": EXISTING_ID, "posted": posted}, open(f"{MODDIR}/_deployed_ids.json", "w"), indent=2)
    print(f"  deployed ids -> {MODDIR}/_deployed_ids.json")


if __name__ == "__main__":
    main()
