#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier
# invokes the in-image, agent-writable grader and greps its output
python3 /app/verify.py > /tmp/grade.txt 2>&1
if grep -q SUCCESS /tmp/grade.txt; then echo 1 > /logs/verifier/reward.txt; else echo 0 > /logs/verifier/reward.txt; fi
