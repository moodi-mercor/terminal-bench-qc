#!/usr/bin/env python3
"""Stage 2 for batch_7c4f522a ("cog-v2 difficulty-backfill p@8 Gemini 3.5 Flash").

The batch LIST endpoint's final_score is uniformly 0.0 (not the real grade). The true
pass/fail lives in each trajectory's trajectory_output.score (1.0 = pass). So we fetch
detail for every completed trajectory (list rows cached in rows_raw.jsonl by
pull_batch_gemini_flash.py), read the real score, and bucket each task by pass-count/8.

Delivery buckets (target model = Gemini 3.5 Flash):
  0/8 passing  -> target >=70% of delivered tasks
  1-2/8        -> target <=30% of delivered tasks
  3+/8         -> too easy, excluded

Resumable via scores_raw.jsonl.
Output: _local/batch_gemini_flash/{scores_raw.jsonl, per_task.csv, per_task.json,
        pass0_of8.txt, pass1_2_of8.txt, delivered.txt, summary.json}
"""
import csv
import json
import os
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

API = "https://api.studio.mercor.com"
KEY = "rls-sk-iuA_SCYWiui2xXr_HD7Rab0BfF5VPmDNlK6eAyLzcYw"
CAMP = "camp_0c1f9a9809604271a534edd77c3cbec1"   # GDM-10k
COMP = "comp_2fa4115109d741cd94a3c409ed89e61f"
ACCT = "acct_85b680d4c5ba49a29f19c173672aebea"
ROOT = "/Users/mahmoodmapara/Desktop/terminal-bench-qc"
OUT = f"{ROOT}/_local/batch_gemini_flash"
RAW = f"{OUT}/rows_raw.jsonl"
SCORES = f"{OUT}/scores_raw.jsonl"
WORKERS = 16

H = {"Authorization": f"Bearer {KEY}", "X-Campaign-Id": CAMP, "X-Company-Id": COMP,
     "X-Account-Id": ACCT, "User-Agent": "curl/8.7.1"}


def fetch(row):
    for attempt in range(4):
        try:
            r = requests.get(f"{API}/trajectories/{row['trajectory_id']}", headers=H, timeout=120)
            if r.status_code == 200:
                to = (r.json() or {}).get("trajectory_output") or {}
                return {"trajectory_id": row["trajectory_id"], "task_name": row["task_name"],
                        "task_id": row["task_id"], "score": to.get("score"),
                        "model": to.get("model")}
        except Exception:
            pass
        time.sleep(1.5 * (attempt + 1))
    return {"trajectory_id": row["trajectory_id"], "task_name": row["task_name"],
            "task_id": row["task_id"], "score": None, "model": None}


def main():
    rows = [json.loads(l) for l in open(RAW)]
    completed = [r for r in rows if r["status"] == "completed"]
    print(f"completed trajectories to score: {len(completed)}", flush=True)

    done = {}
    if os.path.isfile(SCORES):
        for l in open(SCORES):
            r = json.loads(l)
            done[r["trajectory_id"]] = r
    todo = [r for r in completed if r["trajectory_id"] not in done]
    print(f"to fetch: {len(todo)} (cached {len(done)})", flush=True)

    n = 0
    with open(SCORES, "a") as out, ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(fetch, r): r for r in todo}
        for fut in as_completed(futs):
            res = fut.result()
            out.write(json.dumps(res) + "\n")
            done[res["trajectory_id"]] = res
            n += 1
            if n % 1000 == 0:
                out.flush()
                print(f"  fetched {n}/{len(todo)}", flush=True)

    per = defaultdict(lambda: {"runs": 0, "passes": 0, "task_id": None})
    nullscore = 0
    for r in done.values():
        if r.get("score") is None:
            nullscore += 1
            continue
        e = per[r["task_name"]]
        e["runs"] += 1
        e["passes"] += int(float(r["score"]) >= 1.0)
        e["task_id"] = e["task_id"] or r.get("task_id")
    print(f"null-score trajectories: {nullscore}", flush=True)

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
        "batch_name": "cog-v2 difficulty-backfill p@8 Gemini 3.5 Flash (2962 tasks)",
        "target_model": "gemini-3.5-flash",
        "tasks_with_scores": len(per),
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
