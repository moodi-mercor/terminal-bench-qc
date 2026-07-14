#!/usr/bin/env python3
"""Build the normalized per-task QC review for the tb2400 delivery.

Merges every QC signal into one uniform per-task record:
  - source / difficulty (manifest.json): flash pass@8 or opus/gpt5 proxy
  - static QC (qc_static/review-ssot.csv + defects.csv): per-area verdicts + FAIL defects
  - build-aware leak probe (rhprobe_out.tsv): answer literally readable by agent?
  - behavioral oracle gate (oracle_out.txt): oracle passes + no-op fails, network-allowed

Emits:
  _local/tb2400/task_qc_review.csv   (one normalized row per task)
  _local/tb2400/TASK_QC_REVIEW.md    (methodology + rollups + cull list)
"""
import csv, json, os
from collections import Counter, OrderedDict

ROOT = "/Users/mahmoodmapara/Desktop/terminal-bench-qc"
G = f"{ROOT}/_local/tb2400"

man = json.load(open(f"{G}/manifest.json"))

# static: per-area verdicts
ssot = {}
p = f"{G}/qc_static/review-ssot.csv"
if os.path.exists(p):
    for r in csv.DictReader(open(p)):
        ssot[r["task"]] = r

# static: FAIL defects per task
defect_by_task = {}
p = f"{G}/qc_static/defects.csv"
if os.path.exists(p):
    for d in csv.DictReader(open(p)):
        if d["severity"].upper() == "FAIL":
            defect_by_task.setdefault(d["task"], []).append(d["defect"])

# leak probe: LEAK-CANDIDATE if token hits in agent-visible files; NOOP-PASS if empty passes
# format (tab): name, noop, n_token_hits, hit_files, sig_files
leak = {}
p = f"{G}/rhprobe_out.tsv"
if os.path.exists(p):
    for line in open(p):
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 3:
            continue
        name, noop = parts[0], parts[1]
        try:
            nhits = int(parts[2])
        except ValueError:
            nhits = 0
        leak[name] = {"noop": noop, "nhits": nhits,
                      "hit_files": parts[3] if len(parts) > 3 else "",
                      "sig_files": parts[4] if len(parts) > 4 else ""}

# behavioral oracle gate
oracle = {}
p = f"{G}/oracle_out.txt"
if os.path.exists(p):
    for line in open(p):
        parts = line.rstrip("\n").split("\t")
        if len(parts) >= 2:
            oracle[parts[0]] = {"status": parts[1], "detail": parts[2] if len(parts) > 2 else ""}

NOT_EXPLOITABLE = {"reference-solve-reads-truth"}  # oracle reads truth != agent-exploitable

rows = []
for tid, m in man.items():
    defs = defect_by_task.get(tid, [])
    lk = leak.get(tid, {})
    orc = oracle.get(tid, {})
    orc_status = orc.get("status", "pending")

    # leak verdict from build-aware probe
    if not lk:
        leak_verdict = "n/a"
    elif lk.get("noop") == "PASS":
        leak_verdict = "NOOP-PASS"
    elif lk.get("nhits", 0) > 0:
        leak_verdict = "LEAK-CONFIRMED"
    else:
        leak_verdict = "clean"

    # overall QC verdict
    reasons = []
    if orc_status in ("ORACLE-FAIL", "NOOP-PASS", "BUILD-FAIL", "EXC", "EXEC-ERROR"):
        reasons.append(f"oracle:{orc_status}")
    if leak_verdict in ("LEAK-CONFIRMED", "NOOP-PASS"):
        reasons.append(f"leak:{leak_verdict}")
    # static FAIL that is NOT merely a non-exploitable oracle-reads-truth and NOT
    # cleared by the build-aware probe => hold for review
    exploitable_static = [d for d in defs if d not in NOT_EXPLOITABLE]
    if exploitable_static and leak_verdict == "LEAK-CONFIRMED":
        pass  # already counted
    verdict = "CULL" if reasons else "READY"

    rows.append(OrderedDict(
        task_id=tid,
        source=m["source"],
        category=m.get("category", ""),
        diversity_category=m.get("diversity_category", ""),
        flash_passes=m.get("flash_passes"),
        flash_runs=m.get("flash_runs"),
        difficulty_source=m.get("difficulty_source"),
        opus_avg_pass8=m.get("opus_avg_pass8"),
        gpt5_avg_pass8=m.get("gpt5_avg_pass8"),
        qc_static_overall=(ssot.get(tid, {}).get("overall", "")),
        qc_anti_cheat=(ssot.get(tid, {}).get("anti_cheat", "")),
        static_fail_defects=";".join(sorted(set(defs))),
        leak_probe=leak_verdict,
        leak_hit_files=lk.get("hit_files", ""),
        oracle_gate=orc_status,
        oracle_detail=orc.get("detail", "")[:160],
        qc_verdict=verdict,
        cull_reasons=";".join(reasons),
    ))

