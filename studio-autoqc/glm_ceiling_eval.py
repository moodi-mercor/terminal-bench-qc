#!/usr/bin/env python3
"""Ceiling-throttled GLM-5.2 pass@N with genuine-attempt top-up.

Faster than the drain loop (dispatch_glm_fresh.py): instead of dispatching WAVE tasks
then fully draining (which idles ~half the time waiting on the slowest timeout), this
holds a steady CEILING of trajectories in flight across many overlapping batches and
tops up as they finish. Rate-limit bounces are retried because we only credit GENUINE
graded attempts (recover_glm_batch.classify: model produced tokens, no infra error).

Per-task in-flight is tracked (dispatched - terminal) so we never over-dispatch while a
task's earlier attempts are still running.

Route: orch_174947b1 (GLM-5.2, Vercel primary + Fireworks fallback), Terminus-2, no judges.
State: _local/fresh_refl_glm52_pass5/state.json (resumable). Seeds from any batch ids in
ceiling_batch_ids.txt (pre-seed with earlier batches so their work isn't wasted).
"""
import argparse, json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import glm_retry_lib as L
import recover_glm_batch as R
import requests

ORCH = ("orch_174947b124e44793ad1d6ce004c45696", 1)  # default; override with --orch/--orch-ver
AGENT = ("agent_ef13be96aaf149d39d5bf5fdbc5077f9", 2)
SP = "\nYou are an agent that completes tasks independently.\nUse the tools provided to you to complete the task to the best of your ability.\n"
OUT = f"{L.ROOT}/_local/fresh_refl_glm52_pass5"
STATE = f"{OUT}/state.json"
BIDS = f"{OUT}/ceiling_batch_ids.txt"


def classify_run(H, tid):
    """Genuine iff the model produced a graded attempt. For this QC'd task set every
    task is known-runnable (GPT-5.4 ran them), so a missing score is ALWAYS infra
    (rate-limit / killed / empty-error casualty) -> retry, never permanent 'broken'.
    The per-task attempt cap (need_map) is the backstop against a truly stuck task."""
    for _ in range(4):
        try:
            r = requests.get(f"{L.API}/trajectories/{tid}", headers=H, timeout=120)
            if r.status_code == 200:
                to = r.json().get("trajectory_output") or {}
                em = str(to.get("error_message") or "").lower()
                tok = (to.get("usage_metrics") or {}).get("total_tokens", 0) or 0
                score = to.get("score")
                if "agenttimeouterror" in em and tok > 0:
                    return "genuine", 0.0
                if score is None:
                    return "retry", None
                return "genuine", float(score)
        except Exception:
            pass
        time.sleep(1.5)
    return "retry", None


def classify_many_run(H, ids, workers=24):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return dict(zip(ids, ex.map(lambda t: classify_run(H, t), ids)))


def q(H, sql):
    for _ in range(4):
        r = requests.post(f"{L.API}/querier/unstructured", headers=H, json={"query": sql}, timeout=180)
        if r.status_code == 200:
            return r.json().get("rows", [])
        time.sleep(5)
    return []


def inflight(H, bids):
    if not bids:
        return 0
    inlist = "','".join(bids)
    rows = q(H, f"SELECT COUNT(*) n FROM trajectories WHERE trajectory_batch_id IN ('{inlist}') AND trajectory_status IN ('pending','running')")
    return rows[0]["n"] if rows else 0


def load_state(tasks):
    s = json.load(open(STATE)) if os.path.isfile(STATE) else {}
    s.setdefault("genuine", {}); s.setdefault("broken", {})
    s.setdefault("dispatched", {}); s.setdefault("terminal", {}); s.setdefault("counted", [])
    for t in tasks:
        s["genuine"].setdefault(t, []); s["broken"].setdefault(t, 0)
        s["dispatched"].setdefault(t, 0); s["terminal"].setdefault(t, 0)
    return s


def save(s):
    json.dump(s, open(STATE, "w"))


def read_bids():
    return [b for b in open(BIDS).read().split() if b] if os.path.isfile(BIDS) else []


def need_map(s, tasks, runs, cap):
    """Runs still to dispatch per task = target - genuine - in-flight. Give up on a task
    once it has been dispatched runs*cap times without reaching `runs` genuine (stuck)."""
    d = {}
    for t in tasks:
        if len(s["genuine"][t]) >= runs:
            continue
        if s["dispatched"][t] >= runs * cap:  # attempt cap -> stop retrying
            continue
        inflight_t = s["dispatched"][t] - s["terminal"][t]
        need = runs - len(s["genuine"][t]) - inflight_t
        if need > 0:
            d[t] = need
    return d


