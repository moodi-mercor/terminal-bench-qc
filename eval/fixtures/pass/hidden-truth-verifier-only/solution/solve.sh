#!/bin/bash
set -euo pipefail
mkdir -p /app/out
awk '$1=="ERROR"{n++} END{print n+0}' /app/logs/service.log > /app/out/error_count.txt
