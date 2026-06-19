#!/bin/bash
set -euo pipefail
n=$(wc -w < /app/data/in.txt)
mkdir -p /app/out
echo "$n" > /app/out/count.txt
