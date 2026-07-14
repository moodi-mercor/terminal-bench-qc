#!/usr/bin/env python3
"""Add the FULL trajectory record (trajectory.json) to each exported task folder.

Reuses the trajectory_id already resolved in <task>/result.json, fetches the complete
GET /trajectories/<id> response (trajectory_output + all metadata: command_history,
usage_metrics, test_summary_metadata, solution, scores, ...) and writes it as
<task>/trajectory.json. Governed + resumable (skips folders that already have it).
"""
import json, os, sys, threading, time, collections
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
SAPI = "https://api.studio.mercor.com"
OUT = f"{ROOT}/_local/v9_proven_export"
MAXREQ, WINDOW = 8000, 3600.0
_req = collections.deque(); _gl = threading.Lock()


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


def work(folder):
    rp = f"{OUT}/{folder}/result.json"
    tp = f"{OUT}/{folder}/trajectory.json"
    if not os.path.exists(rp) or os.path.exists(tp):
        return "skip"
    tj = json.load(open(rp)).get("trajectory_id")
    if not tj:
        return "no-id"
    govern()
    try:
        full = requests.get(f"{SAPI}/trajectories/{tj}", headers=H, timeout=120).json()
    except Exception as e:
        return f"err"
    json.dump(full, open(tp, "w"), indent=1)
    return "ok"


def main():
    folders = [d for d in os.listdir(OUT) if os.path.isdir(f"{OUT}/{d}")]
    res = collections.Counter()
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(work, d) for d in folders]
        for f in as_completed(futs):
            res[f.result()] += 1
            if sum(res.values()) % 50 == 0:
                print(f"  {sum(res.values())}/{len(folders)} {dict(res)}", flush=True)
    print(f"DONE {dict(res)}", flush=True)


if __name__ == "__main__":
    main()
