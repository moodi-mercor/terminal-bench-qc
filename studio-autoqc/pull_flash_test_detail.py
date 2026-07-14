#!/usr/bin/env python3
"""Pull per-attempt test_statuses for batch_7c4f522a into the detail.jsonl schema the
trajectory-audit triage expects. Reuses the cached trajectory IDs in rows_raw.jsonl and
the GDM-10k auth. Resumable via the output file.

Output row: {trajectory_id, task_name, task_id, status, model, score, test_statuses}
"""
import json, os, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

API = "https://api.studio.mercor.com"
KEY = "rls-sk-iuA_SCYWiui2xXr_HD7Rab0BfF5VPmDNlK6eAyLzcYw"
CAMP = "camp_0c1f9a9809604271a534edd77c3cbec1"
COMP = "comp_2fa4115109d741cd94a3c409ed89e61f"
ACCT = "acct_85b680d4c5ba49a29f19c173672aebea"
ROOT = "/Users/mahmoodmapara/Desktop/terminal-bench-qc"
BASE = f"{ROOT}/_local/batch_gemini_flash"
RAW = f"{BASE}/rows_raw.jsonl"
OUT = f"{ROOT}/_local/gemini_flash_qc/traj_detail.jsonl"
WORKERS = 16
H = {"Authorization": f"Bearer {KEY}", "X-Campaign-Id": CAMP, "X-Company-Id": COMP,
     "X-Account-Id": ACCT, "User-Agent": "curl/8.7.1"}


def fetch(row):
    for a in range(4):
        try:
            r = requests.get(f"{API}/trajectories/{row['trajectory_id']}", headers=H, timeout=120)
            if r.status_code == 200:
                to = (r.json() or {}).get("trajectory_output") or {}
                return {"trajectory_id": row["trajectory_id"], "task_name": row.get("task_name"),
                        "task_id": row.get("task_id"), "status": "completed",
                        "model": to.get("model"), "score": to.get("score"),
                        "test_statuses": to.get("test_statuses")}
        except Exception:
            pass
        time.sleep(1.5 * (a + 1))
    return {"trajectory_id": row["trajectory_id"], "task_name": row.get("task_name"),
            "task_id": row.get("task_id"), "status": "error", "model": None,
            "score": None, "test_statuses": None}


def main():
    rows = [json.loads(l) for l in open(RAW)]
    completed = [r for r in rows if r.get("status") == "completed"]
    done = set()
    if os.path.exists(OUT):
        for l in open(OUT):
            try: done.add(json.loads(l)["trajectory_id"])
            except Exception: pass
    todo = [r for r in completed if r["trajectory_id"] not in done]
    print(f"completed {len(completed)} | cached {len(done)} | to fetch {len(todo)}", flush=True)
    n = 0
    with open(OUT, "a") as out, ThreadPoolExecutor(WORKERS) as ex:
        futs = {ex.submit(fetch, r): r for r in todo}
        for fut in as_completed(futs):
            out.write(json.dumps(fut.result()) + "\n"); n += 1
            if n % 2000 == 0:
                out.flush(); print(f"  {n}/{len(todo)}", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
