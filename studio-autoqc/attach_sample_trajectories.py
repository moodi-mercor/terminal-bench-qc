#!/usr/bin/env python3
"""Attach trajectory + eval evidence to each client-sample folder.

Per task in _local/client_samples_v1:
  trajectories/glm_trial_<i>_<score>.json  — full record of each genuine GLM-5.2 trial
                                             (the 5 trials in eval_summary.json)
  trajectories/opus_pass.json              — one passing Opus-4.8 trajectory (solvability)

GLM trials are recovered by scanning all GLM batches for this pool, classifying each
task's trajectories with glm_retry_lib.classify (same rules as the score state), and
keeping the first 5 genuine ones. Opus pass is found via the querier per task.
"""
import json
import os
import sys
from collections import defaultdict

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import glm_retry_lib as L  # noqa: E402

ROOT = L.ROOT
OUT = f"{ROOT}/_local/client_samples_v1"
GLM_BATCHES = [
    "batch_589c763c23514ecbbd5288a1d67c74e3",  # run 1
    "batch_409b8e955e7e4949a89b415760e43b0a",  # runs 2-5
    "batch_503d9fb2a7d2480bb21c1baf5096a208",  # waves
    "batch_d2d058b65ce64389a379b05534911258",
    "batch_0c5532a00c0a44d786d3ca1b423eeff1",
    "batch_142320bdd98e46c797f8c7d96856715a",
    "batch_6e6f67ff15a24ad3a6ddd6a1c716d7ef",
    "batch_8f79ff733528473f9c7e4c15c6fa0704",
    "batch_b3925608624e41fdb1fea24fe8fbd781",
    "batch_f17d696c013b4993ad5c6cbb1d1ce45b",  # fireworks chunk (45 genuine, mixed params)
]


def full_traj(tid):
    for _ in range(4):
        try:
            r = requests.get(f"{L.API}/trajectories/{tid}", headers=L.H, timeout=120)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
    return None


def main():
    manifest = json.load(open(f"{OUT}/manifest.json"))
    wanted = {s["task_id"]: s["task_name"] for s in manifest["samples"]}

    # 1. collect candidate GLM trajectories per task
    cands = defaultdict(list)
    for bid in GLM_BATCHES:
        try:
            for r in L.list_batch(bid):
                if r["task_id"] in wanted and r["status"] not in ("pending", "running", "cancelled"):
                    cands[r["task_id"]].append(r["id"])
        except Exception as e:
            print(f"  [warn] batch {bid[:16]}: {e}")
    print(f"candidates: {sum(len(v) for v in cands.values())} trajectories over {len(cands)} tasks")

    # 2. per task: classify, keep first 5 genuine, save full records
    for task_id, name in wanted.items():
        tdir = os.path.join(OUT, name, "trajectories")
        os.makedirs(tdir, exist_ok=True)
        res = L.classify_many(cands[task_id], workers=8)
        genuine = [c for c in res if c["genuine"]][:5]
        expected = json.load(open(os.path.join(OUT, name, "eval_summary.json")))["weak_model"]["trials"]
        got = sorted(int(c["score"]) for c in genuine)
        flag = "" if got == sorted(expected) else f"  [note] trials {got} vs summary {sorted(expected)}"
        for i, c in enumerate(genuine, 1):
            d = full_traj(c["id"])
            if d:
                json.dump(d, open(os.path.join(tdir, f"glm_trial_{i}_score{int(c['score'])}.json"), "w"), indent=1)
        print(f"{name}: {len(genuine)} GLM trials saved{flag}", flush=True)

    # 3. one passing Opus trajectory per task (querier per task_id, then detail-check)
    for task_id, name in wanted.items():
        tdir = os.path.join(OUT, name, "trajectories")
        dst = os.path.join(tdir, "opus_pass.json")
        if os.path.exists(dst):
            continue
        sql = (f"SELECT trajectory_id FROM trajectories WHERE task_id='{task_id}' "
               f"AND trajectory_status='completed' ORDER BY created_at DESC LIMIT 60")
        try:
            r = requests.post(f"{L.API}/querier/unstructured", headers=L.H,
                              json={"query": sql}, timeout=300)
            ids = [x["trajectory_id"] for x in r.json().get("rows", [])]
        except Exception as e:
            print(f"{name}: querier error {e}")
            continue
        found = False
        for tid in ids:
            d = full_traj(tid)
            if not d:
                continue
            to = d.get("trajectory_output") or {}
            model = str(to.get("model") or d.get("orchestrator_llm_model") or "").lower()
            if "opus" in model and float(to.get("score") or 0) >= 1.0:
                json.dump(d, open(dst, "w"), indent=1)
                found = True
                break
        print(f"{name}: opus_pass {'saved' if found else 'NOT FOUND'}", flush=True)


if __name__ == "__main__":
    main()
