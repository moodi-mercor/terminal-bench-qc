#!/bin/bash
set -euo pipefail
mkdir -p /app/out
awk "{s+=\$1} END{print s+0}" /app/data/nums.txt > /app/out/total.txt
