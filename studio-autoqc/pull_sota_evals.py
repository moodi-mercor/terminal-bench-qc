#!/usr/bin/env python3
"""Pull raw historical pass rates per task, broken out by model family.

Same full-corpus scan as pull_opus_evals.py, but keeps EVERY model (not just Opus)
and aggregates per (task_id, model_family). Emits raw pass rates so the research team
can curate on measured solvability across SOTA models.

The querier caps unstructured rows at 100 without an explicit LIMIT and times out on
JSON in WHERE/GROUP-BY over the 318k-row table, so we page each batch with a plain
SELECT (JSON only in the SELECT list) and aggregate locally.

Outputs (into _local/sota_pass_rates/):
  pass_rates_long.csv   task_name, task_id, qc_bucket, model_family, runs, passes, pass_rate
  pass_rates_wide.csv   one row/task, <fam>_runs/<fam>_passes/<fam>_pass_rate per SOTA family
  model_coverage.json   per raw-model and per-family: #tasks, total runs, total passes
  summary.json          scan totals

Usage: python pull_sota_evals.py
"""
import csv
import json
import os
import re
import sys
import time
from collections import defaultdict

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "skills", "static-semantic-qc", "scripts")]
import studio_pull as sp  # noqa: E402

API, WORLD = sp.API, sp.WORLD
OUT = os.path.normpath(os.path.join(HERE, "..", "_local", "sota_pass_rates"))
PASS = 1.0
CHUNK = 8000

# exact-model normalisation: strip the provider prefix and known suffixes so
# "openai/gpt-5.5" -> "gpt-5.5", "vertex_ai/gemini-3.1-pro-preview" -> "gemini-3.1-pro-preview".
# no-op/validation is the oracle validation harness, not a model -> dropped.
DROP = {"no-op/validation"}
GLM_RE = re.compile(r"GLM-?([\d.]+)", re.I)


def clean_model(raw):
    if raw in DROP:
        return None
    m = raw.split("/")[-1]                 # drop provider prefix
    if "glm" in raw.lower():
        g = GLM_RE.search(raw)
        return f"glm-{g.group(1)}" if g else "glm"
    if "kimi" in raw.lower():
        return "kimi-k2"
    return m.lower()

# exact SOTA models given columns in the wide table (frontier, strong coverage).
SOTA_MODELS = ["claude-opus-4-8", "claude-opus-4-7", "gpt-5.5", "gpt-5.4",
               "gemini-3.1-pro-preview", "gemini-3.5-flash", "claude-sonnet-4-6"]


def q(key, sql):
    for i in range(6):
        r = requests.post(f"{API}/querier/unstructured", headers=sp.headers(key),
                          json={"query": sql}, timeout=300)
        if r.status_code == 200:
            return r.json()["rows"]
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(3 * (i + 1)); continue
        raise RuntimeError(f"{r.status_code}: {r.text[:200]}")
    raise RuntimeError("query failed after retries")