def harvest(H, s, runs):
    """Classify newly-terminal trajectories across all batches; fold into state."""
    seen = set(s["counted"])
    rows = []
    for bid in read_bids():
        try:
            rows += [r for r in R.list_batch(H, bid)
                     if r.get("trajectory_status") not in ("pending", "running")
                     and r.get("trajectory_id") not in seen]
        except Exception as e:
            print(f"  harvest list err {bid}: {e}", flush=True)
    if not rows:
        return 0, 0, 0
    res = classify_many_run(H, [r["trajectory_id"] for r in rows], workers=24)
    by = {r["trajectory_id"]: r for r in rows}
    g = ret = 0
    for tid, (verdict, sc) in res.items():
        t = by[tid].get("task_id")
        s["counted"].append(tid)
        if t not in s["genuine"]:
            continue
        s["terminal"][t] += 1
        if len(s["genuine"][t]) >= runs:
            continue
        if verdict == "genuine":
            s["genuine"][t].append(sc); g += 1
        else:
            ret += 1  # retry: terminal-but-infra; deficit reopens -> re-dispatched
    save(s)
    return g, 0, ret


def dispatch(H, items, tag, rnd, orch):
    traj = [{"task_id": t, "orchestrator_id": orch[0], "orchestrator_version": orch[1],
             "agent_id": AGENT[0], "agent_version": AGENT[1], "system_prompt": SP} for t in items]
    body = {"trajectory_batch_name": f"{tag} GLM-5.2 ceiling r{rnd}",
            "orchestrator_ids": [orch[0]], "judge_ids": [], "trajectory_request": traj}
    r = requests.post(f"{L.API}/orchestration/trajectories/batch", headers=H, data=json.dumps(body), timeout=300)
    r.raise_for_status()
    return r.json().get("trajectory_batch_id") or r.json().get("batch_id")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--camp", required=True)
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--ceiling", type=int, default=3000)
    ap.add_argument("--chunk", type=int, default=1000)
    ap.add_argument("--cap", type=int, default=6, help="give up a task after runs*cap dispatches")
    ap.add_argument("--tag", default="refl_glm52_pass5")
    ap.add_argument("--orch", default=ORCH[0])
    ap.add_argument("--orch-ver", type=int, default=ORCH[1])
    ap.add_argument("--execute", action="store_true")
    a = ap.parse_args()
    orch = (a.orch, a.orch_ver)
    H = R.hdrs(a.camp)
    tasks = list(json.load(open(a.tasks))["per_task"].keys())
    os.makedirs(OUT, exist_ok=True)
    s = load_state(tasks)
    print(f"[ceiling] {len(tasks)} tasks x{a.runs} | orch={orch[0]} v{orch[1]} | ceiling={a.ceiling} chunk={a.chunk} cap={a.runs*a.cap}/task", flush=True)
    # seed: fold any already-terminal work from pre-seeded batches
    g, brk, ret = harvest(H, s, a.runs)
    print(f"  seed harvest: +{g} genuine, {ret} retry-terminal", flush=True)
    d = need_map(s, tasks, a.runs, a.cap)
    remaining = sum(d.values())
    print(f"  still to dispatch: {remaining} runs across {len(d)} tasks", flush=True)
    if not a.execute:
        print("DRY-RUN"); return
    rnd = int(s.get("round", 0))
    idle = 0
    while True:
        d = need_map(s, tasks, a.runs, a.cap)
        remaining = sum(d.values())
        settled_tasks = sum(1 for t in tasks
                            if len(s["genuine"][t]) >= a.runs or s["dispatched"][t] >= a.runs * a.cap)
        if settled_tasks >= len(tasks):
            print("ALL TASKS SETTLED", flush=True); break
        fl = inflight(H, read_bids())
        room = a.ceiling - fl
        if remaining > 0 and room >= 200:
            items = [t for t, n in d.items() for _ in range(n)]
            chunk = items[:min(room, a.chunk)]
            rnd += 1; s["round"] = rnd
            bid = dispatch(H, chunk, a.tag, rnd, orch)
            for t in chunk:
                s["dispatched"][t] += 1
            with open(BIDS, "a") as f:
                f.write(bid + "\n")
            save(s)
            print(f"  r{rnd}: +{len(chunk)} traj -> {bid} | in-flight was {fl} | deficit {remaining} | settled {settled_tasks}/{len(tasks)}", flush=True)
            idle = 0
            time.sleep(20)
        else:
            time.sleep(45)
        g, brk, ret = harvest(H, s, a.runs)
        if g or brk:
            print(f"    harvested +{g} genuine, {brk} broken ({ret} retry) | in-flight {fl}", flush=True)
        if remaining == 0 and fl == 0:
            idle += 1
            if idle >= 3:
                print("DONE (deficit 0, nothing in flight)", flush=True); break
    done = sum(1 for t in tasks if len(s["genuine"][t]) >= a.runs)
    print(f"FINISHED | tasks with {a.runs} genuine: {done}/{len(tasks)} | broken-capped: {sum(1 for t in tasks if s['broken'][t] and len(s['genuine'][t])<a.runs)}", flush=True)


if __name__ == "__main__":
    main()
