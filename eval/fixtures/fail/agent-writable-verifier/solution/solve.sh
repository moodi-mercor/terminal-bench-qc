#!/bin/bash
set -euo pipefail
python3 - <<'PY'
import csv
total = sum(int(r["amount"]) for r in csv.DictReader(open("/app/data/sales.csv")))
open("/app/out/total.txt", "w").write(str(total))
PY
