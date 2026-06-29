#!/usr/bin/env python3
"""Poll/download the avg@8 eval batch export, then compute per-task per-model
avg@8 and emit the Difficulty + Diversity readout.

Difficulty criterion (per spec 3.1): a task is DIFFICULT/keep if
  avg@8 <= 0.5 on GPT-5.4 OR Opus-4.8.

Score = reward.txt result in trajectory_output.score; model in trajectory_output.model.

Usage:
  python report_eval_avg8.py fetch    # poll export until ready + download
  python report_eval_avg8.py report   # extract + compute readout
"""
import ijson
import json
import os
import sys
import time
import statistics
import urllib.request
from collections import Counter
from decimal import Decimal

import requests

API = "https://api.studio.mercor.com"
CAMP = "camp_4e196b1414a1499db54b43233104b0a7"
COMP = "comp_2fa4115109d741cd94a3c409ed89e61f"
ACCT = "acct_85b680d4c5ba49a29f19c173672aebea"
ROOT = "/Users/mahmoodmapara/Desktop/terminal-bench-qc"
OUT = f"{ROOT}/_local/ots_difficulty"
BATCH = "batch_d52db25d7bd8470ca679b99fadc87399"
EXPORT_F = f"{OUT}/export_eval.json"
COMPACT_F = f"{OUT}/compact_eval.jsonl"
THRESH = 0.5
DIMS = [("category", "Category"), ("subcategory", "Subcategory"),
        ("operation_type", "Task Objective"), ("domain", "Domain"),
        ("language", "Language")]


def key():
    for line in open(f"{ROOT}/.env"):
        if line.startswith("RLS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")


H = {"Authorization": f"Bearer {key()}", "X-Campaign-Id": CAMP, "X-Company-Id": COMP,
     "X-Account-Id": ACCT, "User-Agent": "curl/8.7.1"}


def fetch(max_rounds=60, sleep=20):
    for i in range(max_rounds):
        info = requests.get(f"{API}/export/trajectory-batches/admin/{BATCH}",
                            headers=H, timeout=120).json()
        st = info.get("status")
        print(f"  round {i+1}: export status={st}", flush=True)
        if st == "ready" and info.get("url"):
            urllib.request.urlretrieve(info["url"], EXPORT_F)
            print(f"downloaded {os.path.getsize(EXPORT_F)/1e6:.1f} MB -> {EXPORT_F}")
            return EXPORT_F
        time.sleep(sleep)
    print("export not ready after polling.")
    return None


def _f(x):
    return float(x) if isinstance(x, Decimal) else x


PATH = "worlds.item.orchestrators.item.tasks.item"


def extract():
    """task_name -> {task_id, custom_fields, by_model: {model: [scores]}}."""
    tasks = {}
    n = 0
    with open(EXPORT_F, "rb") as f:
        for t in ijson.items(f, PATH):
            tn = t.get("task_name")
            e = tasks.setdefault(tn, {"task_id": t.get("task_id"),
                                      "custom_fields": t.get("custom_fields") or {},
                                      "by_model": {}})
            for tr in (t.get("trajectories") or []):
                to = tr.get("trajectory_output") or {}
                model = to.get("model") or "unknown"
                sc = to.get("score")
                if sc is not None:
                    e["by_model"].setdefault(model, []).append(_f(sc))
            n += 1
            if n % 500 == 0:
                print(f"  {n} task-blocks...", flush=True)
    # collapse model names to opus / gpt
    out = {}
    for tn, e in tasks.items():
        agg = {}
        for model, scores in e["by_model"].items():
            mk = "opus" if "opus" in model.lower() else ("gpt" if "gpt" in model.lower() else model)
            agg.setdefault(mk, []).extend(scores)
        out[tn] = {"task_id": e["task_id"], "custom_fields": e["custom_fields"],
                   "models": {m: {"n": len(s), "avg": sum(s)/len(s)} for m, s in agg.items() if s}}
    with open(COMPACT_F, "w") as fo:
        for tn, e in out.items():
            fo.write(json.dumps({"task_name": tn, **e}) + "\n")
    print(f"extracted {len(out)} tasks -> {COMPACT_F}")
    return out


def dist(records, key):
    c = Counter()
    for r in records:
        v = (r.get("custom_fields") or {}).get(key)
        if isinstance(v, list):
            v = v[0] if v else None
        c[v if v not in (None, "") else "(none)"] += 1
    return c


def fmt(c, total, top=25):
    return "\n".join(f"  {n:5d}  {100*n/total:5.1f}%  {k}"
                     for k, n in c.most_common(top))