def main():
    os.makedirs(OUT, exist_ok=True)
    key = sp.load_key()

    tasks = sp.list_tasks(key, WORLD)
    meta = {}  # task_id -> (name, bucket)
    for t in tasks:
        if t.get("archived_at") is not None:
            continue
        b = (t.get("custom_fields") or {}).get("qc_final_bucket") or ""
        meta[t["task_id"]] = (t.get("task_name"), b)
    print(f"tasks in world (non-archived): {len(meta)}", flush=True)

    batches = [r["trajectory_batch_id"] for r in
               q(key, f"SELECT trajectory_batch_id, COUNT(*) AS n FROM trajectories "
                       f"WHERE world_id='{WORLD}' GROUP BY 1 ORDER BY n DESC LIMIT 400")
               if r["trajectory_batch_id"]]
    print(f"batches to scan: {len(batches)}", flush=True)

    # agg[(task_id, family)] = {runs, pass}
    agg = defaultdict(lambda: {"runs": 0, "pass": 0})
    raw_models = defaultdict(lambda: {"runs": 0, "pass": 0})
    total = 0
    for bi, bid in enumerate(batches, 1):
        offset = 0
        while True:
            sql = ("SELECT task_id AS tid, trajectory_output->>'model' AS model, "
                   "trajectory_output->>'score' AS score FROM trajectories "
                   f"WHERE world_id='{WORLD}' AND trajectory_batch_id='{bid}' "
                   f"ORDER BY trajectory_id LIMIT {CHUNK} OFFSET {offset}")
            rows = q(key, sql)
            if not rows:
                break
            total += len(rows)
            for r in rows:
                m = (r.get("model") or "").strip()
                if not m:
                    continue
                sc = r.get("score")
                try:
                    scf = float(sc) if sc is not None else None
                except (TypeError, ValueError):
                    scf = None
                passed = 1 if (scf is not None and scf >= PASS) else 0
                cm = clean_model(m)
                if cm is None:
                    continue
                a = agg[(r["tid"], cm)]
                a["runs"] += 1; a["pass"] += passed
                rm = raw_models[cm]
                rm["runs"] += 1; rm["pass"] += passed
            if len(rows) < CHUNK:
                break
            offset += CHUNK
        print(f"  [{bi}/{len(batches)}] {bid[:16]} | scanned {total} | task-fam cells {len(agg)}", flush=True)

    # ---- long table (every exact model, raw)
    long_rows = []
    by_task = defaultdict(dict)  # tid -> model -> (runs, passes)
    for (tid, model), a in agg.items():
        name, bucket = meta.get(tid, (tid, ""))
        pr = a["pass"] / a["runs"] if a["runs"] else 0.0
        long_rows.append({"task_name": name, "task_id": tid, "qc_bucket": bucket,
                          "model": model, "runs": a["runs"], "passes": a["pass"],
                          "pass_rate": f"{pr:.4f}"})
        by_task[tid][model] = (a["runs"], a["pass"])
    long_rows.sort(key=lambda r: (r["task_name"] or "", r["model"]))
    with open(f"{OUT}/pass_rates_long.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["task_name", "task_id", "qc_bucket",
                                          "model", "runs", "passes", "pass_rate"])
        w.writeheader(); w.writerows(long_rows)

    # ---- wide table (one row per task, SOTA exact models as columns)
    cols = ["task_name", "task_id", "qc_bucket"]
    for mdl in SOTA_MODELS:
        cols += [f"{mdl}_runs", f"{mdl}_passes", f"{mdl}_pass_rate"]
    cols += ["sota_models_run", "sota_mean_pass_rate"]
    with open(f"{OUT}/pass_rates_wide.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(cols)
        for tid, mdls in sorted(by_task.items(), key=lambda kv: (meta.get(kv[0], ("",))[0] or "")):
            name, bucket = meta.get(tid, (tid, ""))
            row = [name, tid, bucket]
            rates = []
            for mdl in SOTA_MODELS:
                runs, passes = mdls.get(mdl, (0, 0))
                if runs:
                    pr = passes / runs; row += [runs, passes, f"{pr:.4f}"]; rates.append(pr)
                else:
                    row += [0, 0, ""]
            row += [len(rates), f"{sum(rates)/len(rates):.4f}" if rates else ""]
            w.writerow(row)

    # ---- coverage
    mdl_cov = defaultdict(lambda: {"tasks": 0, "runs": 0, "passes": 0})
    for (tid, model), a in agg.items():
        c = mdl_cov[model]; c["tasks"] += 1; c["runs"] += a["runs"]; c["passes"] += a["pass"]
    json.dump({"by_model": dict(sorted(mdl_cov.items(), key=lambda kv: -kv[1]["runs"])),
               "sota_columns": SOTA_MODELS},
              open(f"{OUT}/model_coverage.json", "w"), indent=1)
    json.dump({"scanned_trajectories": total, "tasks_with_any_run": len(by_task),
               "batches": len(batches)}, open(f"{OUT}/summary.json", "w"), indent=1)

    print(f"\nscanned {total} trajectories; tasks with >=1 run: {len(by_task)}")
    print("model coverage (tasks with >=1 run of that model):")
    for mdl, c in sorted(mdl_cov.items(), key=lambda kv: -kv[1]["runs"]):
        pr = c["passes"] / c["runs"] if c["runs"] else 0
        print(f"  {mdl:26s} tasks={c['tasks']:6d} runs={c['runs']:7d} pass_rate={pr:.2f}")
    print(f"out: {OUT}/")


if __name__ == "__main__":
    main()
