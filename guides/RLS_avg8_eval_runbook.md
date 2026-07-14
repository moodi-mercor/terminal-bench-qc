# RLS avg@8 difficulty eval — runbook

How we measured `avg@8` (difficulty) for the Reflection delivery_2 pool in RL Studio, and how to
continue on tasks that don't have evals yet. avg@8 = fraction of 8 attempts a frontier agent solves;
keep tasks with **avg@8 ≤ 0.5**.

All scripts in `studio-autoqc/`. Auth = `RLS_KEY` from repo-root `.env`; headers below.

## Fixed IDs (delivery_2 world)

```
API      https://api.studio.mercor.com
world    world_d07785c2757b4a5cb643517cbea8ec98   # "Reflection delivery_2 — eval pool"
campaign camp_4e196b1414a1499db54b43233104b0a7
company  comp_2fa4115109d741cd94a3c409ed89e61f
account  acct_85b680d4c5ba49a29f19c173672aebea
agent    agent_ef13be96aaf149d39d5bf5fdbc5077f9 v2   # Lighthouse Harbor / Terminus-2
GPT-5.4  orch_dfafb7e86f4442728e9584f22ff67f70 v12  (reasoning effort high)   # spec model
Opus-4.8 orch_e3599ac0f823422c928fbd2982aa3116 v4   (adaptive, effort high)   # alt (spec allows either)
```
Headers on every call: `Authorization: Bearer $RLS_KEY`, `X-Campaign-Id`, `X-Company-Id`, `X-Account-Id`,
`User-Agent: curl/8.7.1`, `Content-Type: application/json`.

## Prerequisites (once per world)

1. Tasks must be **imported into the world** with a snapshot (files). See `rls_import.py`.
2. The world must have a **platform** or dispatch 400s with "no platform":
   `PATCH /worlds/{world_id}` with `default_platform_ids=["platform_85f92fcdcd534f839799b876bbcc9bb6"]`
   and `default_agent_ids=["agent_ef13be96…"]` (copied from the Canonical Tasks world).

## THE key lesson: dispatch in throttled waves

Firing all ~45k trajectories at once → the platform can't build task images → `ResourceExhausted`,
`env_image_status = None`, ~90% error. **Dispatch in waves, holding in-flight under a ceiling.**

- Ceiling = pending+running trajectories in our batches. **~16k works; ~20k+ starts killing it.**
- Runs per task = 8. Wave = 150–400 tasks. Bigger waves = more platform retries (waste); gentler is cleaner.

### Dispatch: `studio-autoqc/wave_eval.py`
- Reads all task_ids for the world, skips those already in `wave_dispatched.txt` (resumable).
- Loop: if in-flight ≥ `CEILING`, wait; else POST one wave (`WAVE` tasks × `RUNS` runs) to
  `POST /orchestration/trajectories/batch`, append task_ids → `wave_dispatched.txt`, batch id → `wave_batch_ids.txt`.
- Tunables at top: `WAVE`, `CEILING`, `RUNS`. Run: `nohup python3 studio-autoqc/wave_eval.py &`
- Batch body per wave:
  ```json
  {"trajectory_batch_name":"...","orchestrator_ids":["orch_dfafb7e8…"],"judge_ids":[],
   "trajectory_request":[{"task_id","orchestrator_id","orchestrator_version":12,
     "agent_id":"agent_ef13be96…","agent_version":2,"system_prompt":"<SP>"}, ... x8 per task]}
  ```

## Reading results (querier gotcha)

`POST /querier/unstructured` with `{"query": "<SQL>"}` over table `trajectories`.
Score per run: `trajectory_output->>'score'` (text '0.0'/'1.0'). Status: `trajectory_status`
(`completed`/`error`/`failed`/`running`/`pending`/`cancelled`).

**GOTCHA: the querier TRUNCATES large row results (GROUP BY lists, SELECT lists) → false zeros.**
Only trust:
- **Server-side COUNT** (single number, never truncated):
  ```sql
  SELECT COUNT(*) n FROM (SELECT task_id FROM trajectories WHERE trajectory_batch_id IN ('<ids>')
    AND trajectory_status='completed' GROUP BY task_id HAVING COUNT(*)>=8) s
  ```
- **Paginated** lists (`ORDER BY task_id LIMIT 500 OFFSET N`) when you need the actual task list.

Passing count (avg@8 ≤ 0.5, ≥8 completed):
```sql
SELECT COUNT(*) n FROM (SELECT task_id FROM trajectories WHERE trajectory_batch_id IN ('<ids>')
  AND trajectory_status='completed' GROUP BY task_id
  HAVING COUNT(*)>=8 AND AVG((trajectory_output->>'score')::float)<=0.5) s
```
Per-task avg@8 list: same but `SELECT task_id, AVG((trajectory_output->>'score')::float)`, paginated.

Because ~20k trajectories error (platform retries), some tasks land < 8 clean completions. Top-up only
the **genuinely partial** ones (server-side `HAVING COUNT BETWEEN 1 AND 7`) — never re-dispatch the
"0 completed" set from a truncated query, or you re-run thousands already done.

## To continue on tasks WITHOUT evals

1. **Get the not-yet-scored task_ids** (reliable, paginated):
   ```sql
   SELECT task_id FROM trajectories WHERE trajectory_batch_id IN ('<all wave batch ids>')
     AND trajectory_status='completed' GROUP BY task_id HAVING COUNT(*)>=8
     ORDER BY task_id LIMIT 500 OFFSET N          -- these are DONE; the rest of the world's tasks need eval
   ```
   Subtract the DONE set from the world's full task list (`GET /tasks/world/{world_id}`).
   For a NEW task set (e.g. batch_1's 1,041, or another world), just point `wave_eval.py` at its ids.
2. **Seed** `wave_dispatched.txt` with anything already done, `wave_batch_ids.txt` with existing batch ids
   (so it resumes and in-flight is counted correctly).
3. **Run** `wave_eval.py` (WAVE=200, CEILING=16000 is a safe default). Let it drain.
4. **Pull** the passing count / per-task avg@8 with the server-side queries above.
5. **Write back**: put each `avg_at_8` into the task's `task.toml` and the RLS custom field
   `custom_fields->>'field_avg_at_8'` via `POST /tasks/bulk-update`
   (column_key format is `custom_fields->>'field_id'`, NOT `custom_fields:field_id`).

## Files (state, resumable)
```
_local/qc_out_eval_pool/wave_dispatched.txt   # task_ids already dispatched (dedup guard)
_local/qc_out_eval_pool/wave_batch_ids.txt    # every batch id created (for querying results)
_local/qc_out_eval_pool/passing_avg8.json     # {task_name: avg8} for passing tasks
```

## Selecting the delivery set
Passing tasks (avg@8 ≤ 0.5) → sort hardest-first (lowest avg@8) → take the target count while keeping
each category ≤ 20% of the selection. See how delivery_2 (2,500) was picked from 2,697 passing.
