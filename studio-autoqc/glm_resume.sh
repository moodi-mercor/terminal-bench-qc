#!/bin/bash
# Auto-resume for the GLM-5.2 top-up: wait for the in-flight wave batch to drain,
# harvest it (idempotent), then relaunch the retry loop for the remaining deficit.
cd /Users/mahmoodmapara/Desktop/terminal-bench-qc
WAVE=$(grep -oE "batch batch_[0-9a-f]+" _local/glm52_retry/loop.log | tail -1 | awk '{print $2}')
echo "resuming: waiting for wave batch $WAVE to drain"
until python3 - "$WAVE" <<'EOF' 2>/dev/null
import sys
sys.path.insert(0,"studio-autoqc")
import glm_retry_lib as L
st=L.batch_status(sys.argv[1])
active=st.get("pending",0)+st.get("running",0)
print(f"  {dict(st)}",flush=True)
sys.exit(0 if active==0 else 3)
EOF
do sleep 90; done
echo "wave drained; harvesting"
python3 studio-autoqc/glm_retry_run.py --seed-batch "$WAVE" 2>&1 | grep -vE "NotOpenSSL|warnings.warn"
echo "relaunching retry loop"
exec python3 studio-autoqc/glm_retry_run.py --execute --chunk 6000 2>&1 | grep -vE "NotOpenSSL|warnings.warn"