def report():
    recs = [json.loads(l) for l in open(COMPACT_F)]
    total = len(recs)
    for r in recs:
        m = r["models"]
        r["opus_avg"] = m.get("opus", {}).get("avg")
        r["gpt_avg"] = m.get("gpt", {}).get("avg")
    have_both = [r for r in recs if r["opus_avg"] is not None and r["gpt_avg"] is not None]

    def hard_on(r, which):
        v = r.get(which)
        return v is not None and v <= THRESH

    hard_gpt = [r for r in recs if hard_on(r, "gpt_avg")]
    hard_opus = [r for r in recs if hard_on(r, "opus_avg")]
    hard_either = [r for r in recs if hard_on(r, "gpt_avg") or hard_on(r, "opus_avg")]
    hard_both = [r for r in recs if hard_on(r, "gpt_avg") and hard_on(r, "opus_avg")]
    easy = [r for r in recs if r not in hard_either]

    L = []; P = L.append
    P("# OTS Inventory — avg@8 Difficulty + Diversity readout")
    P("")
    P(f"Batch {BATCH}")
    P(f"Models: claude-opus-4-8 (effort=high) + gpt-5.4 (effort=high), Terminus, avg@8")
    P(f"Tasks evaluated: **{total}**  (with both-model scores: {len(have_both)})")
    P("")
    P("## Difficulty (avg@8 <= 0.5)")
    P(f"- HARD on **GPT-5.4**            : **{len(hard_gpt)}**  ({100*len(hard_gpt)/total:.1f}%)")
    P(f"- HARD on **Opus-4.8**           : **{len(hard_opus)}**  ({100*len(hard_opus)/total:.1f}%)")
    P(f"- **PASS difficulty (<=0.5 on GPT OR Opus)** : **{len(hard_either)}**  ({100*len(hard_either)/total:.1f}%)")
    P(f"- HARD on BOTH (GPT AND Opus)    : **{len(hard_both)}**  ({100*len(hard_both)/total:.1f}%)")
    P(f"- FAIL difficulty (easy for both): **{len(easy)}**  ({100*len(easy)/total:.1f}%)")
    P("")
    for label, key2 in (("GPT-5.4", "gpt_avg"), ("Opus-4.8", "opus_avg")):
        vals = [r[key2] for r in recs if r[key2] is not None]
        if vals:
            P(f"  {label}: mean avg@8={statistics.mean(vals):.3f}  median={statistics.median(vals):.3f}")
    P("")
    P("## Diversity distributions")
    P("")
    for k, label in DIMS:
        cA = dist(recs, k)
        cH = dist(hard_either, k)
        P(f"### {label}  ({len(cA)} distinct)")
        P("")
        P(f"**Overall ({total} tasks):**")
        P(fmt(cA, total))
        P("")
        P(f"**Difficulty-PASS subset ({len(hard_either)} tasks):**")
        P(fmt(cH, max(len(hard_either), 1)))
        P("")

    # csv
    with open(f"{OUT}/eval_avg8_tasks.csv", "w") as f:
        f.write("task_name,opus_avg8,opus_n,gpt_avg8,gpt_n,pass_difficulty,"
                "category,subcategory,task_objective,domain,language\n")
        for r in sorted(recs, key=lambda x: x["task_name"]):
            cf = r.get("custom_fields") or {}
            def g(kk):
                v = cf.get(kk)
                if isinstance(v, list):
                    v = v[0] if v else ""
                return str(v or "").replace(",", ";")
            oa = "" if r["opus_avg"] is None else f"{r['opus_avg']:.4f}"
            ga = "" if r["gpt_avg"] is None else f"{r['gpt_avg']:.4f}"
            on = r["models"].get("opus", {}).get("n", 0)
            gn = r["models"].get("gpt", {}).get("n", 0)
            pas = "yes" if (r in hard_either) else "no"
            f.write(f"{r['task_name']},{oa},{on},{ga},{gn},{pas},"
                    f"{g('category')},{g('subcategory')},{g('operation_type')},"
                    f"{g('domain')},{g('language')}\n")

    open(f"{OUT}/REPORT_eval_avg8.md", "w").write("\n".join(L))
    print("\n".join(L)[:3000])
    print(f"\nsaved -> {OUT}/REPORT_eval_avg8.md  +  eval_avg8_tasks.csv")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "fetch"
    if cmd == "fetch":
        fetch()
    elif cmd == "extract":
        extract()
    elif cmd == "report":
        if not os.path.isfile(COMPACT_F):
            extract()
        report()
    else:
        print(__doc__)
