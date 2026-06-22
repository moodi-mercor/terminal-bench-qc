#!/usr/bin/env python3
"""Run the deployed TB AutoQC task modules over every unique task in a Studio batch.

A batch (admin/batch/<id>) is a set of eval trajectories (rollouts). This enumerates
the unique tasks referenced by those trajectories and runs the three task-subject
AutoQC modules (Static / Reviewer / Adversary) on each, then polls + reports.

Phases (resumable — state in _local/tb_modules/):
    enumerate   GET /trajectories/batch/<id> (paginated) -> unique {task_id: task_name}
    trigger     POST /qc-audits/ for each (task x module)            [WRITE]
    poll        GET  /qc-audits/ until complete, collect per-dim verdicts
    report      render a Markdown summary (defect distribution)

Usage:
    python run_batch_autoqc.py enumerate
    python run_batch_autoqc.py trigger          # writes audits (needs approval)
    python run_batch_autoqc.py poll
    python run_batch_autoqc.py report
    python run_batch_autoqc.py all               # trigger -> poll -> report
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
BATCH = os.environ.get("BATCH", "batch_c5e617e48b0f41eaa13337976014e396")

MODULES = {
    "Static Structural QC": "qcspec_7e5dbd46cf6de18e0a08d2a6",
    "Task Quality Review": "qcspec_7bddfd703a12994dbc31fd1b",
    "Reward-Hack / Adversary QC": "qcspec_e5cb0f9be6123abea7d720c4",
}
TRAJ_SPEC = "qcspec_ece2ca798fd2580188abd82c"   # Verifier Audit (subject_kind=trajectory)

MDIR = f"{ROOT}/_local/tb_modules"
TASKS_F = f"{MDIR}/_batch_{BATCH}_tasks.json"
SAMPLE_F = f"{MDIR}/_batch_{BATCH}_sample.json"
TRIG_F = f"{MDIR}/_batch_{BATCH}_triggered.json"
RES_F = f"{MDIR}/_batch_{BATCH}_results.json"
REPORT_F = f"{MDIR}/_batch_{BATCH}_report.md"
TRAJ_F = f"{MDIR}/_batch_{BATCH}_trajs.json"
TRIG_TRAJ_F = f"{MDIR}/_batch_{BATCH}_traj_triggered.json"
RES_TRAJ_F = f"{MDIR}/_batch_{BATCH}_traj_results.json"


def key():
    for line in open(f"{ROOT}/.env"):
        if line.startswith("RLS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")


H = {"Authorization": f"Bearer {key()}", "X-Campaign-Id": CAMP, "X-Company-Id": COMP,
     "X-Account-Id": ACCT, "User-Agent": "curl/8.7.1", "Content-Type": "application/json"}


def get(path, **params):
    r = requests.get(f"{API}{path}", headers=H, params=params or None, timeout=120)
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)


def post(path, body):
    r = requests.post(f"{API}{path}", headers=H, data=json.dumps(body), timeout=120)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text


def enumerate_tasks():
    tasks = {}            # task_id -> task_name
    traj_count = 0
    page, page_size = 1, 100
    while True:
        st, data = get(f"/trajectories/batch/{BATCH}", limit=str(page_size),
                       offset=str((page - 1) * page_size))
        if st != 200 or not isinstance(data, dict):
            print(f"  ! page {page}: http={st} {str(data)[:200]}"); break
        rows = data.get("trajectories", [])
        if not rows:
            break
        for r in rows:
            tid = r.get("task_id")
            if tid:
                tasks.setdefault(tid, r.get("task_name") or tid)
        traj_count += len(rows)
        pg = data.get("pagination", {})
        total_pages = pg.get("total_pages", page)
        if page % 20 == 0 or page >= total_pages:
            print(f"  page {page}/{total_pages}  trajectories={traj_count}  unique_tasks={len(tasks)}",
                  flush=True)
        if page >= total_pages:
            break
        page += 1
    os.makedirs(MDIR, exist_ok=True)
    json.dump({"batch": BATCH, "trajectories": traj_count, "tasks": tasks},
              open(TASKS_F, "w"), indent=2)
    print(f"\nenumerate: {traj_count} trajectories -> {len(tasks)} unique tasks")
    print(f"saved -> {TASKS_F}")
    return tasks


def sample(n):
    """Write an evenly-spaced sample of n tasks across the enumerated set."""
    if not os.path.isfile(TASKS_F):
        print("No task list yet — run `enumerate` first."); sys.exit(1)
    tasks = json.load(open(TASKS_F))["tasks"]
    items = sorted(tasks.items())                      # deterministic order
    if n >= len(items):
        chosen = items
    else:
        step = len(items) / n
        chosen = [items[int(i * step)] for i in range(n)]
    sample = dict(chosen)
    json.dump({"batch": BATCH, "tasks": sample}, open(SAMPLE_F, "w"), indent=2)
    print(f"sample: {len(sample)} of {len(items)} tasks (evenly spaced). saved -> {SAMPLE_F}")


def load_tasks():
    if os.path.isfile(SAMPLE_F):
        print(f"(using sample {SAMPLE_F})")
        return json.load(open(SAMPLE_F))["tasks"]
    if not os.path.isfile(TASKS_F):
        print("No task list yet — run `enumerate` first."); sys.exit(1)
    return json.load(open(TASKS_F))["tasks"]


def trigger():
    tasks = load_tasks()
    triggered = json.load(open(TRIG_F)) if os.path.isfile(TRIG_F) else {}
    jobs = [(tid, mname, sid) for tid in tasks for mname, sid in MODULES.items()]
    print(f"trigger: {len(tasks)} tasks x {len(MODULES)} modules = {len(jobs)} audits")
    n = 0
    for tid, mname, sid in jobs:
        kkey = f"{tid}|{sid}"
        if kkey in triggered and triggered[kkey].get("audit"):
            continue
        st, resp = post("/qc-audits/", {"qc_spec_id": sid, "subject_kind": "task",
                                        "subject_id": tid, "source": "automatic",
                                        "function": None, "dimensions_filter": None,
                                        "subject_params": None})
        aid = (resp.get("qc_audit_id") or resp.get("id")) if isinstance(resp, dict) else None
        triggered[kkey] = {"task": tasks[tid], "task_id": tid, "module": mname,
                           "spec": sid, "audit": aid, "http": st}
        n += 1
        if n % 25 == 0:
            json.dump(triggered, open(TRIG_F, "w"), indent=2)
            print(f"  triggered {n} new (total {len(triggered)})", flush=True)
        time.sleep(0.15)
    json.dump(triggered, open(TRIG_F, "w"), indent=2)
    errs = [k for k, v in triggered.items() if (v.get("http") or 0) >= 300]
    print(f"trigger: {len(triggered)} audits recorded ({len(errs)} errored). saved -> {TRIG_F}")


def enumerate_traj(per_task=2):
    """Collect the sampled tasks' rollouts; keep up to per_task completed each,
    preferring a score-0 and a score-1 (the informative FN / FP cases)."""
    sample_ids = set(load_tasks())          # task_ids in the sample
    cand = {}                               # task_id -> [(traj_id, score, model)]
    page, page_size = 1, 100
    while True:
        st, data = get(f"/trajectories/batch/{BATCH}", limit=str(page_size),
                       offset=str((page - 1) * page_size))
        if st != 200 or not isinstance(data, dict):
            print(f"  ! page {page}: http={st}"); break
        rows = data.get("trajectories", [])
        if not rows:
            break
        for r in rows:
            tid = r.get("task_id")
            if tid in sample_ids and r.get("trajectory_status") == "completed":
                cand.setdefault(tid, []).append(
                    (r.get("trajectory_id"), r.get("final_score"),
                     r.get("orchestrator_llm_model")))
        pg = data.get("pagination", {})
        total_pages = pg.get("total_pages", page)
        if page % 40 == 0 or page >= total_pages:
            print(f"  page {page}/{total_pages}  tasks_with_rollouts={len(cand)}", flush=True)
        if page >= total_pages:
            break
        page += 1
    selected = []
    sample_names = load_tasks()
    for tid, rows in cand.items():
        zero = [x for x in rows if (x[1] or 0) == 0][:1]
        one = [x for x in rows if (x[1] or 0) == 1][:1]
        pick = (zero + one) or rows[:1]
        pick = pick[:per_task]
        for traj, score, model in pick:
            selected.append({"task": sample_names[tid], "task_id": tid,
                             "traj": traj, "score": score, "model": model})
    os.makedirs(MDIR, exist_ok=True)
    json.dump({"batch": BATCH, "trajectories": selected}, open(TRAJ_F, "w"), indent=2)
    print(f"enumerate_traj: {len(selected)} trajectories across {len(cand)} tasks. saved -> {TRAJ_F}")


def trigger_traj():
    if not os.path.isfile(TRAJ_F):
        print("Run `enumerate_traj` first."); sys.exit(1)
    sel = json.load(open(TRAJ_F))["trajectories"]
    triggered = json.load(open(TRIG_TRAJ_F)) if os.path.isfile(TRIG_TRAJ_F) else {}
    print(f"trigger_traj: {len(sel)} trajectory audits (Verifier Audit)")
    n = 0
    for j in sel:
        if j["traj"] in triggered and triggered[j["traj"]].get("audit"):
            continue
        st, resp = post("/qc-audits/", {"qc_spec_id": TRAJ_SPEC, "subject_kind": "trajectory",
                                        "subject_id": j["traj"], "source": "automatic",
                                        "function": None, "dimensions_filter": None,
                                        "subject_params": None})
        aid = (resp.get("qc_audit_id") or resp.get("id")) if isinstance(resp, dict) else None
        triggered[j["traj"]] = {**j, "audit": aid, "http": st}
        n += 1
        if n % 25 == 0:
            json.dump(triggered, open(TRIG_TRAJ_F, "w"), indent=2)
            print(f"  triggered {n} new (total {len(triggered)})", flush=True)
        time.sleep(0.15)
    json.dump(triggered, open(TRIG_TRAJ_F, "w"), indent=2)
    errs = [k for k, v in triggered.items() if (v.get("http") or 0) >= 300]
    print(f"trigger_traj: {len(triggered)} recorded ({len(errs)} errored). saved -> {TRIG_TRAJ_F}")


def poll_traj(max_rounds=120, sleep=15):
    if not os.path.isfile(TRIG_TRAJ_F):
        print("Nothing triggered yet."); sys.exit(1)
    triggered = json.load(open(TRIG_TRAJ_F))
    results = json.load(open(RES_TRAJ_F)) if os.path.isfile(RES_TRAJ_F) else {}
    for rnd in range(max_rounds):
        todo = [k for k in triggered if k not in results]
        if not todo:
            break
        newly = 0
        for traj in todo:
            t = triggered[traj]
            st, data = get("/qc-audits/", subject_kind="trajectory",
                           subject_id=traj, qc_spec_id=TRAJ_SPEC)
            rows = data.get("audits", data if isinstance(data, list) else []) if isinstance(data, (dict, list)) else []
            row = rows[0] if rows else None
            status = (row or {}).get("status")
            if status and status not in ("pending", "queued", "running", "in_progress"):
                gp, dims = _verdict(row.get("outcome") or row.get("result") or {})
                results[traj] = {"task": t["task"], "traj": traj, "score": t.get("score"),
                                 "model": t.get("model"), "status": status,
                                 "global_pass": gp, "dims": dims}
                newly += 1
        json.dump(results, open(RES_TRAJ_F, "w"), indent=2)
        print(f"  round {rnd+1}: {len(results)}/{len(triggered)} complete (+{newly})", flush=True)
        if len(results) >= len(triggered):
            break
        time.sleep(sleep)
    print(f"poll_traj: {len(results)}/{len(triggered)} complete. saved -> {RES_TRAJ_F}")


def _verdict(outcome):
    """Collapse an audit outcome into (global_pass, [(dim, status, why)])."""
    dims = []
    gp = None
    if isinstance(outcome, dict):
        gp = outcome.get("global_pass")
        for sec in (outcome.get("sections") or []):
            for d in (sec.get("dimensions") or []):
                name = d.get("dimension") or d.get("name") or d.get("key")
                status = str(d.get("status", "")).lower()
                why = d.get("justification") or d.get("analysis") or ""
                if not why and isinstance(d.get("findings"), str):
                    why = d.get("findings")
                dims.append((name, status, (why or "").strip()[:280]))
    return gp, dims


def poll(max_rounds=120, sleep=15):
    if not os.path.isfile(TRIG_F):
        print("Nothing triggered yet."); sys.exit(1)
    triggered = json.load(open(TRIG_F))
    results = json.load(open(RES_F)) if os.path.isfile(RES_F) else {}
    for rnd in range(max_rounds):
        todo = [k for k in triggered if k not in results]
        if not todo:
            break
        newly = 0
        for kkey in todo:
            t = triggered[kkey]
            st, data = get("/qc-audits/", subject_kind="task",
                           subject_id=t["task_id"], qc_spec_id=t["spec"])
            rows = data.get("audits", data if isinstance(data, list) else []) if isinstance(data, (dict, list)) else []
            row = rows[0] if rows else None
            status = (row or {}).get("status")
            if status and status not in ("pending", "queued", "running", "in_progress"):
                outcome = row.get("outcome") or row.get("result") or {}
                gp, dims = _verdict(outcome)
                results[kkey] = {"task": t["task"], "task_id": t["task_id"], "module": t["module"],
                                 "status": status, "global_pass": gp, "dims": dims}
                newly += 1
        json.dump(results, open(RES_F, "w"), indent=2)
        done, total = len(results), len(triggered)
        print(f"  round {rnd+1}: {done}/{total} complete (+{newly})", flush=True)
        if done >= total:
            break
        time.sleep(sleep)
    print(f"poll: {len(results)}/{len(triggered)} complete. saved -> {RES_F}")


def report():
    if not os.path.isfile(RES_F):
        print("No results yet."); sys.exit(1)
    results = json.load(open(RES_F))
    by_task = {}
    for v in results.values():
        by_task.setdefault(v["task"], {})[v["module"]] = v
    # a task FAILs if any module has a FAIL dim; flag NEUTRAL (adversary candidate)
    fail_tasks, neutral_tasks, clean = [], [], []
    dim_fail_counts = {}
    for task, mods in by_task.items():
        has_fail = has_neutral = False
        for mname, v in mods.items():
            for dim, status, text in v.get("dims", []):
                if status == "fail":
                    has_fail = True
                    dim_fail_counts[f"{mname} / {dim}"] = dim_fail_counts.get(f"{mname} / {dim}", 0) + 1
                elif status == "neutral":
                    has_neutral = True
        if has_fail:
            fail_tasks.append(task)
        elif has_neutral:
            neutral_tasks.append(task)
        else:
            clean.append(task)
    lines = [f"# Batch AutoQC report — {BATCH}", "",
             f"- Tasks audited: **{len(by_task)}**",
             f"- Audits completed: **{len(results)}**",
             f"- **FAIL** (≥1 module FAIL dim): **{len(fail_tasks)}**",
             f"- NEUTRAL only (adversary candidate, no FAIL): **{len(neutral_tasks)}**",
             f"- Clean: **{len(clean)}**", "",
             "## Defect distribution (by module / dimension)", ""]
    for k, n in sorted(dim_fail_counts.items(), key=lambda x: -x[1]):
        lines.append(f"- {k}: {n}")
    lines += ["", "## FAILed tasks", ""]
    for task in sorted(fail_tasks):
        lines.append(f"### {task}")
        for mname, v in by_task[task].items():
            for dim, status, text in v.get("dims", []):
                if status in ("fail", "neutral"):
                    lines.append(f"- **{status.upper()}** {mname} / {dim}: {text}")
        lines.append("")

    # trajectory (Verifier Audit) section
    if os.path.isfile(RES_TRAJ_F):
        tres = json.load(open(RES_TRAJ_F))
        tfail, tneut = [], []
        tdim = {}
        for v in tres.values():
            worst = None
            for dim, status, text in v.get("dims", []):
                if status == "fail":
                    worst = "fail"; tdim[dim] = tdim.get(dim, 0) + 1
                elif status == "neutral" and worst != "fail":
                    worst = "neutral"
            if worst == "fail":
                tfail.append(v)
            elif worst == "neutral":
                tneut.append(v)
        lines += ["", "## Trajectory Verifier Audit", "",
                  f"- Trajectories audited: **{len(tres)}**",
                  f"- **FAIL** (verifier FP/FN or score-status inconsistency): **{len(tfail)}**",
                  f"- NEUTRAL (needs hidden oracle / not stageable): **{len(tneut)}**", ""]
        for dim, n in sorted(tdim.items(), key=lambda x: -x[1]):
            lines.append(f"- {dim}: {n}")
        lines += ["", "### FAILed trajectories", ""]
        for v in sorted(tfail, key=lambda x: x["task"]):
            lines.append(f"- **{v['task']}** (score={v.get('score')}, {v.get('model')})")
            for dim, status, text in v.get("dims", []):
                if status in ("fail", "neutral"):
                    lines.append(f"    - **{status.upper()}** {dim}: {text}")

    open(REPORT_F, "w").write("\n".join(lines))
    print("\n".join(lines[:12]))
    print(f"\nsaved -> {REPORT_F}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "enumerate"
    if cmd == "enumerate":
        enumerate_tasks()
    elif cmd == "sample":
        sample(int(sys.argv[2]) if len(sys.argv) > 2 else 300)
    elif cmd == "trigger":
        trigger()
    elif cmd == "poll":
        poll()
    elif cmd == "report":
        report()
    elif cmd == "enumerate_traj":
        enumerate_traj(int(sys.argv[2]) if len(sys.argv) > 2 else 2)
    elif cmd == "trigger_traj":
        trigger_traj()
    elif cmd == "poll_traj":
        poll_traj()
    elif cmd == "all":
        trigger(); poll(); report()
    else:
        print(__doc__); sys.exit(1)
