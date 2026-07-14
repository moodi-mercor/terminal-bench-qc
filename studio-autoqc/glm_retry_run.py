#!/usr/bin/env python3
"""Adaptive, throttled GLM-5.2 pass@5 retry loop.

Goal: every task in the pool ends with 5 GENUINE graded attempts (model actually ran),
none polluted by Fireworks rate-limit "failures".

Strategy: maintain per-task genuine-score counts. Each round, dispatch a small chunk of
top-up runs (deficit toward 5), sized to stay under the endpoint rate limit; poll that
chunk to terminal; classify; fold genuine scores in. If a round's genuine-rate is low,
shrink the chunk; if high, grow it. Repeat until every task has 5 (or max rounds).

State persists in _local/glm52_retry/state.json so it is resumable.

Usage:
  python glm_retry_run.py --seed-batch batch_a0ee6052...   # harvest genuine from the big batch first
  python glm_retry_run.py --execute                         # run the retry loop
"""
import argparse
import json
import os
import time
from collections import defaultdict

import requests

import glm_retry_lib as L

OUT = f"{L.ROOT}/_local/glm52_retry"
STATE = f"{OUT}/state.json"
POOL = f"{L.ROOT}/_local/glm52_pool.txt"

ORCH = ("orch_174947b124e44793ad1d6ce004c45696", 1)   # zai/glm-5.2 via Vercel primary + Fireworks fallback (LiteLLM routing) — spreads load; the direct fireworks-glm-5p2 orch (orch_8b6b1289) bypasses fallback and rate-limits
AGENT = ("agent_ef13be96aaf149d39d5bf5fdbc5077f9", 2)  # Terminus (Lighthouse Harbor)
# Mirror the reference batch batch_57b7db0a settings: NO judge/grading runs, and the
# default system prompt (no custom injection). Score comes from the task test suite
# (trajectory_output.score), which runs regardless of judges.
TARGET = 5


def load_state():
    if os.path.isfile(STATE):
        s = json.load(open(STATE))
        s.setdefault("counted", [])
        return s
    ids = [l.strip() for l in open(POOL) if l.strip()]
    return {"scores": {t: [] for t in ids}, "round": 0, "counted": []}


def save_state(s):
    os.makedirs(OUT, exist_ok=True)
    json.dump(s, open(STATE, "w"), indent=0)


def deficits(s):
    return {t: TARGET - len(v) for t, v in s["scores"].items() if len(v) < TARGET}


def poll_terminal(bid, quiet_after=None):
    while True:
        try:
            st = L.batch_status(bid)
        except Exception as e:
            print(f"    poll error ({e}); retrying in 60s", flush=True)
            time.sleep(60)
            continue
        active = st.get("pending", 0) + st.get("running", 0)
        print(f"    {dict(st)}", flush=True)
        if active == 0:
            return
        time.sleep(45)


def dispatch_chunk(items):
    """items: list of task_ids (one trajectory each). Returns batch_id."""
    traj = [{"task_id": t, "orchestrator_id": ORCH[0], "orchestrator_version": ORCH[1],
             "agent_id": AGENT[0], "agent_version": AGENT[1], "system_prompt": ""} for t in items]
    body = {"trajectory_batch_name": "GLM-5.2 pass@5 retry chunk (no grading)",
            "orchestrator_ids": [ORCH[0]], "judge_ids": [], "trajectory_request": traj}
    r = requests.post(f"{L.API}/orchestration/trajectories/batch", headers=L.H,
                      data=json.dumps(body), timeout=300)
    r.raise_for_status()
    d = r.json()
    return d.get("trajectory_batch_id") or d.get("batch_id") or d.get("id")


def harvest(bid, s):
    """Fold genuine scores from an existing batch into state (does not dispatch).

    Idempotent: trajectory ids already counted (state['counted']) are skipped, and only
    TERMINAL trajectories are classified — so this can be re-run on a draining batch.
    """
    seen = set(s["counted"])
    rows = [r for r in L.list_batch(bid)
            if r["status"] not in ("pending", "running") and r["id"] not in seen]
    print(f"harvest {bid}: {len(rows)} new terminal trajectories", flush=True)
    res = L.classify_many([r["id"] for r in rows])
    by_id = {r["id"]: r for r in rows}
    genuine = 0
    for c in res:
        if c["genuine"]:
            tid = by_id[c["id"]]["task_id"]
            key = tid if tid in s["scores"] else (by_id[c["id"]]["task"] or tid)
            if key in s["scores"] and len(s["scores"][key]) < TARGET:
                s["scores"][key].append(c["score"]); genuine += 1
        # infra/error ids are also marked counted: they carry no score and simply
        # leave a deficit that the retry loop tops up
        s["counted"].append(c["id"])
    save_state(s)
    print(f"harvested {genuine} genuine attempts", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-batch")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--chunk", type=int, default=250)
    ap.add_argument("--max-rounds", type=int, default=40)
    a = ap.parse_args()
    s = load_state()

    if a.seed_batch:
        harvest(a.seed_batch, s)

    d = deficits(s)
    remaining = sum(d.values())
    done = sum(1 for v in s["scores"].values() if len(v) >= TARGET)
    print(f"tasks done: {done}/{len(s['scores'])} | trajectories still needed: {remaining}", flush=True)
    if not a.execute or remaining == 0:
        print("(no dispatch — pass --execute to run the retry loop)" if remaining else "ALL DONE")
        return

    chunk = a.chunk
    while remaining > 0 and s["round"] < a.max_rounds:
        s["round"] += 1
        d = deficits(s)
        # build a chunk: spread across tasks, one run per task per round pass
        items = []
        for t, need in d.items():
            items.extend([t] * need)
        items = items[:chunk]
        print(f"round {s['round']}: dispatching {len(items)} runs (chunk={chunk}), "
              f"{remaining} needed across {len(d)} tasks", flush=True)
        bid = dispatch_chunk(items)
        print(f"  batch {bid}", flush=True)
        poll_terminal(bid)
        rows = L.list_batch(bid)
        res = L.classify_many([r["id"] for r in rows])
        by_id = {r["id"]: r for r in rows}
        g = 0
        for c in res:
            if c["genuine"]:
                tid = by_id[c["id"]]["task_id"]
                if tid in s["scores"] and len(s["scores"][tid]) < TARGET:
                    s["scores"][tid].append(c["score"]); g += 1
            s["counted"].append(c["id"])
        save_state(s)
        rate = g / max(1, len(items))
        prev_remaining = remaining
        remaining = sum(deficits(s).values())
        print(f"  genuine this round: {g}/{len(items)} ({rate:.0%}) | remaining now {remaining}", flush=True)
        # Rate-limited runs fail instantly and cost nothing, so under-dispatching just
        # forfeits throughput slots — always fire the full remaining deficit per wave.
        if remaining == prev_remaining:
            print("  no progress this round; backing off 60s", flush=True)
            time.sleep(60)

    print("DONE" if remaining == 0 else f"stopped at round {s['round']}, {remaining} still needed")


if __name__ == "__main__":
    main()
