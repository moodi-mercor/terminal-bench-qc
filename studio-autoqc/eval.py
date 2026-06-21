#!/usr/bin/env python3
"""Run the 3 AutoQC modules across the Studio-auditable subset of the 200-row eval set,
poll to completion, score vs labels (is_defect). Designed to run in the background:
writes progress + partial results to disk, then a final Markdown P/R report.
"""
import csv
import json
import os
import tempfile
import time
import requests

API = "https://api.studio.mercor.com"
CAMP = "camp_4e196b1414a1499db54b43233104b0a7"
COMP = "comp_2fa4115109d741cd94a3c409ed89e61f"
ACCT = "acct_85b680d4c5ba49a29f19c173672aebea"
WORLD = "world_2c7cdb23737845ad83a9acfa1aa8c25b"
ROOT = "/Users/mahmoodmapara/Desktop/terminal-bench-qc"
OUTD = f"{ROOT}/_local/tb_modules"
MODULES = {
    "review": "qcspec_7bddfd703a12994dbc31fd1b",
    "adversary": "qcspec_e5cb0f9be6123abea7d720c4",
    "static": "qcspec_7e5dbd46cf6de18e0a08d2a6",
}
MAX_WALL = 7200  # 2h cap


def key():
    for line in open(f"{ROOT}/.env"):
        if line.startswith("RLS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")


H = {"Authorization": f"Bearer {key()}", "X-Campaign-Id": CAMP, "X-Company-Id": COMP,
     "X-Account-Id": ACCT, "User-Agent": "curl/8.7.1", "Content-Type": "application/json"}


def get(path, **params):
    for attempt in range(4):
        try:
            r = requests.get(f"{API}{path}", headers=H, params=params or None, timeout=60)
            return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)
        except Exception as e:
            if attempt == 3:
                return 0, str(e)
            time.sleep(2 * (attempt + 1))


def post(path, body):
    for attempt in range(4):
        try:
            r = requests.post(f"{API}{path}", headers=H, data=json.dumps(body), timeout=60)
            try:
                return r.status_code, r.json()
            except Exception:
                return r.status_code, r.text
        except Exception as e:
            if attempt == 3:
                return 0, str(e)
            time.sleep(2 * (attempt + 1))


def dims_fail_neutral(outcome):
    f = n = 0
    if isinstance(outcome, dict):
        for s in outcome.get("sections", []):
            for d in s.get("dimensions", []):
                st = (d.get("status") or "").lower()
                f += st == "fail"
                n += st == "neutral"
    return f, n


