#!/bin/bash
# BUG: `set -e` with no `set +e` around pytest — on a failing run the script
# aborts before the reward=0 write, so a no-op yields no reward file (not 0.0).
set -e
python3 -m pip install --break-system-packages -q pytest==9.0.3 2>/dev/null || true
cp /tests/test_outputs.py /app/test_outputs.py 2>/dev/null || true
python3 -m pytest /app/test_outputs.py -q
rc=$?
mkdir -p /logs/verifier
if [ $rc -eq 0 ]; then echo 1 > /logs/verifier/reward.txt; else echo 0 > /logs/verifier/reward.txt; fi
exit $rc
