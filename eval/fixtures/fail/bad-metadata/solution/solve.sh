#!/bin/bash
set -euo pipefail
mkdir -p /app/out
python3 - <<'PY'
import csv
rows=[r for r in csv.DictReader(open('/app/data/records.csv')) if r['status']!='void']
with open('/app/out/clean.csv','w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=['id','status']); w.writeheader(); w.writerows(rows)
PY
