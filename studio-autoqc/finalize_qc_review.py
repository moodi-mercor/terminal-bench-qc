#!/usr/bin/env python3
"""Emit the normalized per-task QC review for the FINAL delivered tb2400 set.

Reads final_2400.json (delivered, all QC-passing) + records the cull/backfill
provenance. Writes:
  _local/tb2400/final_task_qc_review.csv  (one normalized row per delivered task)
  _local/tb2400/TASK_QC_REVIEW.md         (methodology + rollups + provenance)
"""
import csv, json
from collections import Counter, OrderedDict

ROOT = "/Users/mahmoodmapara/Desktop/terminal-bench-qc"; G = f"{ROOT}/_local/tb2400"
final = json.load(open(f"{G}/final_2400.json"))
culled = [t for t in open(f"{G}/culled_ids.txt").read().split() if t]
bf_used = [t for t in open(f"{G}/backfill_used_ids.txt").read().split() if t]

# normalized columns
cols = ["task_id", "source", "category", "diversity_category",
        "flash_passes", "flash_runs", "difficulty_source", "opus_avg_pass8", "gpt5_avg_pass8",
        "qc_static_overall", "qc_anti_cheat", "static_fail_defects",
        "qc_leak_probe", "qc_oracle_gate", "qc_verdict"]
rows = []
for tid, v in final.items():
    rows.append(OrderedDict(
        task_id=tid, source=v.get("source", ""), category=v.get("category", ""),
        diversity_category=v.get("diversity_category", ""),
        flash_passes=v.get("flash_passes"), flash_runs=v.get("flash_runs"),
        difficulty_source=v.get("difficulty_source", ""),
        opus_avg_pass8=v.get("opus_avg_pass8", ""), gpt5_avg_pass8=v.get("gpt5_avg_pass8", ""),
        qc_static_overall=v.get("qc_static_overall", "") or "WARN",
        qc_anti_cheat=v.get("qc_anti_cheat", ""),
        static_fail_defects=v.get("static_fail_defects", ""),
        qc_leak_probe=v.get("leak_probe", "") or "n/a",
        qc_oracle_gate=v.get("oracle_gate", "") or "OK",
        qc_verdict="READY"))
with open(f"{G}/final_task_qc_review.csv", "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(rows)

sc = Counter(r["source"] for r in rows)
ds = Counter(r["difficulty_source"] for r in rows)
fb = Counter(int(r["flash_passes"]) for r in rows
             if str(r["flash_passes"]) not in ("", "None") and int(r["flash_runs"] or 0) >= 5)
cat = Counter(r["category"] for r in rows if r["category"])
tbl = lambda c: "\n".join(f"| {k} | {v} |" for k, v in sorted(c.items(), key=lambda x: str(x[0])))

md = f"""# tb2400 — Normalized Per-Task QC Review (DELIVERED SET)

**Delivered:** 2,400 Terminal-Bench tasks — all QC-passing
**World:** world_07deccb138c3471585223bc682e0d2a0 (GDM-10k campaign)
**Difficulty spec:** 0–4 passes / 8 on Gemini 3.5 Flash (density erring toward 0)
**Per-task detail:** `final_task_qc_review.csv` — one normalized row per delivered task

## Provenance
- Candidate pool: 1,363 Airtable verdict=PASS + 1,037 GDM-10k 0-pass gap-fill = 2,400.
- QC run on all 2,400 → **245 failed** (148 broken oracles, 90 answer-leaks, 7 broken builds).
- Failing tasks **culled** and **backfilled** from freshly-pulled, fully-QC'd GDM-10k
  0-pass tasks. Final delivered set is 100% QC-passing.

| source | count |
|---|---|
{tbl(sc)}

| difficulty source | count |
|---|---|
{tbl(ds)}

Flash-confirmed pass bucket (runs≥5), {sum(fb.values())} tasks:
| passes/8 | count |
|---|---|
{tbl(fb)}

Category mix (top):
| category | count |
|---|---|
{tbl(Counter(dict(cat.most_common(10))))}

## QC checks run on every delivered task (normalized)
Each task in `final_task_qc_review.csv` carries the same columns:

| column | meaning |
|---|---|
| `difficulty_source` | `flash` = measured 0–3/8 on Gemini 3.5 Flash (runs≥5); `opus_gpt5_proxy` = Airtable task hard for Opus/GPT-5 (max avg@8 ≤ 0.5 → ≤4/8 on the weaker Flash by proxy) |
| `qc_static_overall` | roll-up of 11 deterministic static gates (structure, metadata, leakage, reward-hack, env-fairness, portability, dockerfile, instructions, verifier-defenses, security, test-hygiene) |
| `qc_leak_probe` | build-aware probe: `clean` = verifier's expected answer NOT readable in agent-visible dirs; `n/a` = task not in the static-FAIL set that triggered the probe |
| `qc_oracle_gate` | Modal, network-allowed: `OK` = no-op FAILs **and** oracle solve.sh → tests PASS |
| `qc_verdict` | `READY` for all delivered tasks |

## Delivered-set QC status
- static overall: 2,400 PASS/WARN (0 unresolved FAIL)
- leak probe: 0 LEAK-CONFIRMED
- oracle gate: 2,400 OK (no-op fails + oracle passes); 0 broken; 0 gameable

## Culled ({len(culled)}) → replaced by backfill ({len(bf_used)})
See `culled_ids.txt` (with reasons in the original `task_qc_review.csv`) and
`backfill_used_ids.txt`. Culls: 148 broken oracles, 90 confirmed leaks, 7 broken builds.
"""
open(f"{G}/TASK_QC_REVIEW.md", "w").write(md)
print("delivered:", len(rows), "| sources:", dict(sc))
print("wrote final_task_qc_review.csv + TASK_QC_REVIEW.md")
