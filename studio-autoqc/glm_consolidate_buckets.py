#!/usr/bin/env python3
"""Consolidate EVERY GLM-5.2 result we have — the harness runs (Modal/Fireworks/Vercel),
the custom-driver states, the RLS Studio ceiling run, the earlier retry run ("other world"),
and the client_samples trajectories — into one CSV, bucketed by GLM solve rate.

CRITICAL: only VALID trials count. The Fireworks full run was ~89% rate-limit-degraded
(0-token fake-fails); counting those as "unsolved" would inflate the hard buckets. So a
harness trial counts only if it used real tokens AND had no auth/rate-limit error. State-file
scores are already genuine-filtered (model produced tokens), so they count as-is.

Buckets by combined solve_rate over valid trials:
  unsolved (0) | hard (0-0.3) | medium (0.3-0.6) | solvable (>=0.6)
weak_hard flag = solve_rate < 0.6  (== GLM bo5 < 3, the strong/weak-split criterion).
"""
import csv, glob, json, os, re
from collections import defaultdict

L = "/Users/mahmoodmapara/Desktop/terminal-bench-qc/_local"
HARN = f"{L}/master-code-harnesses/logs"
MIN_TOK = 2000  # a valid rollout uses >>this; degraded/auth-fail trials use ~0

# name<->rls-id map (pool). reverse: id->name
id2name = {}
try:
    for name, rid in json.load(open(f"{L}/qc_out_eval_pool/rls_taskids.json")).items():
        id2name[rid] = name
except Exception:
    pass

# per task_key -> list of (score, source)   (valid trials only)
trials = defaultdict(list)
src_seen = defaultdict(set)


def add(key, score, source):
    trials[key].append(float(score))
    src_seen[key].add(source)


def norm(k):
    """map RLS task_<hex> ids to kebab names when known; else keep."""
    return id2name.get(k, k)

# ---- 1. harness runs (epoch_*.json): valid = real tokens + no auth/ratelimit error ----
for run_dir in sorted(glob.glob(f"{HARN}/*/")):
    tag = os.path.basename(run_dir.rstrip("/"))
    n_valid = n_skip = 0
    for f in glob.glob(f"{run_dir}/**/epoch_*.json", recursive=True):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        t = d.get("task_id") or os.path.basename(os.path.dirname(f))
        ts = d.get("test_summary") or {}
        score = ts.get("score")
        mu = d.get("model_usage") or {}
        tok = sum((u.get("output_tokens", 0) or 0) for u in mu.values() if isinstance(u, dict))
        err = str(d.get("error_message") or "")
        bad = ("Authentication" in err) or ("RateLimit" in err) or ("Unauthorized" in err)
        if score is None or tok < MIN_TOK or bad:
            n_skip += 1
            continue
        add(norm(t), score, f"harness:{tag}")
        n_valid += 1
    if n_valid or n_skip:
        print(f"  harness {tag}: valid={n_valid} skipped(degraded/err)={n_skip}")

# ---- 2. custom-driver + RLS ceiling + retry state files (already genuine-filtered) ----
STATE_SOURCES = [
    # local own-Modal driver runs (NOT on RLS) — keep
    (f"{L}/fresh_refl_glm52_pass5/harbor_overnight/state.json", "genuine", "driver:overnight"),
    (f"{L}/fresh_refl_glm52_pass5/harbor_minval/state.json", "genuine", "driver:minval"),
    # NOTE: the RLS ceiling + glm52_retry local state files are SUPERSEDED by the fresh
    # RLS batch pull below (glm_rls_pulled.json) — including both would double-count.
]
for path, key, source in STATE_SOURCES:
    if not os.path.isfile(path):
        continue
    d = json.load(open(path))
    m = d.get(key, {})
    n = 0
    for t, scores in m.items():
        for s in scores:
            add(norm(t), s, source); n += 1
    print(f"  {source}: +{n} trials over {len(m)} tasks")

# ---- 2b. RLS Studio batches (all GLM-5.2 batches, validity-filtered server-side) ----
rls_pull = f"{L}/glm_rls_pulled.json"
if os.path.isfile(rls_pull):
    d = json.load(open(rls_pull))
    n = 0
    for tid, scores in d.items():
        for s in scores:
            add(norm(tid), s, "rls:studio_batches"); n += 1
    print(f"  rls:studio_batches: +{n} valid trials over {len(d)} tasks")

# ---- 3. client_samples_v1 (score in filename glm_trial_N_scoreX.json) ----
n = 0; tk = set()
for f in glob.glob(f"{L}/client_samples_v1/*/trajectories/glm_trial_*_score*.json"):
    m = re.search(r"score(\d+)", os.path.basename(f))
    if not m:
        continue
    task = os.path.basename(os.path.dirname(os.path.dirname(f)))
    add(norm(task), int(m.group(1)), "client_samples_v1"); n += 1; tk.add(task)
if n:
    print(f"  client_samples_v1: +{n} trials over {len(tk)} tasks")

# ---- aggregate + bucket ----
def bucket(rate):
    if rate == 0: return "unsolved"
    if rate < 0.3: return "hard"
    if rate < 0.6: return "medium"
    return "solvable"

pool_names = set(id2name.values())  # kebab names known to live in the RLS pool world
rows = []
for key, scores in trials.items():
    n = len(scores)
    solves = sum(1 for s in scores if s >= 1.0)
    rate = solves / n if n else 0.0
    # lives on RLS if it's a task_<hex> RLS id, or a kebab name in the RLS pool world
    on_rls = str(key).startswith("task_") or (key in pool_names)
    rows.append({
        "task": key,
        "glm_trials": n,
        "glm_solves": solves,
        "solve_rate": round(rate, 3),
        "bucket": bucket(rate),
        "on_rls": "✓" if on_rls else "",
    })

rows.sort(key=lambda r: (r["solve_rate"], -r["glm_trials"]))
out = f"{L}/glm_all_tasks_bucketed.csv"
with open(out, "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
    w.writeheader(); w.writerows(rows)

from collections import Counter
bc = Counter(r["bucket"] for r in rows)
print(f"\n=== {len(rows)} unique GLM-run tasks -> {out} ===")
for b in ["unsolved", "hard", "medium", "solvable"]:
    print(f"  {b:9}: {bc.get(b,0)}")
wh = sum(1 for r in rows if r["solve_rate"] < 0.6)
onr = sum(1 for r in rows if r["on_rls"])
print(f"  weak-hard (bo5<3 / solve_rate<0.6): {wh}/{len(rows)}")
print(f"  on RLS (tick): {onr}/{len(rows)}")
print(f"  total valid GLM trials counted: {sum(r['glm_trials'] for r in rows)}")
