#!/usr/bin/env python3
"""Smoke-test the deployed TB AutoQC modules: trigger audits on a few tasks, poll, print per-dim.

Targets known-defective baked-leak tasks + a control. Triggers all 3 modules per task.
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
WORLD = "world_2c7cdb23737845ad83a9acfa1aa8c25b"
ROOT = "/Users/mahmoodmapara/Desktop/terminal-bench-qc"

MODULES = {
    "Task Quality Review": "qcspec_7bddfd703a12994dbc31fd1b",
    "Reward-Hack / Adversary QC": "qcspec_e5cb0f9be6123abea7d720c4",
    "Static Structural QC": "qcspec_7e5dbd46cf6de18e0a08d2a6",
}
TARGETS = [
    "cloud-cost-anomaly-auditor",          # known defective: baked-answer leak
    "dra-calibration-integrity-pipeline",  # known defective: baked-answer leak
    "edge-telemetry-correlation",          # control (unknown)
]


def key():
    for line in open(f"{ROOT}/.env"):
        if line.startswith("RLS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")


H = {"Authorization": f"Bearer {key()}", "X-Campaign-Id": CAMP, "X-Company-Id": COMP,
     "X-Account-Id": ACCT, "User-Agent": "curl/8.7.1", "Content-Type": "application/json"}


def get(path, **params):
    r = requests.get(f"{API}{path}", headers=H, params=params or None, timeout=120)
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)


def post(path, body):
    r = requests.post(f"{API}{path}", headers=H, data=json.dumps(body), timeout=120)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text


def resolve_ids():
    import tempfile
    cache = os.path.join(tempfile.gettempdir(), f"studio_tasks_{WORLD}.json")
    if os.path.isfile(cache):
        tasks = json.load(open(cache))
    else:
        st, data = get(f"/tasks/world/{WORLD}/full")
        tasks = data.get("tasks", data if isinstance(data, list) else [])
        json.dump(tasks, open(cache, "w"))
    by = {}
    for t in tasks:
        nm = t.get("task_name") or t.get("name")
        if nm in TARGETS:
            by[nm] = t.get("task_id") or t.get("id")
    return by


def main():
    ids = resolve_ids()
    print("resolved:", json.dumps(ids, indent=2))
    triggered = []
    print("\n== Trigger audits ==")
    for name, tid in ids.items():
        if not tid:
            print(f"  ! {name}: not found"); continue
        for mname, sid in MODULES.items():
            st, resp = post("/qc-audits/", {"qc_spec_id": sid, "subject_kind": "task",
                                            "subject_id": tid, "source": "automatic",
                                            "function": None, "dimensions_filter": None,
                                            "subject_params": None})
            aid = resp.get("qc_audit_id") or resp.get("id") if isinstance(resp, dict) else None
            print(f"  {name[:30]:30s} | {mname[:26]:26s} -> {st} audit={aid}"
                  + ("" if st < 300 else f"  ERR={str(resp)[:200]}"))
            triggered.append((name, mname, sid, tid, aid))
        time.sleep(0.3)

    print("\n== Poll for completion (up to 8 min) ==")
    deadline = 0
    done = {}
    for _ in range(48):  # ~8 min at 10s
        pending = [t for t in triggered if (t[0], t[1]) not in done]
        if not pending:
            break
        for name, mname, sid, tid, aid in pending:
            st, data = get("/qc-audits/", subject_kind="task", subject_id=tid, qc_spec_id=sid)
            rows = data.get("audits", data if isinstance(data, list) else []) if isinstance(data, (dict, list)) else []
            row = rows[0] if rows else None
            status = (row or {}).get("status")
            if status and status not in ("pending", "queued", "running", "in_progress"):
                done[(name, mname)] = row
        print(f"  {len(done)}/{len(triggered)} complete...", flush=True)
        if len(done) == len(triggered):
            break
        time.sleep(10)

    print("\n== Results ==")
    out = {}
    for (name, mname), row in done.items():
        res = row.get("outcome") or row.get("result") or row.get("output") or row.get("diagnostics") or row
        out.setdefault(name, {})[mname] = {"status": row.get("status"), "outcome": res}
        print(f"\n### {name} | {mname} [{row.get('status')}]  global_pass={res.get('global_pass') if isinstance(res,dict) else '?'}")
        # try to surface per-dim diagnostics
        diag = res if isinstance(res, list) else (
            res.get("diagnostics") or res.get("dimensions") or res.get("results") if isinstance(res, dict) else None)
        if isinstance(diag, list):
            for d in diag:
                if isinstance(d, dict):
                    print(f"   {str(d.get('status','?')).upper():7s} {str(d.get('text') or d.get('name'))[:140]}")
        else:
            print("   (raw)", str(res)[:400])
    json.dump(out, open(f"{ROOT}/_local/tb_modules/_smoke_results.json", "w"), indent=2, default=str)
    print(f"\nsaved -> _local/tb_modules/_smoke_results.json")
    miss = [(n, m) for (n, m, s, t, a) in triggered if (n, m) not in done]
    if miss:
        print("STILL PENDING (not complete in window):", miss)


if __name__ == "__main__":
    main()
