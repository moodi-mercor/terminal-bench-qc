#!/usr/bin/env python3
"""Rebuild glm_ceiling_eval state from the ground truth of ALL orch_174947b1 batches
(drain 'pass@5 round' + both ceiling runs), so every genuine attempt counts and the
dispatched/terminal counters are exact. Excludes the broken '(new)'-orch batches.
"""
import sys, os, json
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import recover_glm_batch as R
import glm_ceiling_eval as C

CAMP = "camp_4e196b1414a1499db54b43233104b0a7"
OUT = f"{R.L.ROOT}/_local/fresh_refl_glm52_pass5"
RUNS = 5

H = R.hdrs(CAMP)
tasks = list(json.load(open(f"{R.L.ROOT}/_local/qc_out_eval_pool/glm_pass5_tasks.json"))["per_task"].keys())
bids = json.load(open(f"{OUT}/good_orch_batches.json"))
s = {"genuine": {t: [] for t in tasks}, "broken": {t: 0 for t in tasks},
     "dispatched": {t: 0 for t in tasks}, "terminal": {t: 0 for t in tasks},
     "counted": [], "round": 0}

term_ids, term_task = [], {}
for bid in bids:
    rows = R.list_batch(H, bid)
    for r in rows:
        t = r.get("task_id")
        if t not in s["dispatched"]:
            continue
        s["dispatched"][t] += 1
        if r.get("trajectory_status") not in ("pending", "running"):
            term_ids.append(r["trajectory_id"]); term_task[r["trajectory_id"]] = t
    print(f"  scanned {bid}: {len(rows)} traj", flush=True)

print(f"classifying {len(term_ids)} terminal trajectories...", flush=True)
res = C.classify_many_run(H, term_ids, workers=24)
g = 0
for tid, (verdict, sc) in res.items():
    t = term_task[tid]
    s["counted"].append(tid); s["terminal"][t] += 1
    if verdict == "genuine" and len(s["genuine"][t]) < RUNS:
        s["genuine"][t].append(sc); g += 1

json.dump(s, open(f"{OUT}/state.json", "w"))
banked = sum(len(v) for v in s["genuine"].values())
done = sum(1 for v in s["genuine"].values() if len(v) >= RUNS)
inflight = sum(max(0, s["dispatched"][t] - s["terminal"][t]) for t in tasks)
print(f"REBUILT: {banked} genuine banked | {done} tasks 5/5 | in-flight {inflight} | deficit ~{RUNS*len(tasks)-banked}")
# seed the live harvest list with all good batches so their draining traj keep counting
open(f"{OUT}/ceiling_batch_ids.txt", "w").write("\n".join(bids) + "\n")
