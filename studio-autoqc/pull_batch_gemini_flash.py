#!/usr/bin/env python3
"""Bucket every task in batch_7c4f522a ("cog-v2 difficulty-backfill p@8 Gemini 3.5 Flash",
2962 tasks x 8) by how many of its 8 Gemini-3.5-Flash trajectories pass.

The whole batch is run on gemini-3.5-flash (the target model), and the batch LIST
endpoint already carries task_name + final_score + orchestrator_llm_model per row, so
we page the list and count passes directly (final_score >= 1.0) -- no grading step, no
per-trajectory detail fetches.

Delivery buckets:
  0/8 passing  -> target >=70% of delivered tasks
  1-2/8        -> target <=30% of delivered tasks
  3+/8         -> too easy for Flash, excluded from delivery

Output: _local/batch_gemini_flash/{rows_raw.jsonl, per_task.csv, per_task.json,
        pass0_of8.txt, pass1_2_of8.txt, delivered.txt, summary.json}
"""
import csv
import json
import os
import time
from collections import defaultdict

import requests

API = "https://api.studio.mercor.com"
KEY = "rls-sk-iuA_SCYWiui2xXr_HD7Rab0BfF5VPmDNlK6eAyLzcYw"
CAMP = "camp_0c1f9a9809604271a534edd77c3cbec1"   # GDM-10k
COMP = "comp_2fa4115109d741cd94a3c409ed89e61f"
ACCT = "acct_85b680d4c5ba49a29f19c173672aebea"
BATCH = "batch_7c4f522a9b3f4c83857b04c39abd8ce1"
ROOT = "/Users/mahmoodmapara/Desktop/terminal-bench-qc"
OUT = f"{ROOT}/_local/batch_gemini_flash"
LIMIT = 500

H = {"Authorization": f"Bearer {KEY}", "X-Campaign-Id": CAMP, "X-Company-Id": COMP,
     "X-Account-Id": ACCT, "User-Agent": "curl/8.7.1"}


def get(offset):
    for attempt in range(5):
        try:
            r = requests.get(f"{API}/trajectories/batch/{BATCH}", headers=H,
                             params={"limit": str(LIMIT), "offset": str(offset)}, timeout=120)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"list failed at offset {offset}")


def main():
    os.makedirs(OUT, exist_ok=True)
    first = get(0)
    total = first["pagination"]["total_count"]
    print(f"total trajectories: {total}", flush=True)

    rows = []
    offset = 0
    while offset < total:
        d = first if offset == 0 else get(offset)
        items = d.get("trajectories", [])
        if not items:
            break
        rows.extend(items)
        offset += LIMIT
        if (offset // LIMIT) % 5 == 0:
            print(f"  pulled {len(rows)}/{total}", flush=True)

    with open(f"{OUT}/rows_raw.jsonl", "w") as f:
        for it in rows:
            f.write(json.dumps({
                "trajectory_id": it["trajectory_id"], "task_name": it.get("task_name"),
                "task_id": it.get("task_id"), "status": it.get("trajectory_status"),
                "final_score": it.get("final_score"),
                "model": it.get("orchestrator_llm_model")}) + "\n")

    models = defaultdict(int)
    per = defaultdict(lambda: {"runs": 0, "passes": 0, "task_id": None})
    for it in rows:
        models[it.get("orchestrator_llm_model")] += 1
        if it.get("trajectory_status") != "completed":
            continue
        sc = it.get("final_score")
        if sc is None:
            continue
        e = per[it.get("task_name")]
        e["runs"] += 1
        e["passes"] += int(float(sc) >= 1.0)
        e["task_id"] = e["task_id"] or it.get("task_id")
    print("models in batch:", dict(models), flush=True)

    pass0 = sorted(t for t, e in per.items() if e["passes"] == 0)
    pass12 = sorted(t for t, e in per.items() if 1 <= e["passes"] <= 2)
    pass3 = sorted(t for t, e in per.items() if e["passes"] >= 3)
    delivered = sorted(pass0 + pass12)

    json.dump({t: e for t, e in sorted(per.items())},
              open(f"{OUT}/per_task.json", "w"), indent=1)
    with open(f"{OUT}/per_task.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["task_name", "task_id", "runs", "passes", "bucket"])
        for t, e in sorted(per.items()):
            b = "0/8" if e["passes"] == 0 else ("1-2/8" if e["passes"] <= 2 else "3+/8")
            w.writerow([t, e["task_id"], e["runs"], e["passes"], b])
    open(f"{OUT}/pass0_of8.txt", "w").write("\n".join(pass0) + "\n")
    open(f"{OUT}/pass1_2_of8.txt", "w").write("\n".join(pass12) + "\n")
    open(f"{OUT}/delivered.txt", "w").write("\n".join(delivered) + "\n")

    nd = len(delivered)
    summary = {
        "batch_name": first["trajectories"][0].get("trajectory_batch_name"),
        "flash_tasks_total": len(per),
        "pass0_of8": len(pass0),
        "pass1_2_of8": len(pass12),
        "pass3plus_of8": len(pass3),
        "delivered_total": nd,
        "pct_pass0_of_delivered": round(100 * len(pass0) / nd, 1) if nd else None,
        "pct_pass1_2_of_delivered": round(100 * len(pass12) / nd, 1) if nd else None,
        "target": "pass0 >=70% and pass1_2 <=30% of delivered",
    }
    json.dump(summary, open(f"{OUT}/summary.json", "w"), indent=1)
    print(json.dumps(summary, indent=1))
    print(f"out: {OUT}/")


if __name__ == "__main__":
    main()
