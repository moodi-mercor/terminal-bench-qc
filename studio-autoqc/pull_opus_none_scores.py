#!/usr/bin/env python3
"""Pull scores for the opus_none pass@8 batch (batch_f01e4418) and classify the 945
previously-unclassified healthy-hard tasks into opus_pass / opus_fail (bo8 evidence).

Same direct per-trajectory pull pattern as pull_eval_scores_direct.py (scores live in
trajectory_output.score, binary 0/1). Resumable via the RAW jsonl cache.

Output: _local/opus_none_pass8/{scores_raw.jsonl, classification.json,
        opus_pass_new.txt, opus_fail_new.txt}
"""
import json
import os
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

API = "https://api.studio.mercor.com"
CAMP = "camp_4e196b1414a1499db54b43233104b0a7"
COMP = "comp_2fa4115109d741cd94a3c409ed89e61f"
ACCT = "acct_85b680d4c5ba49a29f19c173672aebea"
ROOT = "/Users/mahmoodmapara/Desktop/terminal-bench-qc"
BATCH = "batch_f01e44181f0f4243bc5c593d26a2f33f"
OUT = f"{ROOT}/_local/opus_none_pass8"
RAW = f"{OUT}/scores_raw.jsonl"
WORKERS = 12


def key():
    for line in open(f"{ROOT}/.env"):
        if line.startswith("RLS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")


H = {"Authorization": f"Bearer {key()}", "X-Campaign-Id": CAMP, "X-Company-Id": COMP,
     "X-Account-Id": ACCT, "User-Agent": "curl/8.7.1"}


def list_rows():
    rows, page = [], 1
    while True:
        r = requests.get(f"{API}/trajectories/batch/{BATCH}", headers=H,
                         params={"limit": "100", "offset": str((page - 1) * 100)},
                         timeout=120)
        d = r.json()
        items = d.get("trajectories", [])
        if not items:
            break
        for it in items:
            if it.get("trajectory_status") == "completed":
                rows.append({"id": it["trajectory_id"], "task": it.get("task_name"),
                             "task_id": it.get("task_id")})
        pg = d.get("pagination", {})
        if page >= pg.get("total_pages", page):
            break
        page += 1
    print(f"lean list: {len(rows)} completed trajectories", flush=True)
    return rows


def fetch_score(row):
    for attempt in range(4):
        try:
            r = requests.get(f"{API}/trajectories/{row['id']}", headers=H, timeout=120)
            if r.status_code == 200:
                to = (r.json() or {}).get("trajectory_output") or {}
                return {**row, "score": to.get("score")}
        except Exception:
            pass
        time.sleep(1.5 * (attempt + 1))
    return {**row, "score": None}


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = list_rows()
    done = {}
    if os.path.isfile(RAW):
        for l in open(RAW):
            r = json.loads(l)
            done[r["id"]] = r
    todo = [r for r in rows if r["id"] not in done]
    print(f"to fetch: {len(todo)} (cached {len(done)})", flush=True)
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

    per_task = defaultdict(lambda: {"runs": 0, "passes": 0, "task_id": None})
    for r in done.values():
        if r.get("score") is None:
            continue
        e = per_task[r["task"]]
        e["runs"] += 1
        e["passes"] += int(float(r["score"]) >= 1.0)
        e["task_id"] = e["task_id"] or r.get("task_id")

    newly_pass = sorted(t for t, e in per_task.items() if e["passes"] >= 1)
    newly_fail = sorted(t for t, e in per_task.items() if e["runs"] > 0 and e["passes"] == 0)
    json.dump({t: e for t, e in sorted(per_task.items())},
              open(f"{OUT}/classification.json", "w"), indent=1)
    open(f"{OUT}/opus_pass_new.txt", "w").write("\n".join(newly_pass) + "\n")
    open(f"{OUT}/opus_fail_new.txt", "w").write("\n".join(newly_fail) + "\n")
    print(f"tasks: {len(per_task)} | newly opus_pass: {len(newly_pass)} | "
          f"newly opus_fail: {len(newly_fail)}")


if __name__ == "__main__":
    main()
