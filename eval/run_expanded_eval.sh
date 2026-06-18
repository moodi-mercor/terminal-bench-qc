#!/usr/bin/env bash
# Expanded precision/recall run for eval/expanded_labels.csv.
#
# Run from the repo root:
#   bash eval/run_expanded_eval.sh
#
# Optional prep for the full 50-row overlap:
#   python scripts/studio_pull.py --names @eval/expanded_ots_tasks.txt --out tasks_cache_expanded
#   python scripts/import_tb_tasks.py
#
# The script scores whatever task trees are present. Missing task trees are skipped;
# score_qc reports which labels did not overlap.
set -euo pipefail
cd "$(dirname "$0")/.."

OUT=/tmp/qc_expanded
rm -rf "$OUT"
mkdir -p "$OUT"

ssots=()
run_set () {  # <tasks-dir> <out-subdir>
  local tdir="$1" odir="$OUT/$2"
  [ -d "$tdir" ] || { echo "skip $tdir (absent)"; return; }
  python3 scripts/run_static_qc.py "$tdir" --out-dir "$odir" >/dev/null
  ssots+=("$odir/review-ssot.csv")
}

run_set eval/fixtures fixtures
run_set tasks_cache_expanded expanded_ots
run_set tasks_cache ots_cache
run_set tasks_cache_tb public_tb

if [ "${#ssots[@]}" -eq 0 ]; then
  echo "No task trees found to score."
  exit 1
fi

head -1 "${ssots[0]}" > "$OUT/ssot.csv"
for f in "${ssots[@]}"; do
  tail -n +2 "$f" >> "$OUT/ssot.csv"
done

echo "=== expanded precision / recall ==="
python3 scripts/score_qc.py "$OUT/ssot.csv" eval/expanded_labels.csv
