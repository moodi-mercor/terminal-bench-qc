#!/bin/bash
set -uo pipefail
python3 -m pytest /app/test_outputs.py -q; rc=$?
mkdir -p /logs/verifier; [ $rc -eq 0 ] && echo 1 > /logs/verifier/reward.txt || echo 0 > /logs/verifier/reward.txt; exit $rc
