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
#   - tasks_cache_tb: python scripts/import_tb_tasks.py   (needs the public TB clone
#     under references/tb-public-src/ — see eval/README.md)
#   - tasks_cache:    python scripts/studio_pull.py --names @eval/ots_tasks.txt ...
#     (needs RLS_KEY; the 50 run50 tasks). If absent, the TB clean baseline alone
#     still runs and reports the false-positive rate.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=/tmp/qc_combined; mkdir -p "$OUT"

ssots=(); labels=()
run_set () {  # <tasks-dir> <labels-csv> <out-subdir>
  local tdir="$1" lcsv="$2" odir="$OUT/$3"
  [ -d "$tdir" ] || { echo "skip $tdir (absent)"; return; }
  python3 scripts/run_static_qc.py "$tdir" --out-dir "$odir" >/dev/null
  ssots+=("$odir/review-ssot.csv"); labels+=("$lcsv")
}

run_set tasks_cache    eval/run50_labels.csv    ots
run_set tasks_cache_tb eval/tb_clean_labels.csv tb

# concatenate the per-set SSOTs and label files
head -1 "${ssots[0]}" > "$OUT/ssot.csv"
for f in "${ssots[@]}"; do tail -n +2 "$f" >> "$OUT/ssot.csv"; done
head -1 "${labels[0]}" > "$OUT/labels.csv"
for f in "${labels[@]}"; do tail -n +2 "$f" >> "$OUT/labels.csv"; done

echo "=== combined precision / recall ==="
python3 scripts/score_qc.py "$OUT/ssot.csv" "$OUT/labels.csv"
