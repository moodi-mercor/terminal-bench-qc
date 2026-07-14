#!/usr/bin/env python3
"""Generic GLM batch recovery: re-run the infra-failed (rate-limited) runs of a source
batch until every original run has a genuine graded attempt.

Works on any campaign/batch (Agentic Code, Zero to One Coder, ...). Waves dispatch the
FULL remaining deficit each round - rate-limit bounces are instant and free.

Usage:
  RLS_RECOVER_KEY=... python recover_glm_batch.py --camp camp_... --batch batch_... --execute

State: _local/recover_<batch8>/state.json (resumable). Genuine = model produced tokens
and no infra error; agent timeouts count as genuine score-0 (Terminal-Bench semantics).
"""
import argparse, json, os, sys, time
from collections import Counter, defaultdict
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import glm_retry_lib as L

ROOT = L.ROOT
MAX_ROUNDS = 30

def hdrs(camp):
    key = os.environ.get("RLS_RECOVER_KEY") or L.key()
    return {"Authorization": f"Bearer {key}", "X-Campaign-Id": camp,
            "X-Company-Id": "comp_2fa4115109d741cd94a3c409ed89e61f",
            "X-Account-Id": "acct_85b680d4c5ba49a29f19c173672aebea",
            "User-Agent": "curl/8.7.1", "Content-Type": "application/json"}

def list_batch(H, bid):
    rows, page = [], 1
    while True:
        for attempt in range(6):
            try:
                r = requests.get(f"{L.API}/trajectories/batch/{bid}", headers=H,
                                 params={"limit": "100", "offset": str((page-1)*100)}, timeout=120)
                if r.status_code == 200:
                    d = r.json(); break
            except Exception:
                pass
            time.sleep(3*(attempt+1))
        else:
            raise RuntimeError(f"page fetch failed {bid} p{page}")
        items = d.get("trajectories", [])
        if not items: break
        rows.extend(items)
        pg = d.get("pagination", {})
        if page >= pg.get("total_pages", page): break
        page += 1
    return rows

# Only these transient errors are worth re-running. Everything else that lacks a
# score is a broken task config (missing verifier key, verifier ValidationError, ...)
# that will fail identically on retry -> set aside, never re-dispatched.
RETRYABLE = ("ratelimit", "rate_limit", "rate limit", "badgateway", "bad gateway",
             "no fallback model group", "overloaded", "serviceunavailable",
             "502", "503", "504", "connection", "apitimeout", "read timed out",
             # transient infra: Modal sandbox died mid-run -> a fresh run gets a new sandbox
             "already shut down", "sandbox with container", "sandbox has already")

def classify(H, tid):
    """Return verdict: 'genuine' (with score), 'retry' (rate-limit family), 'broken'."""
    for _ in range(4):
        try:
            r = requests.get(f"{L.API}/trajectories/{tid}", headers=H, timeout=120)
            if r.status_code == 200:
                to = r.json().get("trajectory_output") or {}
                em = str(to.get("error_message") or "").lower()
                tok = (to.get("usage_metrics") or {}).get("total_tokens", 0) or 0
                score = to.get("score")
                if "agenttimeouterror" in em and tok > 0: return "genuine", 0.0
                if any(m in em for m in RETRYABLE): return "retry", None
                if score is None: return "broken", None
                return "genuine", float(score)
        except Exception:
            pass
        time.sleep(1.5)
    return "retry", None  # couldn't read it -> assume transient, try again

def classify_many(H, ids, workers=10):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return dict(zip(ids, ex.map(lambda t: classify(H, t), ids)))

