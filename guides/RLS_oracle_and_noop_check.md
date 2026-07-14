# How to Run Oracle (Golden) + No-op Checks on an OTS Task Set in RL Studio

A practical guide to **behaviorally validating** any off-the-shelf (OTS) task set in RL Studio —
i.e. proving each task is *solvable* and its verifier is *discriminative* — without writing a
single LLM token.

This is **provider-agnostic**: it works for any Harbor-style task world (code, terminal, data,
etc.). Replace the `<PLACEHOLDERS>` with your own world/campaign IDs.

---

## 1. What this checks and why

Every task ships with two things you can validate mechanically:

| Run | What it does | Expected result | If it's wrong… |
|---|---|---|---|
| **Oracle (golden)** | runs the task's **reference solution** (`solution/solve.sh`) | verifier returns **reward = 1** | **Broken task** — the official answer fails its own test. Unusable. |
| **No-op (empty)** | runs **nothing** (empty diff) | verifier returns **reward = 0** | **Verifier too weak** — it passes even when no work is done (reward-hackable). |

A healthy task = **oracle passes AND no-op fails**. Both run on cloud sandboxes (e.g. Modal); the
"agent" is a fixed validator, so there is **$0 LLM spend** and it's fast (~1–3 min/task).

> ⚠️ **Do NOT use a model / LLM for this check.** There is no inference involved. The oracle run
> just executes the task's own `solution/solve.sh`; the no-op run submits an empty patch; the
> task's **deterministic verifier** decides pass/fail. When you pick the "agent" (UI or API),
> choose the **fixed oracle/validation agent — never a model agent** (Claude, GPT, Gemini, etc.).
> Selecting a model would (a) cost tokens, (b) be non-deterministic, and (c) measure the *model*,
> not the *task*. This is a mechanical solvability + verifier-strength check, not an eval.

> The single most valuable QC signal for a task set: a high oracle-fail rate means the corpus is
> shipping broken tasks; any no-op-pass means a verifier accepts nothing-burgers.

---

> **Two ways to do this, both in RL Studio:** the **Studio UI** (point-and-click "Batch Run" —
> §3a) or the **API** (scriptable, for large sets — §3b). They drive the same engine; the UI is
> easiest for a first pass, the API scales to thousands of tasks.

## 2. Prerequisites

- **Studio:** `https://studio.mercor.com` (UI) · `https://api.studio.mercor.com` (API)
- **API key:** in Studio, go to the **API Keys** tab → create a key → put it in a `.env` as
  `RLS_API_KEY=<your-key>`. (Never share keys.) For writes/runs you may need a write-tier key.
- **Headers** on every call:
  ```
  Authorization: Bearer <RLS_KEY>
  X-Campaign-Id: <YOUR_CAMPAIGN_ID>
  X-Company-Id:  <YOUR_COMPANY_ID>
  X-Account-Id:  <YOUR_ACCOUNT_ID>
  Content-Type:  application/json
  ```
- **IDs you'll need to gather** (steps below): your `world_id`, the **validator agent** id, and an
  **orchestrator** id+version.

---

## 3a. The easy way — Studio UI (Batch Run)

For a quick pass or a smaller set, do it entirely in the web app:

1. **Open your world/dataset** in Studio and select the tasks you want to validate (filter or
   select-all).
2. **Create a Batch Run** (Batch Runs / "Run" action). A batch run executes an agent across many
   tasks — that's the engine behind this whole check.
3. **Pick the agent = the oracle/validator agent** (the one that runs `solution/solve.sh` and an
   empty patch). Ask your admin which agent that is if it's not obvious — it's a fixed validation
   agent, not a model.
4. **Pick an orchestrator** (the runtime). Any campaign orchestrator works; you're not testing a
   model, just running the validator.
5. **Submit**, then watch the batch in the **Batch Runs / Analytics** view.
6. **Open the trajectories** and read each one's verifier result — the **golden** (oracle) score
   should be 1 and the **empty/no-op** score should be 0. Sort by score to find the failures fast.

