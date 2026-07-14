# Uploading Harbor tasks to RL Studio (RLS)

How to get Terminal-Bench / Harbor tasks — whether they live on GitHub or just
locally — into an RL Studio **world**, labelled and tagged so they show up in the
dashboard. This is the exact pipeline used to import the 961 batch_1 tasks into
`world_d07785c2…`.

Reusable scripts: `studio-autoqc/rls_import.py` (pool) and
`studio-autoqc/rls_import_b1.py` (batch_1 variant). Copy one and change the
paths/world.

---

## Mental model

An RLS **world** is a container of **tasks**. Each task has:
- a **record** — name + notes + custom-field values (the metadata rows you query/filter)
- a **snapshot / filesystem** — the actual Harbor files (instruction.md, task.toml,
  environment/, solution/, tests/)

Uploading is therefore **two separate calls per task**: create the record, then
push the files. Then two optional passes make it usable: **label** (custom fields
→ dashboard) and **tag**.

```
GitHub repo ──clone/archive──▶ local task tree ──▶ [1] import records
                                                  └▶ [2] upload file trees
                                                     [3] label custom fields
                                                     [4] tag
                                                     [5] verify
```

Nothing here is GitHub-specific — RLS never talks to GitHub. You just need the
task files **on disk**. "From GitHub" = clone first; "locally" = point at your dir.

---

## Prerequisites

### Credentials & IDs
Put your RLS key in the repo-root `.env`:

```
RLS_KEY=<your studio api key>
```

And know your four IDs (find them in an existing importer or the Studio URL):

```python
API   = "https://api.studio.mercor.com"
CAMP  = "camp_…"   # X-Campaign-Id
COMP  = "comp_…"   # X-Company-Id
ACCT  = "acct_…"   # X-Account-Id
WORLD = "world_…"  # target world (must already exist)
```

### Headers (every call)

```python
K  = next(l.split("=",1)[1].strip() for l in open(".env") if l.startswith("RLS_KEY="))
H  = {"Authorization": f"Bearer {K}", "X-Campaign-Id": CAMP,
      "X-Company-Id": COMP, "X-Account-Id": ACCT, "User-Agent": "curl/8.7.1"}
HJ = {**H, "Content-Type": "application/json"}   # for JSON bodies
```

Multipart file uploads use `H` (no Content-Type — requests sets the boundary).

---

## Step 0 — Get the tasks on disk

**From GitHub** (only tracked files, no `.git`, no local cruft):

```bash
git clone --depth 1 https://github.com/ORG/REPO.git /tmp/src
# or, from an existing clone, export one branch's tree cleanly:
git -C /path/to/clone archive origin/main | tar -x -C /tmp/src
```

**Locally**: just point at the directory. Either way you end up with:

```
<TASKS_DIR>/<task-name>/{instruction.md,task.toml,environment/,solution/,tests/}
```

Scrub process artifacts before uploading (they should never ship):

```bash
find <TASKS_DIR> \( -name "*.orig" -o -name "*.refactored" -o -name "*.bak" \
  -o -name "oracle_fix_report.json" -o -name ".oraclefix.done" \
  -o -name "__pycache__" -o -name ".DS_Store" \) -exec rm -rf {} + 2>/dev/null
```

---

## Step 1 — Import task records (bulk)

One POST creates many records and returns `task_id` per `task_name`. Cache the
name→id map to a JSON file — every later step needs it, and it makes the whole
thing resumable.

```python
import json, urllib.request

names = sorted(os.listdir(TASKS_DIR))          # your task list
body  = {"tasks": [{"task_name": n, "notes": "reflection-eval-2026-07-08",
                    "custom_fields": {}} for n in names]}
req = urllib.request.Request(f"{API}/worlds/{WORLD}/import-tasks",
        data=json.dumps(body).encode(), method="POST", headers=HJ)
resp = json.loads(urllib.request.urlopen(req, timeout=300).read())
idmap = {r["task_name"]: r["task_id"] for r in resp["results"]}
json.dump(idmap, open("rls_taskids.json", "w"))
```

- Chunk large imports (≤2000/call) and **sleep ~13s between chunks** — there's a
  ~5-requests/min limiter on this endpoint.
- Retry `429` with backoff.
- `custom_fields` can be `{}` here; we populate values in Step 3.

---

## Step 2 — Upload each task's file tree (snapshot)

Per task: POST every file as multipart, each named `filesystem/<relative path>`.
**The `filesystem/` prefix is required** — that's what tells RLS where the file
goes in the task's container.

```python
import requests

def upload_one(name, tid):
    tdir  = f"{TASKS_DIR}/{name}"
    files = []
    for dp, _, fns in os.walk(tdir):
        for fn in fns:
            full = os.path.join(dp, fn)
            rel  = os.path.relpath(full, tdir)          # e.g. environment/Dockerfile
            files.append(("files", (f"filesystem/{rel}", open(full, "rb"),
                                    "application/octet-stream")))
    r = requests.post(f"{API}/snapshots/task/{tid}/update",
                      headers=H, files=files, timeout=240)
    return r.status_code == 201        # 201 = success
```

Run it **concurrently** (a thread pool of ~12) — it's I/O bound. Record each
success to a `done` file so a re-run skips it (resumable).

```python
import concurrent.futures as cf
done = set(open("uploaded.txt").read().split()) if os.path.exists("uploaded.txt") else set()
todo = [(n, t) for n, t in idmap.items() if n not in done]
with cf.ThreadPoolExecutor(12) as ex:
    futs = {ex.submit(upload_one, n, t): n for n, t in todo}
    for f in cf.as_completed(futs):
        if f.result(): open("uploaded.txt", "a").write(futs[f] + "\n")
```

