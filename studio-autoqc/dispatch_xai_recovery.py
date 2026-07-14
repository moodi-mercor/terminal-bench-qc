#!/usr/bin/env python3
"""Opus-4.8 avg@8 recovery run for the XAI OR-clause: the 1,763 hard tasks
(0/8 on v9 proxy, not brittle/leaked, no frontier data yet). >=1 Opus pass => eligible.
Opus-only (orch v4, effort=high) x Terminus-2. Dry-run unless --execute."""
import json,sys,csv,requests
sys.path.insert(0,"studio-autoqc")
import dispatch_eval_avg8 as D
ids=[r["task_id"] for r in csv.DictReader(open("_local/xai_needs_opus_run.csv"))]
RUNS=8
body=D.build(ids, RUNS, "XAI OR-clause recovery — Opus-4.8 avg@8 (1763 hard)", [D.OPUS])
n=len(body["trajectory_request"])
print(f"tasks: {len(ids)} | runs: {RUNS} | model: {D.OPUS[2]} | trajectories: {n}")
print(f"agent: {D.AGENT_ID} v{D.AGENT_VERSION} (Terminus-2)")
if n>50000: sys.exit(f"ABORT {n}>50k")
if "--execute" not in sys.argv:
    print("\nDRY-RUN — re-run with --execute to dispatch."); sys.exit()
r=requests.post(f"{D.API}/orchestration/trajectories/batch",headers=D.headers(),data=json.dumps(body),timeout=300)
print("HTTP",r.status_code)
try:
    resp=r.json(); bid=resp.get("trajectory_batch_id") or resp.get("batch_id") or resp.get("id")
    print("NEW BATCH ID:",bid); json.dump({"batch_id":bid,"n":n,"tasks":len(ids)},open("_local/xai_recovery_batch.json","w"))
except Exception: print(r.text[:800])
