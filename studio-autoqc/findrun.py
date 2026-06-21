#!/usr/bin/env python3
"""READ-ONLY: find a COMPLETED audit for the existing module to learn the real run mechanism."""
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
EXIST = "qcspec_7bddfd703a12994dbc31fd1b"


def key():
    for line in open(f"{ROOT}/.env"):
        if line.startswith("RLS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")


H = {"Authorization": f"Bearer {key()}", "X-Company-Id": COMP, "X-Campaign-Id": CAMP,
     "X-Account-Id": ACCT, "User-Agent": "curl/8.7.1"}


def get(path, **params):
    r = requests.get(f"{API}{path}", headers=H, params=params or None, timeout=120)
    ct = r.headers.get("content-type", "")
    return r.status_code, (r.json() if ct.startswith("application/json") else r.text)


def rows_of(data):
    return data.get("audits", data if isinstance(data, list) else []) if isinstance(data, (dict, list)) else []

# 1. try several query shapes to list audits for the existing spec
print("== probe query shapes for /qc-audits/ ==")
for params in [
    {"qc_spec_id": EXIST, "subject_kind": "task", "limit": 50},
    {"qc_spec_id": EXIST, "limit": 50},
    {"campaign_id": CAMP, "qc_spec_id": EXIST, "limit": 50},
    {"world_id": WORLD, "qc_spec_id": EXIST, "limit": 50},
    {"subject_kind": "task", "limit": 50},
]:
    st, data = get("/qc-audits/", **params)
    rows = rows_of(data)
    statuses = {}
    for r in rows:
        statuses[r.get("status")] = statuses.get(r.get("status"), 0) + 1
    print(f"  {params} -> http={st} rows={len(rows)} statuses={statuses}")
    if st == 200 and rows:
        found = rows

# 2. find any non-pending audit and dump its execution mechanism
print("\n== look for a completed/run audit to learn the mechanism ==")
all_rows = []
st, data = get("/qc-audits/", qc_spec_id=EXIST, subject_kind="task", limit=200)
all_rows = rows_of(data)
done = [r for r in all_rows if r.get("status") not in ("pending", None)]
print(f"  total={len(all_rows)} non-pending={len(done)}")
sample = (done or all_rows)[:1]
for r in sample:
    print(f"\n  audit {r.get('qc_audit_id')} status={r.get('status')} source={r.get('source')}")
    for k in ("execution", "runtime_config", "outcome", "failure_reason", "qc_spec_version", "created_by"):
        print(f"    {k}: {json.dumps(r.get(k), default=str)[:600]}")

# 3. inspect the world config for an automation / remix that drives qc
print("\n== world: any qc automation / remix wiring? ==")
st, world = get(f"/worlds/{WORLD}")
if isinstance(world, dict):
    for k in world.keys():
        if any(s in k.lower() for s in ("remix", "automat", "qc", "audit", "hook")):
            print(f"    {k}: {json.dumps(world.get(k), default=str)[:300]}")
