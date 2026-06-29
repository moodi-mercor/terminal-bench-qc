#!/usr/bin/env python3
"""Pull the two Reflection-QC difficulty-filter (Sonnet) batches and aggregate
per-task results.

  Old batch  batch_bdd45...  "Reflection QC pass task difficulty filter run"
  New batch  batch_469e...   "...rerun"  (the 1,502 tasks incomplete in old)

Both are claude-sonnet-4-6, Terminus, avg@N (N=3) on world "Canonical Tasks".

Phases:
  pull     paginate /trajectories/batch/<id> -> raw JSONL per batch
  agg      combine (rerun supersedes old per task) -> per-task score table + summary

Usage:
  python pull_difficulty_batches.py pull
  python pull_difficulty_batches.py agg
"""
import json
import os
import sys
import time
import requests

API = "https://api.studio.mercor.com"
CAMP = "camp_4e196b1414a1499db54b43233104b0a7"
COMP = "comp_2fa4115109d741cd94a3c409ed89e61f"
ACCT = "acct_85b680d4c5ba49a29f19c173672aebea"
ROOT = "/Users/mahmoodmapara/Desktop/terminal-bench-qc"

OLD = "batch_bdd4530030c14884b04c031162cb761e"
NEW = "batch_469ef6e7919e4085a040bec1d2d13af8"
OUT = f"{ROOT}/_local/ots_difficulty"


def key():
    for line in open(f"{ROOT}/.env"):
        if line.startswith("RLS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("RLS_KEY not found")


H = {"Authorization": f"Bearer {key()}", "X-Campaign-Id": CAMP, "X-Company-Id": COMP,
     "X-Account-Id": ACCT, "User-Agent": "curl/8.7.1", "Content-Type": "application/json"}

FIELDS = ("trajectory_id", "trajectory_status", "task_id", "task_name",
          "final_score", "grading_statuses", "orchestrator_llm_model",
          "trajectory_batch_id")


def pull_batch(bid):
    os.makedirs(OUT, exist_ok=True)
    fp = f"{OUT}/raw_{bid}.jsonl"
    n = 0
    page, page_size = 1, 100
    with open(fp, "w") as out:
        while True:
            for attempt in range(5):
                r = requests.get(f"{API}/trajectories/batch/{bid}", headers=H,
                                 params={"limit": str(page_size),
                                         "offset": str((page - 1) * page_size)},
                                 timeout=120)
                if r.status_code == 200:
                    break
                time.sleep(2 * (attempt + 1))
            else:
                print(f"  ! page {page}: http={r.status_code} {r.text[:160]}")
                break
            data = r.json()
            rows = data.get("trajectories", [])
            if not rows:
                break
            for row in rows:
                out.write(json.dumps({k: row.get(k) for k in FIELDS}) + "\n")
            n += len(rows)
            pg = data.get("pagination", {})
            total_pages = pg.get("total_pages", page)
            if page % 25 == 0 or page >= total_pages:
                print(f"  {bid[:18]} page {page}/{total_pages} rows={n}", flush=True)
            if page >= total_pages:
                break
            page += 1
    print(f"pull {bid}: {n} trajectories -> {fp}")
    return n


def load(bid):
    fp = f"{OUT}/raw_{bid}.jsonl"
    rows = []
    with open(fp) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def per_task(rows):
    """task_name -> dict(scores=[...], statuses=[...], task_id, n_traj, n_graded)."""
    d = {}
    for r in rows:
        tn = r.get("task_name") or r.get("task_id")
        e = d.setdefault(tn, {"task_id": r.get("task_id"), "scores": [],
                              "statuses": [], "n_traj": 0})
        e["n_traj"] += 1
        gs = r.get("grading_statuses") or []
        e["statuses"].extend(gs)
        sc = r.get("final_score")
        if sc is not None:
            e["scores"].append(sc)
    return d


def agg():
    old = load(OLD)
    new = load(NEW)
    old_t = per_task(old)
    new_t = per_task(new)
    rerun_names = set(new_t)
    # rerun supersedes old for those task_names; union everything else
    combined = {}
    for tn, e in old_t.items():
        if tn not in rerun_names:
            combined[tn] = {**e, "src": "old"}
    for tn, e in new_t.items():
        combined[tn] = {**e, "src": "new"}

    total_tasks = len(combined)
    graded, ungraded = [], []
    for tn, e in combined.items():
        scores = e["scores"]
        e["n_scores"] = len(scores)
        e["avg"] = (sum(scores) / len(scores)) if scores else None
        if scores:
            graded.append(tn)
        else:
            ungraded.append(tn)

    json.dump(combined, open(f"{OUT}/combined_per_task.json", "w"), indent=2)

    # status distribution
    st_counts = {}
    for e in combined.values():
        for s in e["statuses"]:
            st_counts[s] = st_counts.get(s, 0) + 1

    print(f"=== Sonnet difficulty-filter combined (rerun supersedes old) ===")
    print(f"old-batch tasks      : {len(old_t)}  ({len(old)} traj)")
    print(f"new-batch tasks      : {len(new_t)}  ({len(new)} traj)")
    print(f"rerun names          : {len(rerun_names)}")
    print(f"combined unique tasks: {total_tasks}")
    print(f"  with >=1 graded run: {len(graded)}")
    print(f"  fully ungraded     : {len(ungraded)}")
    print(f"grading_status counts: {st_counts}")
    if graded:
        # difficulty bucket: avg <= 0.5 = 'hard/keep'
        hard = [tn for tn in graded if combined[tn]["avg"] is not None and combined[tn]["avg"] <= 0.5]
        print(f"\nSonnet avg@N<=0.5 (hard): {len(hard)} / {len(graded)} graded")
        # avg distribution
        import statistics
        avgs = [combined[tn]["avg"] for tn in graded]
        print(f"avg score mean={statistics.mean(avgs):.3f}  median={statistics.median(avgs):.3f}")
    print(f"\nsaved -> {OUT}/combined_per_task.json")
    if ungraded:
        print(f"sample ungraded: {ungraded[:5]}")


# ---- batch export (bulk; includes trajectory_output.score = reward.txt result) ----
def export_url(bid):
    r = requests.get(f"{API}/export/trajectory-batches/admin/{bid}", headers=H, timeout=120)
    return r.json()


def fetch_export(bid, tag):
    import urllib.request
    info = export_url(bid)
    status = info.get("status")
    print(f"  {tag} ({bid[:18]}): status={status}")
    if status != "ready" or not info.get("url"):
        return None
    fp = f"{OUT}/export_{tag}.json"
    urllib.request.urlretrieve(info["url"], fp)
    print(f"  {tag}: downloaded {os.path.getsize(fp)/1e6:.1f} MB -> {fp}")
    return fp


def export():
    for bid, tag in ((OLD, "old"), (NEW, "new")):
        fetch_export(bid, tag)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "pull"
    if cmd == "pull":
        pull_batch(OLD)
        pull_batch(NEW)
    elif cmd == "agg":
        agg()
    elif cmd == "export":
        export()
    else:
        print(__doc__); sys.exit(1)