Throughput ≈ 1,200/min at 12 workers. Retry `429` with backoff.

> **Gotcha:** a single call with **>1000 files** gets rejected. Tasks with huge
> fixture sets must be chunked across multiple `snapshots/.../update` calls.

---

## Step 3 — Label custom fields (makes the dashboard work)

Records exist but their dashboard columns are empty until you set custom-field
**values**. Use `bulk-update`; the column key format is
`custom_fields->>'<field_id>'` (this exact quoting matters).

First, know the world's field IDs (`GET /worlds/{WORLD}` → `task_schema.fields`).
For this world:

| field_id | type | source |
|---|---|---|
| `field_avg_at_8` | number | task.toml `avg_at_8` |
| `field_difficulty` | text | derived: `≤0.25 Hard / ≤0.5 Medium / else Easy` |
| `field_category` | text | task.toml `category` |
| `field_subcategory` | text | task.toml `subcategory` |
| `field_expert_hours` | number | task.toml `expert_time_estimate_hours` |
| `field_reflection_batch` | text | e.g. `batch_1` |

```python
import tomllib   # needs py3.11+ (use the modalenv python if system py is older)

def rows_for(idmap):
    for name, tid in idmap.items():
        m = tomllib.load(open(f"{TASKS_DIR}/{name}/task.toml", "rb"))["metadata"]
        a = float(m.get("avg_at_8", 0.0))
        diff = "Hard" if a <= 0.25 else ("Medium" if a <= 0.5 else "Easy")
        ups = [("field_avg_at_8", a), ("field_difficulty", diff),
               ("field_category", m.get("category")), ("field_subcategory", m.get("subcategory")),
               ("field_expert_hours", m.get("expert_time_estimate_hours")),
               ("field_reflection_batch", "batch_1")]
        yield {"task_id": tid,
               "updates": [{"column_key": f"custom_fields->>'{k}'", "value": v}
                           for k, v in ups if v not in (None, "")]}

rows = list(rows_for(idmap))
for i in range(0, len(rows), 500):                     # 500/batch
    r = requests.post(f"{API}/tasks/bulk-update", headers=HJ,
                      json={"updates": rows[i:i+500]}, timeout=300)
    assert all(x["success"] for x in r.json()["results"])
```

---

## Step 4 — Tag (optional but recommended)

Groups the batch and drives tag-filtered views. The tag must be registered on the
campaign first (`PATCH /campaigns/{id}` → `campaign_settings.campaign_tags_config`);
then:

```python
ids = list(idmap.values())
for i in range(0, len(ids), 800):                      # 800/batch
    requests.post(f"{API}/tasks/bulk-tag", headers=HJ,
                  json={"task_ids": ids[i:i+800], "tag_ids": ["tag_…"]}, timeout=180)
```

---

## Step 5 — Verify (trust COUNT, not lists)

Use the querier. `custom_fields->>'field'` reads a value back.

```python
def q(sql):
    return requests.post(f"{API}/querier/unstructured", headers=HJ,
                         json={"query": sql}, timeout=120).json()["rows"]

q(f"SELECT COUNT(*) AS n FROM tasks WHERE world_id='{WORLD}'")
q(f"SELECT custom_fields->>'field_difficulty' AS diff, COUNT(*) AS n "
  f"FROM tasks WHERE world_id='{WORLD}' AND custom_fields->>'field_reflection_batch'='batch_1' "
  f"GROUP BY 1")
```

> **Gotcha:** the querier **truncates** list / `GROUP BY` results, so a raw
> `SELECT ...` can silently drop rows and look like data is missing. Only trust a
> server-side `COUNT(*)` (wrap grouped queries:
> `SELECT COUNT(*) FROM (… GROUP BY … HAVING …)`).

---

## Running the reference scripts

```bash
# records first (fast, rate-limited), then file trees (concurrent, resumable)
python studio-autoqc/rls_import_b1.py --import
python studio-autoqc/rls_import_b1.py --upload --workers 12
# then label + tag with the snippets above (or fold them into the script)
```

Both `--import` and `--upload` are idempotent/resumable via their state files
(`rls_taskids_b1.json`, `rls_uploaded_b1.txt`), so a crash mid-run just re-runs.

---

## Cheatsheet — the four endpoints

| Purpose | Method + path | Batch | Notes |
|---|---|---|---|
| Create records | `POST /worlds/{world}/import-tasks` | ≤2000 | ~5/min; returns name→id |
| Upload files | `POST /snapshots/task/{tid}/update` | 1 task | multipart `filesystem/<rel>`; ≤1000 files; 201=ok |
| Set field values | `POST /tasks/bulk-update` | 500 | key `custom_fields->>'field_id'` |
| Apply tag | `POST /tasks/bulk-tag` | 800 | tag pre-registered on campaign |
| Read back | `POST /querier/unstructured` | — | trust COUNT, not lists |

## Common failure modes
- **Missing `filesystem/` prefix** → files land in the wrong place / task won't build.
- **Wrong column_key quoting** → update returns success but field stays empty. Must be `custom_fields->>'field_id'`.
- **`ResourceExhausted` on eval** if you then fire thousands of rollouts at once → throttle in waves.
- **`task_<hex>` vs kebab names** are just names; RLS doesn't care, but keep your name→id map authoritative.

## Creating the world (prerequisite, not in the scripts)
`POST /worlds/` with `campaign_id` in the **body** (required alongside `world_name`):
```json
{"world_name": "…", "world_description": "…", "campaign_id": "camp_…"}
```
Returns the world object with a new `world_id`. Verify with `GET /worlds/?campaign_id=…`.
