#!/usr/bin/env bash
# Combined precision/recall run over the expanded eval set.
#
# Three labeled sources (see eval/README.md):
#   1. eval/fixtures + eval/golden_labels.csv  — synthetic, offline, deterministic
#   2. tasks_cache    + eval/run50_labels.csv  — 50 real OTS tasks, ground-truthed
#                                                 from eval/run50_gt/ (defective set)
#   3. tasks_cache_tb + eval/tb_clean_labels.csv — 226 real public TerminalBench
#                                                 tasks, normalized TB1->TB2, clean
#                                                 baseline (precision / FP set)
#
# Precision needs both defective AND clean tasks, so this scores the OTS + TB sets
# together. Run from the repo root.  Prereqs:
#   - tasks_cache_tb: python skills/static-semantic-qc/scripts/import_tb_tasks.py
#     (needs the public TB clone under _local/references/tb-public-src/ — see eval/README.md)
#   - tasks_cache:    python skills/static-semantic-qc/scripts/studio_pull.py \
#                       --names @eval/ots_tasks.txt --out _local/tasks_cache
#     (needs RLS_KEY; the 50 run50 tasks). If absent, the TB clean baseline alone
#     still runs and reports the false-positive rate.
# Task trees are kept local-only under _local/ (gitignored).
set -euo pipefail
cd "$(dirname "$0")/.."
L1=skills/static-semantic-qc/scripts   # Layer 1 entry points
SH=shared                              # cross-layer contract (aggregate, score, gate)
OUT=/tmp/qc_combined; mkdir -p "$OUT"

ssots=(); labels=()
run_set () {  # <tasks-dir> <labels-csv> <out-subdir>
  local tdir="$1" lcsv="$2" odir="$OUT/$3"
  [ -d "$tdir" ] || { echo "skip $tdir (absent)"; return; }
  python3 "$L1/run_static_qc.py" "$tdir" --out-dir "$odir" >/dev/null
  ssots+=("$odir/review-ssot.csv"); labels+=("$lcsv")
}

run_set _local/tasks_cache    eval/run50_labels.csv    ots
run_set _local/tasks_cache_tb eval/tb_clean_labels.csv tb

# Merge the per-set SSOTs deduping by task, keeping the WORST verdict — never let a
# clean row from one set overwrite a defective row for the same task (sticky FAIL,
# the same rule shared/aggregate.py + shared/gate.py enforce across layers).
python3 - "$OUT/ssot.csv" "${ssots[@]}" <<'PY'
import csv, os, sys
out, paths = sys.argv[1], sys.argv[2:]
rank = {"PASS": 0, "WARN": 1, "FAIL": 2}
rows, hdr = {}, None
for p in paths:
    if not os.path.exists(p):
        continue
    rs = list(csv.DictReader(open(p)))
    if rs:
        hdr = list(rs[0].keys())
    for r in rs:
        t = r["task"]
        if t not in rows or rank.get(r["overall"], 0) > rank.get(rows[t]["overall"], 0):
            rows[t] = r
with open(out, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=hdr)
    w.writeheader()
    w.writerows(rows.values())
print(f"combined SSOT: {len(rows)} tasks")
PY

# labels: the sets are disjoint task-wise, so a plain concat is safe
head -1 "${labels[0]}" > "$OUT/labels.csv"
for f in "${labels[@]}"; do tail -n +2 "$f" >> "$OUT/labels.csv"; done

echo "=== combined precision / recall ==="
python3 "$SH/score_qc.py" "$OUT/ssot.csv" "$OUT/labels.csv"
