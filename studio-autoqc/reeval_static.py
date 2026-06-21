#!/usr/bin/env python3
"""Re-audit ONLY the static module (now v2) on tasks static v1 had FAILed, then recompute P/R.
Loosening can only flip FAIL->PASS/NEUTRAL, so unchanged tasks keep their v1 verdict."""
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


def post(path, body):
    for a in range(4):
        try:
            r = requests.post(f"{API}{path}", headers=H, data=json.dumps(body), timeout=60)
            try:
                return r.status_code, r.json()
            except Exception:
                return r.status_code, r.text
        except Exception as e:
            if a == 3:
                return 0, str(e)
            time.sleep(2 * (a + 1))


def fail_neutral(oc):
    f = n = 0
    if isinstance(oc, dict):
        for s in oc.get("sections", []):
            for d in s.get("dimensions", []):
                st = (d.get("status") or "").lower()
                f += st == "fail"; n += st == "neutral"
    return f, n


old = json.load(open(f"{OUTD}/_eval200_results.json"))
tasks, results = old["tasks"], old["results"]
ids = {(t.get("task_name") or t.get("name")): (t.get("task_id") or t.get("id"))
       for t in json.load(open(os.path.join(tempfile.gettempdir(), f"studio_tasks_{WORLD}.json")))}


def static_failed(nm):
    r = results.get(nm, {}).get("static", {})
    return r.get("global_pass") is False or r.get("fail", 0) > 0


targets = [nm for nm in tasks if static_failed(nm)]
print(f"re-auditing static v2 on {len(targets)} tasks (static v1 FAILs)", flush=True)

for i, nm in enumerate(targets, 1):
    post("/qc-audits/", {"qc_spec_id": STATIC, "subject_kind": "task", "subject_id": ids[nm],
                         "source": "automatic", "function": None, "dimensions_filter": None, "subject_params": None})
    if i % 20 == 0:
        print(f"  triggered {i}/{len(targets)}", flush=True)
    time.sleep(0.1)

print("polling v2 static results...", flush=True)
new = {}
start = time.time()
while time.time() - start < 5400:
    pend = 0
    for nm in targets:
        if nm in new:
            continue
        st, data = get("/qc-audits/", subject_kind="task", subject_id=ids[nm], qc_spec_id=STATIC)
        rows = data.get("audits", data if isinstance(data, list) else []) if isinstance(data, (dict, list)) else []
        v2 = [a for a in rows if a.get("qc_spec_version") == 2 and a.get("status") == "completed"]
        if v2:
            a = sorted(v2, key=lambda x: x.get("created_at", ""))[-1]
            f, n = fail_neutral(a.get("outcome"))
            oc = a.get("outcome")
            new[nm] = {"status": "completed", "global_pass": oc.get("global_pass") if isinstance(oc, dict) else None,
                       "fail": f, "neutral": n}
        else:
            pend += 1
    print(f"  {len(new)}/{len(targets)} v2 done ({pend} pending)", flush=True)
    if len(new) == len(targets):
        break
    time.sleep(20)

# merge: replace static verdict for re-run tasks
for nm, v in new.items():
    results[nm]["static"] = v


def flagged(rec, mk):
    r = rec.get(mk, {})
    return (r.get("global_pass") is False) or (r.get("fail", 0) > 0)


def score(pred):
    tp = fp = fn = tn = 0
    for nm, meta in tasks.items():
        if nm not in results or not all(mk in results[nm] for mk in ("static", "review", "adversary")):
            continue
        gt = meta["is_defect"]; p = pred(results[nm])
        tp += gt and p; fp += (not gt) and p; fn += gt and (not p); tn += (not gt) and (not p)
    pr = tp / (tp + fp) if tp + fp else 0.0
    rc = tp / (tp + fn) if tp + fn else 0.0
    return tp, fp, fn, tn, pr, rc


L = ["# AutoQC 200-set Eval — v2 (static WARN->NEUTRAL)", "",
     f"Static re-audited (v2) on {len(new)}/{len(targets)} prior-FAIL tasks; review/adversary unchanged from v1.",
     "", "| Detector | TP | FP | FN | TN | Precision | Recall |", "|---|---|---|---|---|---|---|"]
for label, pred in [
    ("static (v2)", lambda r: flagged(r, "static")),
    ("review", lambda r: flagged(r, "review")),
    ("static+review (deployable)", lambda r: flagged(r, "static") or flagged(r, "review")),
    ("any incl adversary-neutral", lambda r: flagged(r, "static") or flagged(r, "review") or r.get("adversary", {}).get("neutral", 0) > 0),
]:
    tp, fp, fn, tn, pr, rc = score(pred)
    L.append(f"| {label} | {tp} | {fp} | {fn} | {tn} | {pr:.2f} | {rc:.2f} |")
L += ["", "## Static FPs remaining (clean still flagged by static v2)", ""]
for nm, meta in tasks.items():
    if (not meta["is_defect"]) and nm in results and flagged(results[nm], "static"):
        L.append(f"- `{nm}` (fail={results[nm]['static'].get('fail')})")
open(f"{OUTD}/EVAL200_REPORT_v2.md", "w").write("\n".join(L))
json.dump({"tasks": tasks, "results": results}, open(f"{OUTD}/_eval200_results_v2.json", "w"), default=str)
print("\n".join(L))
print(f"\nwrote {OUTD}/EVAL200_REPORT_v2.md")