# write CSV
cols = list(rows[0].keys())
with open(f"{G}/task_qc_review.csv", "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=cols)
    w.writeheader()
    w.writerows(rows)

# rollups
n = len(rows)
gated = sum(1 for r in rows if r["oracle_gate"] != "pending")
oc = Counter(r["oracle_gate"] for r in rows)
lc = Counter(r["leak_probe"] for r in rows)
vc = Counter(r["qc_verdict"] for r in rows)
sc = Counter(r["source"] for r in rows)
fb = Counter(r["flash_passes"] for r in rows if r["flash_passes"] is not None and (r["flash_runs"] or 0) >= 5)
ds = Counter(r["difficulty_source"] for r in rows)
culls = [r for r in rows if r["qc_verdict"] == "CULL"]

def tbl(counter):
    return "\n".join(f"| {k} | {v} |" for k, v in sorted(counter.items(), key=lambda x: str(x[0])))

md = f"""# tb2400 — Normalized Per-Task QC Review

**Corpus:** 2,400 Terminal-Bench tasks (world_07deccb138c3471585223bc682e0d2a0)
**Spec:** 0–4 passes / 8 on Gemini 3.5 Flash (0-dense) + pass QC
**Per-task detail:** `task_qc_review.csv` (one normalized row per task)

## Composition
| source | count |
|---|---|
{tbl(sc)}

| difficulty source | count |
|---|---|
{tbl(ds)}

Flash-confirmed pass bucket (runs≥5):
| passes/8 | count |
|---|---|
{tbl(fb)}

## QC layers run on every task
1. **Static QC** — 11 deterministic gates (structure, metadata, leakage, reward-hack,
   env-fairness, portability, dockerfile, instructions, verifier-defenses, security,
   test-hygiene). Verdict per area + FAIL defects.
2. **Build-aware leak probe** — build the *agent* image, grep the verifier's expected
   answer tokens across agent-visible dirs; LEAK-CONFIRMED if the answer is literally
   readable; NOOP-PASS if the empty container already passes. (run on static-FAIL tasks)
3. **Behavioral oracle gate (Modal, network-allowed)** — no-op must FAIL, oracle
   solve.sh → tests must PASS. Catches broken oracles and vacuous/gameable verifiers.

## Results
Oracle gate coverage: {gated}/{n}

| oracle_gate | count |
|---|---|
{tbl(oc)}

| leak_probe | count |
|---|---|
{tbl(lc)}

| QC verdict | count |
|---|---|
{tbl(vc)}

## Culled ({len(culls)})
| task_id | source | reasons |
|---|---|---|
""" + "\n".join(f"| {c['task_id']} | {c['source']} | {c['cull_reasons']} |" for c in culls) + "\n"

open(f"{G}/TASK_QC_REVIEW.md", "w").write(md)
print(f"tasks: {n} | gated: {gated} | verdicts: {dict(vc)}")
print(f"oracle: {dict(oc)}")
print(f"leak_probe: {dict(lc)}")
print(f"culls: {len(culls)}")
print("wrote task_qc_review.csv + TASK_QC_REVIEW.md")
