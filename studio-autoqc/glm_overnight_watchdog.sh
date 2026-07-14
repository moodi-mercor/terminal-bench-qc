#!/bin/bash
# Overnight keepalive for the GLM-5.2 pass@5 loop. If dispatch_glm_fresh dies (network
# blip, transient crash), relaunch it — it resumes from state.json, so nothing is lost.
# Stops itself once the deficit is essentially clear.
D=/Users/mahmoodmapara/Desktop/terminal-bench-qc
CAMP=camp_4e196b1414a1499db54b43233104b0a7
LOG=$D/_local/fresh_refl_glm52_pass5/original.log
WLOG=$D/_local/fresh_refl_glm52_pass5/watchdog.log
STATE=$D/_local/fresh_refl_glm52_pass5/state.json

launch() {
  cd $D/studio-autoqc
  nohup python3 dispatch_glm_fresh.py --camp $CAMP \
    --tasks $D/_local/qc_out_eval_pool/glm_pass5_tasks.json \
    --runs 5 --wave 1600 --gate 500 \
    --pool-batches $D/_local/fresh_refl_glm52_pass5/pool_batches.json \
    --orch orch_4561e5a556ad4a99814df01c398f8ffc,orch_264284455e804250beb366f678b3947c \
    --orch-ver 1,1 --tag refl_glm52_pass5 --execute >> $LOG 2>&1 &
  echo "$(date '+%H:%M:%S') watchdog: (re)launched dispatch PID $!" >> $WLOG
}

echo "$(date '+%H:%M:%S') watchdog: started" >> $WLOG
while true; do
  # done? banked >= ~99% of target -> stop
  banked=$(python3 -c "import json;s=json.load(open('$STATE'));print(sum(len(v) for v in s['genuine'].values()))" 2>/dev/null || echo 0)
  full=$(python3 -c "import json;s=json.load(open('$STATE'));print(sum(1 for v in s['genuine'].values() if len(v)>=5))" 2>/dev/null || echo 0)
  if [ "${full:-0}" -ge 5560 ]; then
    echo "$(date '+%H:%M:%S') watchdog: $full/5627 tasks complete — stopping watchdog" >> $WLOG
    break
  fi
  if ! pgrep -f dispatch_glm_fresh.py >/dev/null; then
    echo "$(date '+%H:%M:%S') watchdog: dispatch not running (banked=$banked full=$full) — relaunching" >> $WLOG
    launch
    sleep 30
  fi
  sleep 120
done
