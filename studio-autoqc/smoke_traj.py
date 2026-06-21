#!/usr/bin/env python3
"""Smoke-test the deployed Verifier Audit (trajectory) module.

Triggers it on a few real trajectories incl. the abandoned-cart split-score set,
polls, and prints FN/FP/consistency verdicts. THE key check: do the dims return
real verdicts, or NEUTRAL 'trajectory not staged' (selector needs fixing)?

Reads the deployed id from _deployed_traj_id.json (run deploy_traj.py first).
"""
import json
import sys
import time
import requests

API = "https://api.studio.mercor.com"
CAMP = "camp_4e196b1414a1499db54b43233104b0a7"
COMP = "comp_2fa4115109d741cd94a3c409ed89e61f"
ACCT = "acct_85b680d4c5ba49a29f19c173672aebea"
ROOT = "/Users/mahmoodmapara/Desktop/terminal-bench-qc"

# abandoned-cart-releaser: split-score (opus 0.0/5-3 = FN candidate; gpt-5.5 1.0/8-0 = clean)
TRAJ = [
    ("abandoned-cart opus 0.0", "traj_deb90a5e4e544550b87c1973d1733bc2"),
    ("abandoned-cart gemini 0.0", "traj_dcb7630c6df045d2be29c76fd206bf17"),
    ("abandoned-cart gpt5.5 1.0", "traj_a4e6e0f35c6c483baa981503a4bbd664"),
]


def key():
    for line in open(f"{ROOT}/.env"):
        if line.startswith("RLS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")


H = {"Authorization": f"Bearer {key()}", "X-Campaign-Id": CAMP, "X-Company-Id": COMP,
     "X-Account-Id": ACCT, "User-Agent": "curl/8.7.1", "Content-Type": "application/json"}


def get(path, **p):
    r = requests.get(f"{API}{path}", headers=H, params=p or None, timeout=120)
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)


def post(path, body):
    r = requests.post(f"{API}{path}", headers=H, data=json.dumps(body), timeout=120)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text


def main():
    sid = json.load(open(f"{ROOT}/_local/tb_modules/_deployed_traj_id.json"))["verifier_audit_trajectory"]
    print(f"module: {sid}")
    triggered = []
    for label, tid in TRAJ:
        st, resp = post("/qc-audits/", {"qc_spec_id": sid, "subject_kind": "trajectory",
                                        "subject_id": tid, "source": "automatic",
                                        "function": None, "dimensions_filter": None, "subject_params": None})
        aid = resp.get("qc_audit_id") or resp.get("id") if isinstance(resp, dict) else None
        print(f"  trigger {label:28s} -> {st} audit={aid}" + ("" if st < 300 else f" ERR={str(resp)[:200]}"))
        triggered.append((label, tid))
        time.sleep(0.3)

    print("\n== poll (up to 8 min) ==")
    done = {}
    for _ in range(48):
        for label, tid in triggered:
            if label in done:
                continue
            st, data = get("/qc-audits/", subject_kind="trajectory", subject_id=tid, qc_spec_id=sid)
            rows = data.get("audits", data if isinstance(data, list) else []) if isinstance(data, (dict, list)) else []
            row = rows[0] if rows else None
            stt = (row or {}).get("status")
            if stt and stt not in ("pending", "queued", "running", "in_progress"):
                done[label] = row
        print(f"  {len(done)}/{len(triggered)} complete...", flush=True)
        if len(done) == len(triggered):
            break
        time.sleep(10)

    print("\n== results ==")
    out = {}
    for label, row in done.items():
        o = row.get("outcome") or {}
        print(f"\n### {label}  [{row.get('status')}] global_pass={o.get('global_pass')} counts={o.get('status_counts')}")
        for sec in (o.get("sections") or []):
            for dd in (sec.get("dimensions") or []):
                an = (dd.get("analysis") or dd.get("text") or "")
                staged = "NOT-STAGED" if "not staged" in an.lower() or "not_staged" in an.lower() else ""
                print(f"   {str(dd.get('status','?')).upper():7s} {dd.get('dimension') or dd.get('name')}: {an[:160]} {staged}")
        out[label] = o
    json.dump(out, open(f"{ROOT}/_local/tb_modules/_smoke_traj_results.json", "w"), indent=2, default=str)
    print("\nsaved -> _local/tb_modules/_smoke_traj_results.json")


if __name__ == "__main__":
    main()
