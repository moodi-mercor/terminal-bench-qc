#!/usr/bin/env python3
"""Direct (export-free) score pull for the avg@8 eval batch.

The bulk export for 13k trajectories is slow to build, but each trajectory's
reward score lives in GET /trajectories/{id} -> trajectory_output.score, with the
model in trajectory_output.model. We page the lean batch list for the ids +
task_name, then fetch scores concurrently.

Usage: python pull_eval_scores_direct.py
Writes: _local/ots_difficulty/compact_eval.jsonl  (per-task per-model avg@8)
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

API = "https://api.studio.mercor.com"
CAMP = "camp_4e196b1414a1499db54b43233104b0a7"
COMP = "comp_2fa4115109d741cd94a3c409ed89e61f"
ACCT = "acct_85b680d4c5ba49a29f19c173672aebea"
ROOT = "/Users/mahmoodmapara/Desktop/terminal-bench-qc"
OUT = f"{ROOT}/_local/ots_difficulty"
BATCH = "batch_d52db25d7bd8470ca679b99fadc87399"
INV = f"{OUT}/inventory_tax.json"
RAW = f"{OUT}/eval_scores_raw.jsonl"
COMPACT = f"{OUT}/compact_eval.jsonl"
WORKERS = 12


def key():
    for line in open(f"{ROOT}/.env"):
        if line.startswith("RLS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")


H = {"Authorization": f"Bearer {key()}", "X-Campaign-Id": CAMP, "X-Company-Id": COMP,
     "X-Account-Id": ACCT, "User-Agent": "curl/8.7.1"}


def list_rows():
    rows = []
    page, size = 1, 100
    while True:
        r = requests.get(f"{API}/trajectories/batch/{BATCH}", headers=H,
                         params={"limit": str(size), "offset": str((page-1)*size)},
                         timeout=120)
        d = r.json()
        items = d.get("trajectories", [])
        if not items:
            break
        for it in items:
            if it.get("trajectory_status") == "completed":
                rows.append({"id": it["trajectory_id"], "task": it.get("task_name"),
                             "model": it.get("orchestrator_llm_model")})
        pg = d.get("pagination", {})
        if page >= pg.get("total_pages", page):
            break
        page += 1
    print(f"lean list: {len(rows)} completed trajectories")
    return rows


def fetch_score(row):
    for attempt in range(4):
        try:
            r = requests.get(f"{API}/trajectories/{row['id']}", headers=H, timeout=120)
            if r.status_code == 200:
                to = (r.json() or {}).get("trajectory_output") or {}
                return {**row, "score": to.get("score"),
                        "model2": to.get("model") or row["model"]}
        except Exception:
            pass
        time.sleep(1.5 * (attempt + 1))
    return {**row, "score": None, "model2": row["model"]}


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = list_rows()
    done = {}
    if os.path.isfile(RAW):
        for l in open(RAW):
            r = json.loads(l)
            done[r["id"]] = r
    todo = [r for r in rows if r["id"] not in done]
    print(f"to fetch: {len(todo)} (cached {len(done)})")
    n = 0
    with open(RAW, "a") as out, ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(fetch_score, r): r for r in todo}
        for fut in as_completed(futs):
            res = fut.result()
            out.write(json.dumps(res) + "\n")
            done[res["id"]] = res
            n += 1
            if n % 500 == 0:
                out.flush()
                print(f"  fetched {n}/{len(todo)}", flush=True)
    print(f"fetched all. total rows: {len(done)}")

    # aggregate per task per model
    agg = {}
    for r in done.values():
        if r.get("score") is None:
            continue
        m = (r.get("model2") or "").lower()
        mk = "opus" if "opus" in m else ("gpt" if "gpt" in m else m or "unknown")
        e = agg.setdefault(r["task"], {})
        e.setdefault(mk, []).append(float(r["score"]))
    inv = json.load(open(INV)) if os.path.isfile(INV) else {}
    with open(COMPACT, "w") as f:
        for task, models in agg.items():
            rec = {"task_name": task,
                   "custom_fields": {},  # taxonomy comes from inventory join downstream
                   "models": {m: {"n": len(s), "avg": sum(s)/len(s)} for m, s in models.items()}}
            f.write(json.dumps(rec) + "\n")
    print(f"aggregated {len(agg)} tasks -> {COMPACT}")


if __name__ == "__main__":
    main()
