#!/usr/bin/env python3
"""READ-ONLY diagnostic: why are audits pending / why 0 rows for new modules."""
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
NEW = {"Reward-Hack / Adversary QC": "qcspec_e5cb0f9be6123abea7d720c4",
       "Static Structural QC": "qcspec_7e5dbd46cf6de18e0a08d2a6"}


def key():
    for line in open(f"{ROOT}/.env"):
        if line.startswith("RLS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")


H = {"Authorization": f"Bearer {key()}", "X-Campaign-Id": CAMP, "X-Company-Id": COMP,
     "X-Account-Id": ACCT, "User-Agent": "curl/8.7.1"}


def get(path, **params):
    r = requests.get(f"{API}{path}", headers=H, params=params or None, timeout=120)
    ct = r.headers.get("content-type", "")
    return r.status_code, (r.json() if ct.startswith("application/json") else r.text)


cache = os.path.join(tempfile.gettempdir(), f"studio_tasks_{WORLD}.json")
tasks = json.load(open(cache)) if os.path.isfile(cache) else []
tid = next((t.get("task_id") or t.get("id") for t in tasks
            if (t.get("task_name") or t.get("name")) == "cloud-cost-anomaly-auditor"), None)

print(f"== ALL audits on cloud-cost-anomaly-auditor ({tid}) — no spec filter ==")
st, data = get("/qc-audits/", subject_kind="task", subject_id=tid)
rows = data.get("audits", data if isinstance(data, list) else []) if isinstance(data, (dict, list)) else []
print(f"  http={st} total_rows={len(rows)}")
for r in rows:
    print(f"   {r.get('qc_audit_id')} spec={r.get('qc_spec_id')} v{r.get('qc_spec_version')} "
          f"status={r.get('status')} source={r.get('source')} created={r.get('created_at')}")

print("\n== full record of one PENDING audit (execution/runtime/failure) ==")
pend = next((r for r in rows if r.get("status") == "pending"), rows[0] if rows else None)
if pend:
    for k in ("status", "failure_reason", "execution", "runtime_config", "outcome"):
        print(f"   {k}: {json.dumps(pend.get(k), default=str)[:500]}")

print("\n== new modules state (GET /qc-specs/{id}) ==")
for name, sid in NEW.items():
    st, spec = get(f"/qc-specs/{sid}")
    keys = sorted(spec.keys()) if isinstance(spec, dict) else spec
    print(f"   {name}: http={st} is_latest={spec.get('is_latest') if isinstance(spec,dict) else '?'} "
          f"version={spec.get('version') if isinstance(spec,dict) else '?'} status={spec.get('status') if isinstance(spec,dict) else '?'}")

print("\n== any audits at all for the new specs? (campaign-wide) ==")
for name, sid in NEW.items():
    st, data = get("/qc-audits/", qc_spec_id=sid)
    rows = data.get("audits", data if isinstance(data, list) else []) if isinstance(data, (dict, list)) else []
    print(f"   {name}: http={st} rows={len(rows)}")