def main():
    studio = {(t.get("task_name") or t.get("name")): (t.get("task_id") or t.get("id"))
              for t in json.load(open(os.path.join(tempfile.gettempdir(), f"studio_tasks_{WORLD}.json")))}
    rows = list(csv.DictReader(open(f"{ROOT}/eval/expanded_labels.csv")))
    tasks = {}
    for r in rows:
        nm = r["task"]
        if nm in studio and nm not in tasks:
            tasks[nm] = {"task_id": studio[nm],
                         "is_defect": r.get("is_defect") in ("1", "true", "True"),
                         "kind": r.get("kind"), "source": r.get("source"),
                         "expected_title": r.get("expected_title")}
    print(f"auditable tasks: {len(tasks)}  (defects={sum(t['is_defect'] for t in tasks.values())})", flush=True)

    sid2mk = {v: k for k, v in MODULES.items()}

    def latest_by_spec(task_id):
        st, data = get("/qc-audits/", subject_kind="task", subject_id=task_id)
        arows = data.get("audits", data if isinstance(data, list) else []) if isinstance(data, (dict, list)) else []
        latest = {}
        for a in arows:
            sid = a.get("qc_spec_id")
            if sid in MODULES.values() and (sid not in latest or a.get("created_at", "") > latest[sid].get("created_at", "")):
                latest[sid] = a
        return latest

    def record(rec_for_task, a):
        oc = a.get("outcome")
        fl, nu = dims_fail_neutral(oc)
        rec_for_task[sid2mk[a["qc_spec_id"]]] = {
            "status": a.get("status"),
            "global_pass": oc.get("global_pass") if isinstance(oc, dict) else None,
            "fail": fl, "neutral": nu}

    # ---- resumable trigger: skip modules already completed, (re)trigger the rest ----
    results = {}   # nm -> {mk: {...}}
    print("== triggering (resumable) ==", flush=True)
    for i, (nm, meta) in enumerate(tasks.items(), 1):
        latest = latest_by_spec(meta["task_id"])
        for mk, sid in MODULES.items():
            a = latest.get(sid)
            if a and a.get("status") == "completed":
                record(results.setdefault(nm, {}), a)          # already done — reuse
                continue
            st, resp = post("/qc-audits/", {"qc_spec_id": sid, "subject_kind": "task",
                                            "subject_id": meta["task_id"], "source": "automatic",
                                            "function": None, "dimensions_filter": None, "subject_params": None})
            if st >= 300 or st == 0:
                print(f"  ERR {nm}/{mk}: {st} {str(resp)[:120]}", flush=True)
        if i % 25 == 0:
            print(f"  trigger pass {i}/{len(tasks)} tasks", flush=True)
        time.sleep(0.1)
    pre = sum(1 for nm in tasks if len(results.get(nm, {})) == len(MODULES))
    print(f"== trigger pass done; {pre}/{len(tasks)} already complete; polling rest ==", flush=True)

    # ---- poll (one GET per task returns all module audits) ----
    start = time.time()
    cyc = 0
    while time.time() - start < MAX_WALL:
        cyc += 1
        pending = 0
        for nm, meta in tasks.items():
            have = results.get(nm, {})
            if all(mk in have for mk in MODULES):
                continue
            st, data = get("/qc-audits/", subject_kind="task", subject_id=meta["task_id"])
            arows = data.get("audits", data if isinstance(data, list) else []) if isinstance(data, (dict, list)) else []
            latest = {}
            for a in arows:
                sid = a.get("qc_spec_id")
                if sid not in latest or (a.get("created_at", "") > latest[sid].get("created_at", "")):
                    latest[sid] = a
            for mk, sid in MODULES.items():
                a = latest.get(sid)
                if a and a.get("status") not in ("pending", "queued", "running", "in_progress", None):
                    oc = a.get("outcome")
                    fl, nu = dims_fail_neutral(oc)
                    results.setdefault(nm, {})[mk] = {
                        "status": a.get("status"),
                        "global_pass": oc.get("global_pass") if isinstance(oc, dict) else None,
                        "fail": fl, "neutral": nu}
            if not all(mk in results.get(nm, {}) for mk in MODULES):
                pending += 1
        done = sum(1 for nm in tasks if all(mk in results.get(nm, {}) for mk in MODULES))
        json.dump({"done": done, "total": len(tasks), "results": results},
                  open(f"{OUTD}/_eval200_progress.json", "w"), default=str)
        print(f"  cycle {cyc}: {done}/{len(tasks)} tasks complete ({pending} pending)", flush=True)
        if done == len(tasks):
            break
        time.sleep(20)

    # ---- score ----
    def flagged(rec, mk):  # module says defective?
        r = rec.get(mk, {})
        return (r.get("global_pass") is False) or (r.get("fail", 0) > 0)

    lines = ["# AutoQC 200-set Eval — Precision / Recall", "",
             f"Auditable tasks: {len(tasks)} (defects={sum(t['is_defect'] for t in tasks.values())}). "
             f"Completed: {sum(1 for nm in tasks if all(mk in results.get(nm,{}) for mk in MODULES))}.",
             "Flag = a module returns global_pass=False or >=1 fail dim. Adversary neutrals = candidates (not counted as flag).",
             "", "| Detector | TP | FP | FN | TN | Precision | Recall |", "|---|---|---|---|---|---|---|"]

    def score(pred):
        tp = fp = fn = tn = 0
        for nm, meta in tasks.items():
            if nm not in results or not all(mk in results[nm] for mk in MODULES):
                continue
            gt = meta["is_defect"]; p = pred(results[nm])
            tp += gt and p; fp += (not gt) and p; fn += gt and (not p); tn += (not gt) and (not p)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        return tp, fp, fn, tn, prec, rec

    for label, pred in [
        ("static", lambda r: flagged(r, "static")),
        ("review", lambda r: flagged(r, "review")),
        ("static+review (deployable)", lambda r: flagged(r, "static") or flagged(r, "review")),
        ("any incl. adversary-neutral", lambda r: flagged(r, "static") or flagged(r, "review")
            or r.get("adversary", {}).get("neutral", 0) > 0),
    ]:
        tp, fp, fn, tn, pr, rc = score(pred)
        lines.append(f"| {label} | {tp} | {fp} | {fn} | {tn} | {pr:.2f} | {rc:.2f} |")

    # list misses + false alarms for the deployable combo
    lines += ["", "## False negatives (missed defects — static+review)", ""]
    for nm, meta in tasks.items():
        if meta["is_defect"] and nm in results and all(mk in results[nm] for mk in MODULES):
            if not (flagged(results[nm], "static") or flagged(results[nm], "review")):
                lines.append(f"- `{nm}` ({meta.get('expected_title') or meta.get('source')})")
    lines += ["", "## False positives (clean flagged — static+review)", ""]
    for nm, meta in tasks.items():
        if (not meta["is_defect"]) and nm in results and all(mk in results[nm] for mk in MODULES):
            if flagged(results[nm], "static") or flagged(results[nm], "review"):
                which = [mk for mk in ("static", "review") if flagged(results[nm], mk)]
                lines.append(f"- `{nm}` flagged by {which}")

    open(f"{OUTD}/EVAL200_REPORT.md", "w").write("\n".join(lines))
    json.dump({"tasks": tasks, "results": results}, open(f"{OUTD}/_eval200_results.json", "w"), default=str)
    print("\n".join(lines))
    print(f"\nwrote {OUTD}/EVAL200_REPORT.md")


if __name__ == "__main__":
    main()
