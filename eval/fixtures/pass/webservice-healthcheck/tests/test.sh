#!/bin/bash
set -uo pipefail
python3 -m pip install --break-system-packages -q pytest==9.0.3 2>/dev/null || true
cp /tests/test_outputs.py /app/test_outputs.py 2>/dev/null || true
set +e
python3 -m pytest /app/test_outputs.py -q
rc=$?
set -e
mkdir -p /logs/verifier
if [ $rc -eq 0 ]; then echo 1 > /logs/verifier/reward.txt; else echo 0 > /logs/verifier/reward.txt; fi
exit $rc
