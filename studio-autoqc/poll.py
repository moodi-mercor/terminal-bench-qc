#!/usr/bin/env python3
"""READ-ONLY: re-poll the smoke-test audits and dump raw shape so we can see real status/results."""
import json
import os
import tempfile
import requests

API = "https://api.studio.mercor.com"
CAMP = "camp_4e196b1414a1499db54b43233104b0a7"
COMP = "comp_2fa4115109d741cd94a3c409ed89e61f"
ACCT = "acct_85b680d4c5ba49a29f19c173672aebea"
WORLD = "world_2c7cdb23737845ad83a9acfa1aa8c25b"
ROOT = "/Users/mahmoodmapara/Desktop/terminal-bench-qc"
MODULES = {
    "Task Quality Review": "qcspec_7bddfd703a12994dbc31fd1b",
    "Reward-Hack / Adversary QC": "qcspec_e5cb0f9be6123abea7d720c4",
    "Static Structural QC": "qcspec_7e5dbd46cf6de18e0a08d2a6",
}
TARGETS = ["cloud-cost-anomaly-auditor", "dra-calibration-integrity-pipeline", "edge-telemetry-correlation"]


def key():
    for line in open(f"{ROOT}/.env"):
        if line.startswith("RLS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")


H = {"Authorization": f"Bearer {key()}", "X-Campaign-Id": CAMP, "X-Company-Id": COMP,
     "X-Account-Id": ACCT, "User-Agent": "curl/8.7.1"}


def get(path, **params):
    r = requests.get(f"{API}{path}", headers=H, params=params or None, timeout=120)
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)


cache = os.path.join(tempfile.gettempdir(), f"studio_tasks_{WORLD}.json")
tasks = json.load(open(cache)) if os.path.isfile(cache) else []
ids = {t.get("task_name") or t.get("name"): (t.get("task_id") or t.get("id")) for t in tasks
       if (t.get("task_name") or t.get("name")) in TARGETS}

first = True
for name in TARGETS:
    tid = ids.get(name)
    for mname, sid in MODULES.items():
        st, data = get("/qc-audits/", subject_kind="task", subject_id=tid, qc_spec_id=sid)
        rows = data.get("audits", data if isinstance(data, list) else []) if isinstance(data, (dict, list)) else []
        if first and rows:
            print("RAW ROW KEYS:", sorted(rows[0].keys()))
            print("RAW ROW SAMPLE:", json.dumps(rows[0], default=str)[:900], "\n" + "=" * 70)
            first = False
        row = rows[0] if rows else None
        status = (row or {}).get("status")
        print(f"{name[:30]:30s} | {mname[:26]:26s} | http={st} rows={len(rows)} status={status}")
        if row and status in ("complete", "completed"):
            res = row.get("result") or row.get("output") or row.get("diagnostics") or row.get("dimensions")
            diag = res if isinstance(res, list) else (res.get("dimensions") if isinstance(res, dict) else None)
            if isinstance(diag, list):
                for d in diag:
                    if isinstance(d, dict):
                        print(f"     {str(d.get('status','?')).upper():7s} {str(d.get('text') or d.get('name'))[:130]}")
