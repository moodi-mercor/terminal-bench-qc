# How to Bulk-Fix a File Across Many RL Studio Tasks — Fast

A practical guide to editing one file (e.g. `tests/test.sh`) across thousands of OTS
tasks in RL Studio and pushing the fix back **in minutes, not days** — without a
delete/re-upload, without tripping rate limits.

Proven end-to-end 2026-07-07 on the conftest-plant rollout: **9,011 tasks patched in ~28 min**.

---

## TL;DR

1. **Fix in place — never delete + re-upload.** `POST /snapshots/task/{id}/update`
   creates a *new immutable snapshot* and keeps the old one. Task IDs, trajectories, and
   eval history stay attached; it's fully reversible. Deleting orphans all of that.
2. **The write endpoint has no documented rate cap — it tolerates ~12–15-wide concurrency
   at ~330 writes/min with zero 429s.** Any "5/min" you see is a *self-imposed* throttle,
   not the server. Fan writes out over a thread pool.
3. **There is NO batch/presigned upload endpoint.** Reads get presigned S3 URLs; writes
   have exactly one path (`.../update`). Concurrency on that endpoint is the only lever.
4. Always **back up the original + verify the new snapshot + log per-task state** so the
   run is resumable and reversible.

---

## The API (campaign `[OTS] Terminal Bench`)

Base `https://api.studio.mercor.com`. Headers on every call:
`Authorization: Bearer $RLS_KEY`, `X-Campaign-Id`, `X-Company-Id`
(ids in `_local/references/studio-data-access.md`). **Writes use `RLS_WRITE_KEY`**
(annotator role), reads use `RLS_KEY`.

| Step | Endpoint | Notes |
|---|---|---|
| Enumerate tasks | `GET /tasks/world/{world}/full` | ~13k rows, ~60s; `studio_pull.list_tasks` caches it. **Do NOT use `/querier` — it silently caps at 100 rows.** Filter `custom_fields` locally. |
| List a task's files | `GET /snapshots/task/{id}/input-files` | keys look like `tasks/snap_<id>/filesystem/<path>` |
| Download a file | `GET /snapshots/task/{id}/file-url?file_path=filesystem/<path>` | returns a presigned S3 URL → GET it (no auth). `file_path` must start with `filesystem/` |
| **Write a file** | `POST /snapshots/task/{id}/update` | multipart `files=[(...)]`; new snapshot, reversible |

### The two upload gotchas that waste an afternoon
- **Upload path must include the `filesystem/` prefix.** Filenames are stored verbatim,
  snapshot-relative. Upload to `filesystem/tests/test.sh`, NOT `tests/test.sh` — the latter
  lands one level too high and leaves the real graded file untouched (silent no-op).
- Pass `data={"remove_files": "tests/test.sh"}` to strip any stray top-level copy.

```python
requests.post(f"{API}/snapshots/task/{tid}/update",
    headers={"Authorization": f"Bearer {wkey}", "X-Campaign-Id": CAMP, "X-Company-Id": COMP},
    files=[("files", ("filesystem/tests/test.sh", content.encode(), "text/x-sh"))],
    data={"remove_files": "tests/test.sh"}, timeout=180)
```

---

## The recipe

1. **Enumerate + filter locally.** Pull the full world list, filter on the flag/CQV you
   care about (e.g. `custom_fields['qc_conftest_vulnerable'] == 'true'`).
2. **Fetch → transform → back up.** For each task, download the file, apply the edit,
   write the original to `<out>/backup/<task>/<file>` *before* any write. Skip tasks
   already fixed (idempotent transform: bail if the fix is already present).
3. **Upload concurrently.** Thread pool, **12 workers** is the sweet spot (15 also clean;
   don't go higher — a few saved minutes isn't worth risking abuse protection throttling
   the whole batch). Retry 429 with backoff anyway, just in case.
4. **Verify.** Re-fetch the file, confirm the change is present. Record status.
5. **Log resumable state.** Append `{name, id, status}` to `state.jsonl` under a lock;
   on restart, skip anything already `patched`/`skipped-*`. Transient errors
   (timeouts, 502s) stay retryable — just re-run the tool, it picks them up.

## Reference implementation (reuse it)

- `studio-autoqc/bulk_patch_conftest.py` — sequential, careful, single source of the
  fetch/patch/upload/verify helpers (`fetch_testsh`, `patch`, `upload_testsh`, `load_done`).
- **`studio-autoqc/bulk_patch_conftest_fast.py` — the concurrent runner. Use this.**
  It imports the helpers above and fans them over a `ThreadPoolExecutor` with a lock on
  the state file. Shares the same `out`/`state.jsonl`/`backup` dir, so the two resume off
  each other.

```bash
# dry-run: fetch + patch + backup, no writes
python3 studio-autoqc/bulk_patch_conftest_fast.py --dry-run

# full concurrent rollout (resumable; re-run to sweep up transient errors)
python3 studio-autoqc/bulk_patch_conftest_fast.py --apply --workers 12
```

**To adapt to a different file/edit:** copy the fast runner and swap three things —
the enumeration filter (which tasks), the target filename in `fetch_*`/`upload_*`
(`filesystem/tests/test.sh` → your path), and the transform function (the `patch()`
logic). Everything else — concurrency, backup, verify, resumable state — stays.

---

## Why not delete + re-upload?
It hits the *same* rate-limited `/update` endpoint (two calls/task instead of one, so
slower), throws away the task ID (orphaning every trajectory + eval score), isn't
reversible, and leaves holes in the corpus if it dies mid-run. In-place snapshot update
is the same end state with none of the damage.