That's the same outcome as the API flow below; the UI just hides the IDs and curl. For anything
above a few hundred tasks, use the API path (it's resumable and scriptable).

## 3b. The scalable way — API

### Step A — Get the task IDs you want to validate
Use the querier (SQL over the task table). It returns task IDs (capped at 5,000 per call — page if larger).

```bash
curl -s -X POST "$API/querier/task-ids" -H "$AUTH" -H "$HDRS" -d '{
  "query": "SELECT task_id FROM tasks WHERE world_id='\''<YOUR_WORLD_ID>'\'' AND is_latest=TRUE AND archived_at IS NULL"
}'
```
Save the IDs to a file. (Field name is `query`, not `sql_query`.)

### Step B — Find the validator agent
This is the fixed agent that runs the oracle + no-op (often called `validate_patch` or an
"oracle"/"validation" agent). List agents and pick it, or ask your Studio admin:

```bash
curl -s "$API/agents" -H "$AUTH" -H "$HDRS" | jq '.agents[] | {id, name, version}'
```
Record its `agent_id` + `version`. *(In one setup this was `agent_ec6f9201…`; yours may differ.)*

### Step C — Find an orchestrator
Each trajectory needs an orchestrator (the runtime config). **Gotcha:** a world's
`default_orchestrator_ids` is often **empty** — don't rely on it. Fetch one from your campaign:

```bash
curl -s "$API/orchestrators?campaign_id=<YOUR_CAMPAIGN_ID>" -H "$AUTH" -H "$HDRS" \
  | jq '.orchestrators[] | {id, version}'
```
Record an `orchestrator_id` + `version`.

### Step D — Create the validation batch
`POST /orchestration/trajectories/batch`. One entry per task; each entry references the task,
the orchestrator, and the validator agent.

```bash
curl -s -X POST "$API/orchestration/trajectories/batch" -H "$AUTH" -H "$HDRS" -d '{
  "name": "oracle-noop-validation-<DATE>",
  "trajectories": [
    {
      "task_id": "<TASK_ID>",
      "orchestrator_id": "<ORCH_ID>", "orchestrator_version": <ORCH_VER>,
      "agent_id": "<VALIDATOR_AGENT_ID>", "agent_version": <AGENT_VER>
    }
    /* …one object per task… */
  ]
}'
```
The response includes a **batch id**. Save it.

**Limits to respect:**
- Batch creation is **rate-limited (~5 batches/min)** — and there is a broader **~10k requests/hour**
  ceiling across *all* API calls (GET+POST). Don't hammer it with parallel pollers.
- **Max ~50,000 trajectories per batch.** For a big world, chunk into multiple batches.

### Step E — Monitor the batch
```bash
curl -s "$API/trajectory-batches/<BATCH_ID>" -H "$AUTH" -H "$HDRS" | jq '{status, total, completed}'
```
Poll **sparingly** (e.g. every few minutes) so you don't burn the hourly request budget.

### Step F — Read the results
Pull the trajectories and read their `trajectory_output`:

```bash
curl -s "$API/trajectories/batch/<BATCH_ID>?limit=100&offset=0" -H "$AUTH" -H "$HDRS" \
  | jq '.trajectories[] | {task_id,
      golden:  .trajectory_output.golden_score,
      empty:   .trajectory_output.empty_score,
      passed:  .trajectory_output.validation_passed}'
```

Key fields in `trajectory_output`:
- `golden_score` — oracle run (want **1.0**)
- `empty_score` — no-op run (want **0.0**)
- `validation_passed` — `true` iff golden=1 **and** empty=0
- `test_statuses` — per-check pass/fail (useful for diagnosing *which* assertion the oracle failed)

---

## 4. How to read the outcomes

| golden | empty | Verdict | Action |
|---|---|---|---|
| 1.0 | 0.0 | ✅ Healthy | keep |
| **0.0** | 0.0 | ❌ **Broken oracle** — official solution fails its own test | fix the solution/verifier, or cull |
| 1.0 | **1.0** | ❌ **No-op passes** — verifier accepts empty work | strengthen the verifier (it's reward-hackable) |
| 0.0 | 1.0 | ❌ Inverted/garbage | the verifier is fundamentally wrong |

Tally the broken-oracle rate and no-op-pass rate across the set — those are your headline QC numbers.

---

## 5. Gotchas (learned the hard way)

- **The hourly cap counts GETs too.** Polling loops and per-task detail fetches eat the same
  ~10k/hr budget as batch creation. Run **one** governed worker, not many parallel pollers, or
  everything starts returning `429`.
- **Empty `default_orchestrator_ids`** — fetch an orchestrator from the campaign (Step C); don't
  assume the world provides one.
- **Determinism matters.** A trustworthy task passes the oracle and fails the no-op *consistently*.
  If you see flaky results, re-run that task a few times before trusting the verdict.
- **Validate from a clean state.** These runs build the task image fresh — that's the point. Don't
  validate against a locally-modified or pre-solved workspace.
- **$0 LLM tokens, but not free.** It uses sandbox CPU time; a 13k-task world is hours of wall-clock.
  Chunk and run in the background.
- **Persist results to disk and make the pull resumable** (write each trajectory's verdict to a
  JSONL keyed by task_id), so a dropped connection doesn't cost you the whole run.

---

## 6. Minimal automation sketch

```
1. querier  -> task_ids.txt
2. GET agents, GET orchestrators -> record validator agent + orchestrator id/version
3. chunk task_ids into batches of <= 50k (respecting 5 batches/min)
4. POST /orchestration/trajectories/batch per chunk -> save batch_ids
5. poll GET /trajectory-batches/{id} every few minutes until complete
6. page GET /trajectories/batch/{id} -> append {task_id, golden, empty, passed} to results.jsonl
7. tally: broken-oracle = golden<1 ; noop-pass = empty>=1 ; healthy = passed==true
```

Keep one process, pace requests under the hourly cap, and you can validate an entire OTS world
end-to-end with no model spend.