def wait_drain(H, bid):
    while True:
        try:
            st = Counter(r.get("trajectory_status") for r in list_batch(H, bid))
        except Exception as e:
            print(f"    poll error {e}; retry 60s", flush=True); time.sleep(60); continue
        print(f"    {dict(st)}", flush=True)
        if st.get("pending",0)+st.get("running",0) == 0: return
        time.sleep(90)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--camp", required=True)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--execute", action="store_true")
    a = ap.parse_args()
    H = hdrs(a.camp)
    out = f"{ROOT}/_local/recover_{a.batch[6:14]}"
    os.makedirs(out, exist_ok=True)
    spath = f"{out}/state.json"
    s = json.load(open(spath)) if os.path.isfile(spath) else {"deficit": None, "round": 0, "genuine": {}, "broken": {}}

    if s["deficit"] is None:
        print(f"waiting for source batch {a.batch[:16]}... to drain", flush=True)
        wait_drain(H, a.batch)
        rows = list_batch(H, a.batch)
        r0 = rows[0]
        s["cfg"] = {"orchestrator_id": r0["orchestrator_id"], "orchestrator_version": r0.get("orchestrator_version") or 1,
                    "agent_id": r0["agent_id"], "agent_version": r0.get("agent_version") or 1}
        res = classify_many(H, [r["trajectory_id"] for r in rows])
        by_task = defaultdict(int); genuine = defaultdict(list); broken = defaultdict(int)
        for r in rows:
            v, sc = res[r["trajectory_id"]]
            if v == "genuine": genuine[r["task_id"]].append(sc)
            elif v == "retry": by_task[r["task_id"]] += 1       # rate-limit -> re-run
            else: broken[r["task_id"]] += 1                      # config-broken -> set aside
        s["deficit"] = dict(by_task); s["genuine"] = dict(genuine); s["broken"] = dict(broken)
        json.dump(s, open(spath, "w"))
        print(f"source: {len(rows)} runs | genuine {sum(len(v) for v in genuine.values())} | "
              f"rate-limited to re-run {sum(by_task.values())} | broken (skipped) {sum(broken.values())}", flush=True)

    remaining = sum(s["deficit"].values())
    print(f"deficit (rate-limited only): {remaining} runs across {sum(1 for v in s['deficit'].values() if v)} tasks "
          f"| broken skipped: {sum(s.get('broken',{}).values())}", flush=True)
    if not a.execute:
        print("DRY-RUN - pass --execute to recover"); return

    while remaining > 0 and s["round"] < MAX_ROUNDS:
        s["round"] += 1
        items = [t for t, n in s["deficit"].items() for _ in range(n)]
        traj = [{"task_id": t, **s["cfg"], "system_prompt": ""} for t in items]
        body = {"trajectory_batch_name": f"recovery of {a.batch[:14]} round {s['round']}",
                "orchestrator_ids": [s["cfg"]["orchestrator_id"]], "judge_ids": [],
                "trajectory_request": traj}
        r = requests.post(f"{L.API}/orchestration/trajectories/batch", headers=H,
                          data=json.dumps(body), timeout=300)
        r.raise_for_status()
        bid = r.json().get("trajectory_batch_id")
        print(f"round {s['round']}: dispatched {len(items)} -> {bid}", flush=True)
        wait_drain(H, bid)
        rows = list_batch(H, bid)
        res = classify_many(H, [x["trajectory_id"] for x in rows])
        g = brk = 0
        for x in rows:
            v, sc = res[x["trajectory_id"]]
            if s["deficit"].get(x["task_id"], 0) <= 0:
                continue
            if v == "genuine":
                s["deficit"][x["task_id"]] -= 1
                s["genuine"].setdefault(x["task_id"], []).append(sc); g += 1
            elif v == "broken":
                # surfaced as broken on re-run -> stop retrying this slot
                s["deficit"][x["task_id"]] -= 1
                s["broken"][x["task_id"]] = s.get("broken", {}).get(x["task_id"], 0) + 1; brk += 1
            # v == "retry": leave deficit, try again next wave
        remaining = sum(s["deficit"].values())
        json.dump(s, open(spath, "w"))
        print(f"  genuine {g}/{len(items)} | newly-broken {brk} | remaining {remaining}", flush=True)

    print("DONE" if remaining == 0 else f"stopped at round {s['round']}, {remaining} left", flush=True)

if __name__ == "__main__":
    main()
