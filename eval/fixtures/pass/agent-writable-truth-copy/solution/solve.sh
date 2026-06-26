#!/bin/bash
set -euo pipefail
mkdir -p /app/out
awk "\$1==\"WARN\"{n++} END{print n+0}" /app/logs/app.log > /app/out/warn.txt
