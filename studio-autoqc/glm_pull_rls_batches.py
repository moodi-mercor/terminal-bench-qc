#!/usr/bin/env python3
"""Pull per-task GLM-5.2 scores from ALL GLM-5.2 trajectory batches on RLS Studio
(the July-5 strong/weak-split runs + retries + this session's runs), filtering to VALID
genuine attempts (real tokens, no rate-limit/auth error) — same rule as the local harness
data. Writes _local/glm_rls_pulled.json = {rls_task_id: [scores...]}.

Paginated raw pull (LIMIT/OFFSET) then aggregate locally — avoids the querier's GROUP-BY
row truncation. GLM-5.1 batches are excluded (different model)."""
import json, sys, time
sys.path.insert(0, "/Users/mahmoodmapara/Desktop/terminal-bench-qc/studio-autoqc")
import glm_retry_lib as L, requests
from collections import defaultdict

L_DIR = "/Users/mahmoodmapara/Desktop/terminal-bench-qc/_local"
MIN_TOK = 2000
PAGE = 2000


def q(sql, tries=4):
    for _ in range(tries):
        r = requests.post(f"{L.API}/querier/unstructured", headers=L.H, json={"query": sql}, timeout=180)
        if r.status_code == 200:
            return r.json().get("rows", [])
        time.sleep(4)
    print("  QUERY FAIL:", r.status_code, r.text[:120]); return []


# GLM-5.2 batches only (exclude 5.1 + the Canonical-gap 5.1 run)
bids = [r["bid"] for r in q(
    "SELECT trajectory_batch_id AS bid FROM trajectory_batches "
    "WHERE (trajectory_batch_name ILIKE '%glm-5.2%' OR trajectory_batch_name ILIKE '%glm52%') "
    "AND trajectory_batch_name NOT ILIKE '%5.1%'")]
print(f"GLM-5.2 batches: {len(bids)}")
inlist = "','".join(bids)

by_task = defaultdict(list)
total_rows = valid_rows = 0
off = 0
while True:
    rows = q(f"""SELECT task_id AS tid,
                        trajectory_output->>'score' AS score,
                        (trajectory_output->'usage_metrics')->>'total_tokens' AS toks,
                        trajectory_output->>'error_message' AS err
                 FROM trajectories
                 WHERE trajectory_batch_id IN ('{inlist}')
                 ORDER BY trajectory_id LIMIT {PAGE} OFFSET {off}""")
    if not rows:
        break
    for r in rows:
        total_rows += 1
        sc, tk, err = r.get("score"), r.get("toks"), (r.get("err") or "")
        if sc is None:
            continue
        try:
            tkn = int(tk) if tk is not None else 0
        except Exception:
            tkn = 0
        bad = any(m in err for m in ("RateLimit", "rate limit", "Authentication", "Unauthorized"))
        if tkn < MIN_TOK or bad:
            continue
        by_task[r["tid"]].append(float(sc))
        valid_rows += 1
    print(f"  offset {off}: +{len(rows)} rows | valid so far {valid_rows}/{total_rows}", flush=True)
    off += PAGE
    if len(rows) < PAGE:
        break

json.dump(by_task, open(f"{L_DIR}/glm_rls_pulled.json", "w"))
print(f"\nDONE | rows scanned={total_rows} valid={valid_rows} | tasks with >=1 valid={len(by_task)}")
print(f"-> {L_DIR}/glm_rls_pulled.json")
