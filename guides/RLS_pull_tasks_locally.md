# Pulling tasks & data from RL Studio (RLS) to your local machine

Two supported paths. Pick based on what you have:

| Path | What you need | Best for |
|---|---|---|
| **1. Direct REST API** | An RLS API key (`RLS_KEY`) + the campaign/company IDs | Bulk pulls, scripts, CI — works anywhere, no Claude needed |
| **2. Mercor MCP (inside Claude Code)** | The `mercor-mcp` server connected in Claude Code | Ad-hoc queries, one-off lookups, letting Claude drive |

You do **not** need both. The REST API is the workhorse for pulling task trees locally; MCP is convenient for interactive querying.

---

## Path 1 — Direct REST API

### Auth

- Base URL: `https://api.studio.mercor.com`
- Get an API key from the Studio settings page for your campaign (or ask the campaign owner). Keep it in a gitignored `.env` as `RLS_KEY=...`.
- Every request needs these headers:

```python
H = {
    "Authorization": f"Bearer {RLS_KEY}",
    "X-Campaign-Id": "camp_...",   # your campaign id
    "X-Company-Id":  "comp_...",   # your company id
    # some endpoints also accept/want X-Account-Id
}
```

Campaign/company/world IDs are visible in Studio URLs when you browse the campaign.

### List all tasks in a world

```python
import requests
data = requests.get(f"{API}/tasks/world/{WORLD}/full", headers=H, timeout=300).json()
tasks = data["tasks"]   # includes task_id, task_name, status, custom_fields, tags
```

Gotcha: `/full` over a big world (~13k tasks) takes ~60s — cache the JSON locally and re-use it.

### Download a task's files (the actual task tree)

Task files live in a **snapshot**. Two calls per file:

```python
# 1. list the snapshot's files
files = requests.get(f"{API}/snapshots/task/{task_id}/input-files", headers=H).json()["files"]

# 2. per file: mint a presigned URL, then plain GET (no auth on the S3 URL)
j = requests.get(f"{API}/snapshots/task/{task_id}/file-url", headers=H,
                 params={"file_path": fs_path}).json()
blob = requests.get(j["url"], timeout=120).content
```

Gotchas:
- Snapshot keys look like `tasks/snap_<id>/filesystem/<path>`. The `file-url` endpoint wants the path rooted at `filesystem/`; strip that prefix when writing locally so you end up with a standard task tree.
- Presigned URLs expire — if a download fails, re-mint the URL and retry, don't reuse it.

A complete working reference implementation is in this repo:
`skills/static-semantic-qc/scripts/studio_pull.py` (list → snapshot → download, with caching and retries). Usage:

```bash
python studio_pull.py --list
python studio_pull.py --n 50 --out ./tasks_cache
python studio_pull.py --task-id task_xxx --out ./tasks_cache
```

### Pull trajectories / eval results

```python
# full trajectory record: output, command_history, scores, test summary…
traj = requests.get(f"{API}/trajectories/{trajectory_id}", headers=H, timeout=120).json()
```

Note: per-run scores live in the trajectory detail's `trajectory_output.score`, not in list-endpoint `final_score`.

### Bulk / SQL-style queries: the querier

Read-only SQL over the Studio postgres:

```python
rows = requests.post(f"{API}/querier/unstructured", headers=H,
                     json={"query": "SELECT task_id, task_name FROM tasks WHERE ... LIMIT 5000"},
                     timeout=300).json()["rows"]
```

Querier gotchas (learned the hard way):
- Rows are **silently capped at 100** unless the query has an explicit `LIMIT`.
- `/querier/task-ids` caps at ~5k ids.
- JSON/jsonb expressions in `WHERE` or `GROUP BY` over large tables (300k+ trajectories) **time out** — put jsonb only in the `SELECT` list, page with plain `WHERE id > last_id ORDER BY id LIMIT n`, and aggregate locally.
- Custom fields: query as `custom_fields->>'<field_id>'` (the field *id*, not the display name).
- Retry on 429/5xx with backoff; keep bulk pulls to a bounded request rate (a few thousand req/hr is safe).

---

## Path 2 — Mercor MCP inside Claude Code

If you have `mercor-mcp` connected in Claude Code, you don't need to hand-roll requests:

1. **Vetted skills** — ask Claude to search the skills catalog (`discover_skills`) for:
   - `studio-querier` — read-only SQL pulls of worlds/tasks/trajectories/grades/snapshots, handles auth, pagination, jsonb, and snapshot download endpoints.
   - `studio-task-operations` — seed / bulk-transition / **import & export** tasks on a world.

   These come from the `@mercor/mercor-skills-role-spl` plugin — installing that plugin gives you the skills directly.

2. **Direct MCP tools** — the `code_data_*` tool family covers the code-data inventory, e.g. `code_data_list_tasks`, `code_data_get_task_by_id`, `code_data_get_task_download_url` (returns a URL you can curl to disk).

MCP still authenticates as *you* (Okta), so you need access to the campaign — but you don't need to manage an RLS_KEY.

---

## TL;DR for your friend

- **Yes, you need an API key** (RLS_KEY + campaign/company IDs) for the scripted path, **or** the mercor-mcp connection in Claude Code for the interactive path.
- Scripted bulk pull = `GET /tasks/world/{world}/full` → per task `GET /snapshots/task/{id}/input-files` → per file `GET /snapshots/task/{id}/file-url` → GET the presigned URL.
- Interactive = install the SPL skills plugin and use `studio-querier` / `studio-task-operations`.
