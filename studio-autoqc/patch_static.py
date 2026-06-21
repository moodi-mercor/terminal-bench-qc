#!/usr/bin/env python3
"""PATCH the Static Structural module to the v2 (WARN->NEUTRAL) calibration."""
import json
import requests

API = "https://api.studio.mercor.com"
CAMP = "camp_4e196b1414a1499db54b43233104b0a7"
COMP = "comp_2fa4115109d741cd94a3c409ed89e61f"
ACCT = "acct_85b680d4c5ba49a29f19c173672aebea"
SID = "qcspec_7e5dbd46cf6de18e0a08d2a6"
ROOT = "/Users/mahmoodmapara/Desktop/terminal-bench-qc"


def key():
    for line in open(f"{ROOT}/.env"):
        if line.startswith("RLS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")


H = {"Authorization": f"Bearer {key()}", "X-Campaign-Id": CAMP, "X-Company-Id": COMP,
     "X-Account-Id": ACCT, "User-Agent": "curl/8.7.1", "Content-Type": "application/json"}
import sys
specfile = sys.argv[1] if len(sys.argv) > 1 else f"{ROOT}/_local/tb_modules/03_static_structural_v3.json"
spec = json.load(open(specfile if specfile.startswith("/") else f"{ROOT}/_local/tb_modules/{specfile}"))

# snapshot current first
cur = requests.get(f"{API}/qc-specs/{SID}", headers=H, timeout=60).json()
json.dump(cur, open(f"{ROOT}/_local/tb_modules/_snapshot_static_v{cur.get('version')}.json", "w"), indent=2)
print(f"snapshot static v{cur.get('version')} saved")

r = requests.patch(f"{API}/qc-specs/{SID}", headers=H,
                   data=json.dumps({"spec": spec, "name": "Static Structural QC",
                                    "description": spec["rubric"]["description"][:480]}), timeout=120)
print("PATCH ->", r.status_code)
try:
    print("new version:", r.json().get("version"))
except Exception:
    print(r.text[:400])
