#!/usr/bin/env bash
# Expanded precision/recall run for eval/expanded_labels.csv.
#
# Run from the repo root:
#   bash eval/run_expanded_eval.sh
#
# Optional prep for the full 100-row overlap:
#   python scripts/studio_pull.py --names @eval/expanded_ots_tasks.txt --out tasks_cache_expanded
#   python scripts/import_tb_tasks.py
#
# This scores the COMBINED static + semantic layer:
#   - static gates run over every present task tree;
#   - the confirmed Layer-2 semantic/manual findings in eval/expanded_sem_findings/
#     (the original run50 verifier defects plus newly sampled OTS defects that
#     require reviewer evidence) are folded into the expanded_ots set before its
#     SSOT is built, so the score reflects static + semantic.
# Missing task trees are skipped; score_qc reports which labels did not overlap.
set -euo pipefail
cd "$(dirname "$0")/.."

OUT=/tmp/qc_expanded
rm -rf "$OUT"
mkdir -p "$OUT"

sets=()
run_set () {  # <tasks-dir> <out-subdir>
  local tdir="$1" odir="$OUT/$2"
  [ -d "$tdir" ] || { echo "skip $tdir (absent)"; return; }
  python3 scripts/run_static_qc.py "$tdir" --out-dir "$odir" >/dev/null
  sets+=("$2")
}

run_set eval/fixtures fixtures
run_set tasks_cache_expanded expanded_ots
run_set tasks_cache ots_cache
run_set tasks_cache_tb public_tb
run_set tasks_cache_v200 v200

if [ "${#sets[@]}" -eq 0 ]; then
  echo "No task trees found to score."
  exit 1
fi

# Fold the confirmed Layer-2 semantic findings into the expanded_ots set and
# re-aggregate so its SSOT reflects static + semantic.
if [ -d "$OUT/expanded_ots" ]; then
  cp eval/expanded_sem_findings/sem_*.json "$OUT/expanded_ots/" 2>/dev/null || true
  ( cd scripts && python3 aggregate.py "$OUT/expanded_ots" --out-dir "$OUT/expanded_ots" >/dev/null )
fi

# Same for the v200 cold-discovery set: fold in its confirmed semantic defect
# findings AND the verify-refuted metas (which drop the static leakage flags the
# Layer-2 review cleared as false positives), then re-aggregate.
if [ -d "$OUT/v200" ]; then
  cp eval/expanded_sem_findings_v200/*.json "$OUT/v200/" 2>/dev/null || true
  ( cd scripts && python3 aggregate.py "$OUT/v200" --out-dir "$OUT/v200" >/dev/null )
fi

# Build one SSOT, deduping by task and keeping the WORST verdict. Some OTS
# tasks live in BOTH tasks_cache and tasks_cache_expanded; without dedup the
# clean ots_cache row could overwrite the defective expanded_ots row.
python3 - "$OUT" "${sets[@]}" <<'PY'
import csv, os, sys
OUT = sys.argv[1]
sets = sys.argv[2:]
rank = {"PASS": 0, "WARN": 1, "FAIL": 2}
rows, hdr = {}, None
# expanded_ots last so the semantic-bearing rows win ties
order = [s for s in ("fixtures", "ots_cache", "public_tb", "expanded_ots", "v200") if s in sets]
for sub in order:
    p = f"{OUT}/{sub}/review-ssot.csv"
    if not os.path.exists(p):
        continue
    rs = list(csv.DictReader(open(p)))
    if rs:
        hdr = list(rs[0].keys())
    for r in rs:
        t = r["task"]
        if t not in rows or rank.get(r["overall"], 0) >= rank.get(rows[t]["overall"], 0):
            rows[t] = r
with open(f"{OUT}/ssot.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=hdr)
    w.writeheader()
    w.writerows(rows.values())
print(f"combined SSOT: {len(rows)} tasks")
PY

echo "=== expanded precision / recall (static + semantic) ==="
python3 scripts/score_qc.py "$OUT/ssot.csv" eval/expanded_labels.csv
