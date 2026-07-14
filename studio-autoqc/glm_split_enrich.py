#!/usr/bin/env python3
"""Enrich the 576 confirmed strong/weak-split tasks (Opus-solvable & GLM-5.2 bo5<3) with
RLS metadata: GLM pass rate, language (from tags), category, production date (created_at).
Writes split_bucket_666.csv + prints the 4 distributions."""
import csv, json, sys, time
from collections import Counter
sys.path.insert(0, "/Users/mahmoodmapara/Desktop/terminal-bench-qc/studio-autoqc")
import glm_retry_lib as L, requests

Ld = "/Users/mahmoodmapara/Desktop/terminal-bench-qc/_local"
A = list(csv.DictReader(open(f"{Ld}/glm52_runs_all.csv")))
OPUS = {r["task_name"]: r for r in csv.DictReader(open(f"{Ld}/opus_qc/good_tasks_opus.csv"))}


def grate(r):
    t = int(r["glm_trials"]); return int(r["glm_passes"]) / t if t else 0


def opus_ok(name):
    o = OPUS.get(name); return bool(o) and int(o["opus_passes"]) >= 1


# ratio-based weak-model filter: GLM solves <3/5 (rate<0.6). GPT/Opus solvability is taken
# as satisfied (the 1,129 are the pre-filtered Opus-solvable set) — no extra Opus filter.
# Partial coverage allowed — we only need the ratio, not a full pass@5.
wh = [r for r in A if grate(r) < 0.6]
ids = [r["task_id"] for r in wh]
passes = {r["task_id"]: (int(r["glm_passes"]), int(r["glm_trials"])) for r in wh}
print(f"strong/weak bucket (GLM rate<0.6): {len(ids)}")

def q(sql):
    for _ in range(4):
        r = requests.post(f"{L.API}/querier/unstructured", headers=L.H, json={"query": sql}, timeout=120)
        if r.status_code == 200:
            return r.json().get("rows", [])
        time.sleep(3)
    return []

# pull metadata in chunks
meta = {}
for i in range(0, len(ids), 50):
    chunk = ids[i:i+50]
    inlist = "','".join(chunk)
    got = q(f"SELECT task_id, task_name, created_at, custom_fields FROM tasks WHERE task_id IN ('{inlist}')")
    for row in got:
        meta[row["task_id"]] = row
print(f"metadata pulled: {len(meta)}/{len(ids)}")
missing = [t for t in ids if t not in meta]
if missing:
    print(f"  still missing {len(missing)} (likely other campaign/world) — filling from local tasks_cache")
    import os, glob
    A_names = {r["task_id"]: r["task_name"] for r in wh}
    for tid in missing:
        nm = A_names[tid]
        for c in glob.glob(f"{Ld}/tasks_cache*/{nm}/task.toml"):
            raw = open(c, errors="replace").read()
            import re
            cat = (re.search(r'category\s*=\s*"([^"]+)"', raw) or [None, ""])[1]
            tagm = re.search(r'tags\s*=\s*\[([^\]]*)\]', raw)
            tags = re.findall(r'"([^"]+)"', tagm.group(1)) if tagm else []
            meta[tid] = {"task_name": nm, "created_at": "",
                         "custom_fields": {"category": cat, "tags": tags}}
            break
    print(f"  after local fill: {len(meta)}/{len(ids)}")

# explicit language tags (ordered priority)
LANGS = ["python","julia","rust","golang","go","typescript","javascript","nodejs","node",
         "java","cpp","c++","csharp","c#","ruby","bash","shell","sql","scala","kotlin","php",
         "swift","perl","haskell","elixir","lua","dart","matlab","fortran","ocaml",
         "clojure","erlang","zig","nim","r","c"]
NORM = {"golang":"go","node":"javascript","nodejs":"javascript","c++":"cpp","c#":"csharp","shell":"bash"}
# framework/tool tags -> language (fallback when no explicit language tag)
FRAMEWORK = {
    "python": ["pytorch","tensorflow","keras","fastapi","flask","django","pandas","numpy",
               "scipy","scikit","sklearn","peft","transformers","huggingface","matplotlib",
               "pytest","asyncio","aiohttp","celery","sqlalchemy","pydantic","conda","pip",
               "pyarrow","safetensors","mlops","polars","dask","xgboost","lightgbm","jupyter",
               "notebook","onnx","langchain","pyspark"],
    "javascript": ["express","fastify","react","vue","webpack","npm","yarn","socket.io"],
    "rust": ["cargo","tokio","actix"], "go": ["gin","goroutine"],
}
# domain tags that are Python in this corpus when no other language is present
PY_DOMAINS = ["machine-learning","model-training","data-science","data-engineering",
              "data-pipeline","data-processing","numerical-methods","bioinformatics"]
def lang_from_tags(tags):
    ts = [str(t).lower() for t in (tags or [])]
    for lng in LANGS:
        if lng in ts:
            return NORM.get(lng, lng)
    for lang, fws in FRAMEWORK.items():
        if any(fw in ts for fw in fws):
            return lang
    if any(d in ts for d in PY_DOMAINS):
        return "python"
    return "unknown"

rows = []
for r in wh:
    tid = r["task_id"]; m = meta.get(tid, {})
    cf = m.get("custom_fields") or {}
    p, tr = passes[tid]
    o = OPUS.get(r["task_name"], {})
    op, oru = int(o.get("opus_passes", 0)), int(o.get("opus_runs", 0))
    rows.append({
        "task_id": tid, "task_name": r["task_name"],
        "glm_passes": p, "glm_trials": tr, "glm_pass_rate": round(p/tr, 2) if tr else 0,
        "opus_passes": op, "opus_runs": oru, "opus_pass_rate": round(op/oru, 2) if oru else 0,
        "trial_scores": r["trial_scores"], "coverage": r["coverage"],
        "language": lang_from_tags(cf.get("tags")),
        "category": cf.get("category") or (cf.get("domain") or ["?"])[0] if cf else "?",
        "production_date": (m.get("created_at") or "")[:10],
    })

out = f"{Ld}/split_bucket_666.csv"
with open(out, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
print(f"-> {out}\n")

# ---- distributions ----
print("=== 1. GLM-5.2 pass rate (passes out of trials) ===")
for k, v in sorted(Counter(f"{r['glm_passes']}/{r['glm_trials']}" for r in rows).items()):
    print(f"  {k}: {v}")
print(f"  mean pass_rate: {sum(r['glm_pass_rate'] for r in rows)/len(rows):.3f}")

print("\n=== 2. Language distribution ===")
for k, v in Counter(r["language"] for r in rows).most_common():
    print(f"  {k:12}: {v} ({v*100//len(rows)}%)")

print("\n=== 3. Task category distribution ===")
for k, v in Counter(r["category"] for r in rows).most_common():
    print(f"  {str(k):32}: {v} ({v*100//len(rows)}%)")

print("\n=== 4. Production date (by month) ===")
for k, v in sorted(Counter(r["production_date"][:7] for r in rows).items()):
    print(f"  {k}: {v}")
print(f"  range: {min(r['production_date'] for r in rows if r['production_date'])} .. {max(r['production_date'] for r in rows if r['production_date'])}")
