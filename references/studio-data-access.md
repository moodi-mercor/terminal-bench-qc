# Studio Data Access — Terminal Bench OTS

Terminal Bench OTS tasks live in **RL Studio**, campaign **`[OTS] Terminal Bench`**.
Each task stores a full canonical TB2 file tree as a snapshot. Verified end-to-end 2026-06-17.

## Identifiers
- Campaign: `camp_4e196b1414a1499db54b43233104b0a7`  (`[OTS] Terminal Bench`)
- Company:  `comp_2fa4115109d741cd94a3c409ed89e61f`
- Account:  `acct_85b680d4c5ba49a29f19c173672aebea`
- World:    `world_2c7cdb23737845ad83a9acfa1aa8c25b`  (13,430 tasks, all "Ready to Deliver" as of 2026-06-17)

## Auth
- Base URL: `https://api.studio.mercor.com`
- Headers: `Authorization: Bearer $RLS_KEY`, `X-Campaign-Id: <campaign>`, `X-Company-Id: <company>`
- Key is scoped to ONE campaign. Wrong campaign → `403 "API key is not scoped to the requested campaign"`.
- Discover the key's campaign: `GET /campaigns` (returns campaign_id/name/company_id/account_id).
- Identity: `GET /users/me`.
- `GET /openapi.json` returns no paths (docs disabled) — endpoints below were found by probing.

## Endpoints (all GET, follow redirects with `-L`)
| Purpose | Endpoint | Returns |
|---|---|---|
| List worlds | `/worlds` | worlds[] (needs campaign header) |
| List tasks in world | `/tasks/world/{world_id}/full` | `{tasks:[...]}` — each has `task_id`, `task_name`, `world_id`, `version`, `task_status_defn`, `custom_fields`, `task_data_id` (= snapshot id) |
| Task detail | `/tasks/{task_id}` | full task incl. `custom_fields.gh_files` ([{file_s3_url, original_filename}]) and `task_data_id` |
| List snapshot files | `/snapshots/task/{task_id}/input-files` | `{snapshot_id, files:[{key, filename, size, last_modified}]}` — filenames are `filesystem/<tb-path>` |
| **Download a file** | `/snapshots/task/{task_id}/file-url?file_path=filesystem/<path>` | `{url: <presigned S3>}` — then GET the url (no auth). file_path MUST include the `filesystem/` prefix. |

## Canonical task tree (example: actor-claim-renewal-sync)
```
filesystem/task.toml                       # TB2, schema_version "1.1"
filesystem/instruction.md
filesystem/environment/Dockerfile
filesystem/environment/setup_commands.sh
filesystem/environment/<task data files>   # e.g. CONTRACT.md, PROTOCOL_SPEC.md
filesystem/tests/test.sh
filesystem/tests/test_outputs.py
filesystem/solution/solve.sh
```
`custom_fields` carries: `tags`, `domain`, `category`, `subcategory`, `harness` ("harbor"), `operation_type`, `gh_files`.

## task.toml shape (observed)
`schema_version`, `artifacts`; `[metadata]` difficulty/category/subcategory/operation_type/tags/expert_time_estimate_min/junior_time_estimate_min; `[verifier] timeout_sec`; `[agent] timeout_sec`; `[environment]` build_timeout_sec/cpus/memory_mb/storage_mb/gpus/allow_internet/mcp_servers; `[verifier.env]`, `[environment.env]`, `[solution.env]`.

## Gotchas
- `file_path` query param is named `file_path` (NOT `key`); value must be rooted at `filesystem/` and URL-encoded. **The `key` returned by `/input-files` is actually `tasks/snap_<id>/filesystem/<path>` — strip everything before `filesystem/` before passing it as `file_path`, or you get a 404.** (`studio_pull.py` does this.)
- `/tasks/world/{world}/full` returns all ~13k tasks and takes ~60s — `studio_pull.py` caches the result in `$TMPDIR/studio_tasks_<world>.json` (use `--refresh` to re-fetch).
- Presigned S3 URLs are short-lived — download promptly, re-mint if expired.
- Studio also hosts a **Code-QA** campaign/world whose snapshots are PR-review format (`golden.patch`, `rubric.json`, `prompt_statement.md`) — DO NOT confuse with TB OTS. Always use the `[OTS] Terminal Bench` campaign for TB.
- `RLS_KEY` lives in the repo's gitignored `.env`.
