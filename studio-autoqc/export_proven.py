#!/usr/bin/env python3
"""Pull the PASSING trajectory (solution + eval result) for each v9-proven task,
into a git-friendly tree for upload.

Layout:
  _local/v9_proven_export/
    manifest.csv
    <task_name>/solution.sh        # the agent's solution (diff/script that passed)
    <task_name>/result.json        # score, test_statuses, model, trajectory_id, metadata

513 tasks have a known passing trajectory_id (use directly). The 34 avg@8-proven
tasks: locate a score>0 attempt in batch_d52db25 by name, fetch its detail.
Governed under the request cap. Resumable (skips tasks already exported).
"""
import csv, json, os, sys, threading, time, collections
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
SAPI = "https://api.studio.mercor.com"
OUT = f"{ROOT}/_local/v9_proven_export"
MAXREQ, WINDOW = 8000, 3600.0
_req = collections.deque(); _gl = threading.Lock(); _wl = threading.Lock()


def key():
    for l in open(f"{ROOT}/.env"):
        if l.startswith("RLS_KEY="):
            return l.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("no RLS_KEY")


H = {"Authorization": f"Bearer {key()}", "X-Campaign-Id": "camp_4e196b1414a1499db54b43233104b0a7",
     "X-Company-Id": "comp_2fa4115109d741cd94a3c409ed89e61f",
     "X-Account-Id": "acct_85b680d4c5ba49a29f19c173672aebea", "User-Agent": "curl/8.7.1"}


def govern():
    while True:
        with _gl:
            now = time.time()
            while _req and now - _req[0] > WINDOW:
                _req.popleft()
            if len(_req) < MAXREQ:
                _req.append(now); return
            w = WINDOW - (now - _req[0]) + 1
        time.sleep(max(1, w))


def detail(tj):
    govern()
    try:
        return requests.get(f"{SAPI}/trajectories/{tj}", headers=H, timeout=90).json().get("trajectory_output") or {}
    except Exception:
        return {}


# d52db25 trajectory_ids by task_name (for the avg@8-proven set)
def d52_by_name():
    m = collections.defaultdict(list)
    for l in open(f"{ROOT}/_local/traj_audit/attempts_d52db25.jsonl"):
        r = json.loads(l)
        if r.get("task_name"):
            m[r["task_name"]].append(r["trajectory_id"])
    return m


def save(task_id, name, model, score, tj, o):
    d = f"{OUT}/{name}"; os.makedirs(d, exist_ok=True)
    open(f"{d}/solution.sh", "w").write(o.get("solution") or "")
    res = {"task_id": task_id, "task_name": name, "model": model, "score": score,
           "trajectory_id": tj, "tests_total": o.get("tests_total"),
           "tests_passed": o.get("tests_passed"), "tests_failed": o.get("tests_failed"),
           "test_statuses": o.get("test_statuses"), "exit_code": o.get("exit_code"),
           "duration_seconds": o.get("duration_seconds")}
    json.dump(res, open(f"{d}/result.json", "w"), indent=1)


def work(row, d52):
    tid, name, proof = row["task_id"], row["task_name"], row["proof"]
    if os.path.exists(f"{OUT}/{name}/result.json"):
        return (name, "skip")
    if proof == "passing_trajectory":
        tj = row["trajectory_id"]; o = detail(tj)
        if (o.get("score") or 0) > 0:
            save(tid, name, row["model"], o.get("score"), tj, o); return (name, "ok")
        return (name, "stale-score")
    # avg@8: find a passing attempt in d52db25 by name
    for tj in d52.get(name, []):
        o = detail(tj)
        if (o.get("score") or 0) > 0:
            save(tid, name, o.get("model"), o.get("score"), tj, o); return (name, "ok-avg8")
    return (name, "no-pass-found")


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = list(csv.DictReader(open(f"{ROOT}/_local/v9_proven_FINAL.csv")))
    d52 = d52_by_name()
    res = collections.Counter(); manifest = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(work, r, d52): r for r in rows}
        for fut in as_completed(futs):
            name, st = fut.result(); res[st] += 1
            if st.startswith("ok"):
                manifest.append((futs[fut]["task_id"], name))
            if sum(res.values()) % 50 == 0:
                print(f"  {sum(res.values())}/{len(rows)} {dict(res)}", flush=True)
    # manifest
    with open(f"{OUT}/manifest.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["task_id", "task_name", "path"])
        for tid, name in sorted(manifest, key=lambda x: x[1]):
            w.writerow([tid, name, f"{name}/"])
    print(f"DONE {dict(res)} | exported folders: {len([d for d in os.listdir(OUT) if os.path.isdir(f'{OUT}/{d}')])}", flush=True)


if __name__ == "__main__":
    main()
