#!/bin/bash
set -uo pipefail
cp /tests/test_outputs.py /app/test_outputs.py
set +e
python3 -m pytest /app/test_outputs.py -q
rc=$?
set -e
mkdir -p /logs/verifier
if [ $rc -eq 0 ]; then echo 1 > /logs/verifier/reward.txt; else echo 0 > /logs/verifier/reward.txt; fi
exit $rc
