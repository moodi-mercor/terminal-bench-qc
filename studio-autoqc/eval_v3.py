#!/usr/bin/env python3
"""Combined re-eval after static v3 + reviewer v2:
 - re-audit STATIC (latest ver) on tasks static v1 FAILed (v3 strictness ⊆ v1, so others unchanged)
 - re-audit REVIEW (latest ver) on ALL 141 tasks (reviewer changed both directions)
 - reuse ADVERSARY from v1
Then recompute P/R -> EVAL200_REPORT_v3.md
"""
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
REVIEW = "qcspec_7bddfd703a12994dbc31fd1b"
STATIC = "qcspec_7e5dbd46cf6de18e0a08d2a6"


def key():
    for line in open(f"{ROOT}/.env"):
        if line.startswith("RLS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")


H = {"Authorization": f"Bearer {key()}", "X-Campaign-Id": CAMP, "X-Company-Id": COMP,
     "X-Account-Id": ACCT, "User-Agent": "curl/8.7.1", "Content-Type": "application/json"}


def get(path, **p):
    for a in range(4):
        try:
            r = requests.get(f"{API}{path}", headers=H, params=p or None, timeout=60)
            return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)
        except Exception as e:
            if a == 3:
                return 0, str(e)
            time.sleep(2 * (a + 1))


def post(body):
    for a in range(4):
        try:
            r = requests.post(f"{API}/qc-audits/", headers=H, data=json.dumps(body), timeout=60)
            return r.status_code
        except Exception:
            if a == 3:
                return 0
            time.sleep(2 * (a + 1))


def fail_neutral(oc):
    f = n = 0
    if isinstance(oc, dict):
        for s in oc.get("sections", []):
            for d in s.get("dimensions", []):
                st = (d.get("status") or "").lower()
                f += st == "fail"; n += st == "neutral"
    return f, n


def latest_ver(sid):
    st, spec = get(f"/qc-specs/{sid}")
    return spec.get("version") if isinstance(spec, dict) else None


old = json.load(open(f"{OUTD}/_eval200_results.json"))
tasks, results = old["tasks"], old["results"]
ids = {(t.get("task_name") or t.get("name")): (t.get("task_id") or t.get("id"))
       for t in json.load(open(os.path.join(tempfile.gettempdir(), f"studio_tasks_{WORLD}.json")))}

RV, SV = latest_ver(REVIEW), latest_ver(STATIC)
print(f"latest versions: review v{RV}, static v{SV}", flush=True)


def static_failed_v1(nm):
    r = results.get(nm, {}).get("static", {})
    return r.get("global_pass") is False or r.get("fail", 0) > 0


static_targets = [nm for nm in tasks if static_failed_v1(nm)]
review_targets = list(tasks.keys())  # all
jobs = [(nm, STATIC, SV) for nm in static_targets] + [(nm, REVIEW, RV) for nm in review_targets]
print(f"re-auditing: {len(static_targets)} static + {len(review_targets)} review = {len(jobs)} audits", flush=True)

for i, (nm, sid, _) in enumerate(jobs, 1):
    post({"qc_spec_id": sid, "subject_kind": "task", "subject_id": ids[nm], "source": "automatic",
          "function": None, "dimensions_filter": None, "subject_params": None})
    if i % 40 == 0:
        print(f"  triggered {i}/{len(jobs)}", flush=True)
    time.sleep(0.1)

print("polling...", flush=True)
newres = {}  # (nm,sid) -> verdict
start = time.time()
while time.time() - start < 7200:
    pend = 0
    for nm, sid, ver in jobs:
        if (nm, sid) in newres:
            continue
        st, data = get("/qc-audits/", subject_kind="task", subject_id=ids[nm], qc_spec_id=sid)
        rows = data.get("audits", data if isinstance(data, list) else []) if isinstance(data, (dict, list)) else []
        cand = [a for a in rows if a.get("qc_spec_version") == ver and a.get("status") == "completed"]
        if cand:
            a = sorted(cand, key=lambda x: x.get("created_at", ""))[-1]
            f, n = fail_neutral(a.get("outcome"))
            oc = a.get("outcome")
            newres[(nm, sid)] = {"status": "completed",
                                 "global_pass": oc.get("global_pass") if isinstance(oc, dict) else None,
                                 "fail": f, "neutral": n}
        else:
            pend += 1
    print(f"  {len(newres)}/{len(jobs)} done ({pend} pending)", flush=True)
    if len(newres) == len(jobs):
        break
    time.sleep(20)

# merge
for nm in static_targets:
    if (nm, STATIC) in newres:
        results[nm]["static"] = newres[(nm, STATIC)]
for nm in review_targets:
    if (nm, REVIEW) in newres:
        results[nm]["review"] = newres[(nm, REVIEW)]


def flagged(rec, mk):
    r = rec.get(mk, {})
    return (r.get("global_pass") is False) or (r.get("fail", 0) > 0)


def score(pred):
    tp = fp = fn = tn = 0
    for nm, meta in tasks.items():
        if not all(mk in results.get(nm, {}) for mk in ("static", "review", "adversary")):
            continue
        gt = meta["is_defect"]; p = pred(results[nm])
        tp += gt and p; fp += (not gt) and p; fn += gt and (not p); tn += (not gt) and (not p)
    pr = tp / (tp + fp) if tp + fp else 0.0
    rc = tp / (tp + fn) if tp + fn else 0.0
    return tp, fp, fn, tn, pr, rc


L = ["# AutoQC 200-set Eval — v3 (static surgical + reviewer sharpened)", "",
     f"static v{SV} re-audited on {len(static_targets)} prior-FAIL tasks; review v{RV} re-audited on all {len(review_targets)}; adversary reused from v1.",
     "", "| Detector | TP | FP | FN | TN | Precision | Recall |", "|---|---|---|---|---|---|---|"]
for label, pred in [
    ("static (v3)", lambda r: flagged(r, "static")),
    ("review (v2)", lambda r: flagged(r, "review")),
    ("static+review (deployable)", lambda r: flagged(r, "static") or flagged(r, "review")),
    ("any incl adversary-neutral", lambda r: flagged(r, "static") or flagged(r, "review") or r.get("adversary", {}).get("neutral", 0) > 0),
]:
    tp, fp, fn, tn, pr, rc = score(pred)
    L.append(f"| {label} | {tp} | {fp} | {fn} | {tn} | {pr:.2f} | {rc:.2f} |")
L += ["", "## Defects still MISSED (static+review)", ""]
for nm, meta in tasks.items():
    if meta["is_defect"] and all(mk in results.get(nm, {}) for mk in ("static", "review")):
        if not (flagged(results[nm], "static") or flagged(results[nm], "review")):
            L.append(f"- `{nm}`")
L += ["", "## False positives (clean flagged — static+review)", ""]
for nm, meta in tasks.items():
    if (not meta["is_defect"]) and all(mk in results.get(nm, {}) for mk in ("static", "review")):
        if flagged(results[nm], "static") or flagged(results[nm], "review"):
            L.append(f"- `{nm}` by {[mk for mk in ('static','review') if flagged(results[nm], mk)]}")
open(f"{OUTD}/EVAL200_REPORT_v3.md", "w").write("\n".join(L))
json.dump({"tasks": tasks, "results": results}, open(f"{OUTD}/_eval200_results_v3.json", "w"), default=str)
print("\n".join(L))
print(f"\nwrote {OUTD}/EVAL200_REPORT_v3.md")
