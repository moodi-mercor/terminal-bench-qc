#!/bin/bash
# Overnight supervisor — runs the ONE governed backfill process under caffeinate.
# repair_autoqc.py self-limits to <10k requests/hr (all verbs) so it cannot re-trip
# the rate limiter. Single process only. Restarts on crash; exits when ALL COMPLETE.
set -u
cd /Users/mahmoodmapara/Desktop/terminal-bench-qc
LOG=_local/autoqc_full/night.log
END=$(( $(date +%s) + 14*3600 ))
echo "[night $(date '+%m-%d %H:%M')] supervisor start" >> "$LOG"
while [ "$(date +%s)" -lt "$END" ]; do
  python3 studio-autoqc/repair_autoqc.py >> _local/autoqc_full/backfill.log 2>&1
  rc=$?
  if grep -q "ALL COMPLETE" _local/autoqc_full/backfill.log 2>/dev/null; then
    echo "[night $(date '+%H:%M')] ALL COMPLETE" >> "$LOG"; break
  fi
  echo "[night $(date '+%H:%M')] process exited rc=$rc — restart in 30s" >> "$LOG"
  sleep 30
done
echo "[night $(date '+%m-%d %H:%M')] supervisor exit" >> "$LOG"
