#!/bin/bash
# Chunked GLM-5.2 pass@5 over all delivered tasks via master-code-harnesses execute-batch.
# Ephemeral Modal apps die after ~3h, so run in ~150-task chunks (each ~1.5h) — a fresh
# ephemeral app per chunk. Resumable: a chunk with a .done marker is skipped. Each chunk
# completes all its tasks (epoch-breadth within the chunk), so results land incrementally.
set -u
R=/Users/mahmoodmapara/Desktop/terminal-bench-qc/_local/master-code-harnesses
D=/Users/mahmoodmapara/Desktop/code-qa-evals/benchmark-code-qa-ext
POOL=/Users/mahmoodmapara/Desktop/terminal-bench-qc/_local/refl_eval_pool/delivery_0708
BASE="$R/logs/glm_chunked"; CHUNKDIRS="$R/local/glm_chunks"
CONC=200; K=5; SIZE=150
mkdir -p "$BASE" "$CHUNKDIRS"
export MODAL_PROFILE=mercor-data-delivery
set -a; . "$D/.env"; set +a

# build the task list + chunk assignments once
python3 - "$POOL" "$CHUNKDIRS" "$SIZE" <<'PY'
import os,sys,json
POOL,CH,SIZE=sys.argv[1],sys.argv[2],int(sys.argv[3])
tasks=sorted(d for d in os.listdir(POOL) if os.path.exists(f"{POOL}/{d}/task.toml"))
chunks=[tasks[i:i+SIZE] for i in range(0,len(tasks),SIZE)]
json.dump(chunks,open(f"{CH}/chunks.json","w"))
print(f"{len(tasks)} tasks -> {len(chunks)} chunks of {SIZE}")
PY

NCHUNKS=$(python3 -c "import json;print(len(json.load(open('$CHUNKDIRS/chunks.json'))))")
echo "[chunked] $NCHUNKS chunks | conc=$CONC k=$K size=$SIZE"
for i in $(seq 0 $((NCHUNKS-1))); do
  OUT="$BASE/chunk_$i"
  if [ -f "$OUT/.done" ]; then echo "[chunk $i] already done, skip"; continue; fi
  # materialize this chunk's task dir (copies)
  CT="$CHUNKDIRS/chunk_$i"; rm -rf "$CT"; mkdir -p "$CT"
  python3 - "$POOL" "$CHUNKDIRS" "$CT" "$i" <<'PY'
import os,sys,json,shutil
POOL,CH,CT,i=sys.argv[1],sys.argv[2],sys.argv[3],int(sys.argv[4])
chunk=json.load(open(f"{CH}/chunks.json"))[i]
for t in chunk: shutil.copytree(f"{POOL}/{t}",f"{CT}/{t}")
print(f"chunk {i}: {len(chunk)} tasks staged")
PY
  mkdir -p "$OUT"
  echo "[chunk $i] launching $(date +%H:%M:%S)"
  cd "$R" && python3 scripts/run_lighthouse_eval.py \
    --plugin-dir harnesses/lighthouse_based/benchmark-terminal-bench --benchmark terminal_bench \
    --overrides-config configs/default/benchmark_terminal_bench.yaml \
    --model vercel_ai_gateway/zai/glm-5.2 \
    --tasks-dir "$CT" --logs-dir "$OUT" --num-epochs $K --concurrency-limit $CONC \
    >> "$OUT/run.out" 2>&1
  rc=$?
  comp=$(grep -c '] completed:' "$OUT"/run_*.log 2>/dev/null | paste -sd+ - | bc 2>/dev/null)
  echo "[chunk $i] finished rc=$rc completed=$comp $(date +%H:%M:%S)"
  touch "$OUT/.done"
  rm -rf "$CT"   # free disk
done
echo "[chunked] ALL CHUNKS DONE"
