#!/usr/bin/env python3
"""Run the Verifier Audit (trajectory) module across all rollouts of the OTS eval tasks.

Source map: _local/tb_modules/_eval_traj_map.json  (task_name -> [(traj_id, score, model)])
Triggers one audit per trajectory, saves audit ids immediately (resumable), then polls
and writes results incrementally. Re-running collects any stragglers.
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
SID = "qcspec_ece2ca798fd2580188abd82c"   # Verifier Audit (trajectory)
TAG = ("_" + sys.argv[1]) if len(sys.argv) > 1 else ""
TRIG = f"{ROOT}/_local/tb_modules/_eval_sweep_triggered{TAG}.json"
RES = f"{ROOT}/_local/tb_modules/_eval_sweep_results{TAG}.json"


def key():
    for line in open(f"{ROOT}/.env"):
        if line.startswith("RLS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")


H = {"Authorization": f"Bearer {key()}", "X-Campaign-Id": CAMP, "X-Company-Id": COMP,
     "X-Account-Id": ACCT, "User-Agent": "curl/8.7.1", "Content-Type": "application/json"}


def get(path, **p):
    r = requests.get(f"{API}{path}", headers=H, params=p or None, timeout=120)
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)


def post(path, body):
    r = requests.post(f"{API}{path}", headers=H, data=json.dumps(body), timeout=120)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text


def main():
    tmap = json.load(open(f"{ROOT}/_local/tb_modules/_eval_traj_map.json"))
    jobs = []  # (task, traj_id, score)
    for task, rows in tmap.items():
        for tid, score, model in rows:
            jobs.append({"task": task, "traj": tid, "score": score, "model": model})
    print(f"sweep: {len(jobs)} trajectories across {len(tmap)} tasks", flush=True)

    # trigger (resumable)
    triggered = json.load(open(TRIG)) if os.path.isfile(TRIG) else {}
    for j in jobs:
        if j["traj"] in triggered:
            continue
        st, resp = post("/qc-audits/", {"qc_spec_id": SID, "subject_kind": "trajectory",
                                        "subject_id": j["traj"], "source": "automatic",
                                        "function": None, "dimensions_filter": None, "subject_params": None})
        triggered[j["traj"]] = {"task": j["task"], "score": j["score"], "model": j["model"],
                                "audit": (resp.get("qc_audit_id") or resp.get("id")) if isinstance(resp, dict) else None,
                                "http": st}
        if len(triggered) % 25 == 0:
            json.dump(triggered, open(TRIG, "w"), indent=2)
            print(f"  triggered {len(triggered)}/{len(jobs)}", flush=True)
        time.sleep(0.2)
    json.dump(triggered, open(TRIG, "w"), indent=2)
    print(f"triggered all {len(triggered)}", flush=True)

    # poll + collect incrementally
    results = json.load(open(RES)) if os.path.isfile(RES) else {}
    for rnd in range(90):  # generous
        todo = [t for t in triggered if t not in results]
        if not todo:
            break
        for tid in todo:
            st, data = get("/qc-audits/", subject_kind="trajectory", subject_id=tid, qc_spec_id=SID)
            rows = data.get("audits", data if isinstance(data, list) else []) if isinstance(data, (dict, list)) else []
            row = rows[0] if rows else None
            stt = (row or {}).get("status")
            if stt and stt not in ("pending", "queued", "running", "in_progress"):
                o = row.get("outcome") or {}
                flags = []
                for sec in (o.get("sections") or []):
                    for dd in (sec.get("dimensions") or []):
                        if str(dd.get("status", "")).lower() in ("fail", "neutral"):
                            flags.append({"dim": dd.get("dimension") or dd.get("name"),
                                          "status": dd.get("status"),
                                          "text": (dd.get("analysis") or dd.get("text") or "")[:300]})
                results[tid] = {"task": triggered[tid]["task"], "score": triggered[tid]["score"],
                                "model": triggered[tid]["model"], "status": stt,
                                "counts": o.get("status_counts"), "flags": flags}
        json.dump(results, open(RES, "w"), indent=2)
        print(f"  collected {len(results)}/{len(triggered)}", flush=True)
        if len(results) == len(triggered):
            break
        time.sleep(15)

    # summary
    fn = [r for r in results.values() for f in r["flags"] if "false_negative" in (f["dim"] or "").lower() or "False Negative" in (f["dim"] or "")]
    print("\n== SWEEP SUMMARY ==")
    print(f"  collected {len(results)}/{len(triggered)}")
    nfail = sum(1 for r in results.values() if any(f["status"].lower() == "fail" for f in r["flags"]))
    nneu = sum(1 for r in results.values() if any(f["status"].lower() == "neutral" for f in r["flags"]) and not any(f["status"].lower()=="fail" for f in r["flags"]))
    print(f"  trajectories with a FAIL flag   : {nfail}")
    print(f"  trajectories with only NEUTRAL  : {nneu}")
    print(f"  results -> {RES}")
    for tid, r in results.items():
        for f in r["flags"]:
            if f["status"].lower() == "fail":
                print(f"   FAIL {r['task']} (score={r['score']},{r['model']}) :: {f['dim']}: {f['text'][:160]}")


if __name__ == "__main__":
    main()
