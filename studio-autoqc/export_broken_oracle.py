#!/usr/bin/env python3
"""Export the broken-oracle bucket from RL Studio into a Codex-ready work tree.

A "broken oracle" = a task whose reference solution (solution/solve.sh) does NOT
pass its own tests (behavioral golden_score < 1). Studio labels these
`custom_fields.qc_final_bucket == 'broken-oracle'` (2,509 tasks as of 2026-07-07,
the `view_qc_broken_oracle` annotator view).

This script:
  1. lists the world, filters to the broken-oracle bucket (latest, not archived),
  2. downloads each task's `filesystem/` snapshot into <out>/tasks/<task_name>/
     as a standard TB2 tree (task.toml, environment/, tests/, solution/),
  3. writes <out>/manifest.csv (name, id, difficulty, category, language, link),
  4. drops AGENTS.md + PLAYBOOK.md + fix_report.template.json into <out>/ so a
     Codex session started in <out> knows exactly what to do.

Resumable (skips tasks already fully pulled) and shardable (--limit/--offset), so
you can hand Codex one batch at a time instead of all 2,509 at once.

Usage:
  python export_broken_oracle.py --limit 20                 # pilot batch of 20
  python export_broken_oracle.py --limit 200 --offset 200   # next shard
  python export_broken_oracle.py --all --workers 8          # everything (slow)
  python export_broken_oracle.py --list-only                # just count + manifest
"""
import argparse
import csv
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "skills", "static-semantic-qc", "scripts")]
import studio_pull as sp  # noqa: E402

WORLD = sp.WORLD
BUCKET = "broken-oracle"
VIEW_URL = ("https://studio.mercor.com/annotator/views/view_qc_broken_oracle/"
            f"?world_id={WORLD}")
DEFAULT_OUT = os.path.normpath(os.path.join(HERE, "..", "_local", "broken_oracle_export"))

# Marker files that indicate a task tree pulled successfully (so --resume can skip it).
CORE_FILES = ["task.toml", "tests/test.sh", "solution/solve.sh"]


def is_broken_oracle(t):
    cf = t.get("custom_fields") or {}
    return (t.get("archived_at") is None
            and cf.get("qc_final_bucket") == BUCKET)


def already_pulled(task_dir):
    return os.path.isfile(os.path.join(task_dir, "task.toml"))


def pull_one(key, task, tasks_root):
    name = task.get("task_name") or task["task_id"]
    task_dir = os.path.join(tasks_root, name)
    if already_pulled(task_dir):
        return name, "skip", 0
    try:
        n = sp.pull_task(key, task, tasks_root)
        return name, ("ok" if n else "empty"), n
    except Exception as e:  # noqa: BLE001 - one bad task shouldn't kill the run
        return name, f"error: {e}", 0


def write_manifest(rows, out):
    cols = ["task_name", "task_id", "difficulty", "category", "subcategory",
            "language", "operation_type", "harness", "qc_remediation",
            "expert_time_estimate_min", "view_url"]
    with open(os.path.join(out, "manifest.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--limit", type=int, default=None, help="max tasks to pull this run")
    ap.add_argument("--offset", type=int, default=0, help="skip the first N (for sharding)")
    ap.add_argument("--all", action="store_true", help="pull the entire bucket")
    ap.add_argument("--workers", type=int, default=6, help="parallel task downloads")
    ap.add_argument("--list-only", action="store_true",
                    help="write manifest + docs, download nothing")
    ap.add_argument("--refresh", action="store_true", help="re-fetch the world list")
    args = ap.parse_args()

    key = sp.load_key()
    tasks = sp.list_tasks(key, WORLD, refresh=args.refresh)
    broken = sorted((t for t in tasks if is_broken_oracle(t)),
                    key=lambda t: t.get("task_name") or "")
    print(f"broken-oracle bucket: {len(broken)} tasks (world {WORLD})", flush=True)

    os.makedirs(args.out, exist_ok=True)
    tasks_root = os.path.join(args.out, "tasks")
    os.makedirs(tasks_root, exist_ok=True)

    # Manifest covers the FULL bucket regardless of what this run downloads.
    rows = []
    for t in broken:
        cf = t.get("custom_fields") or {}
        rows.append({
            "task_name": t.get("task_name"), "task_id": t["task_id"],
            "difficulty": cf.get("difficulty"), "category": cf.get("category"),
            "subcategory": cf.get("subcategory"), "language": cf.get("language"),
            "operation_type": cf.get("operation_type"), "harness": cf.get("harness"),
            "qc_remediation": cf.get("qc_remediation"),
            "expert_time_estimate_min": cf.get("expert_time_estimate_min"),
            "view_url": VIEW_URL,
        })
    write_manifest(rows, args.out)
    write_docs(args.out)
    print(f"wrote manifest.csv + AGENTS.md + PLAYBOOK.md -> {args.out}/", flush=True)

    if args.list_only:
        return

    subset = broken[args.offset:]
    if not args.all:
        subset = subset[: (args.limit if args.limit is not None else 20)]
    print(f"downloading {len(subset)} task(s) with {args.workers} workers "
          f"(offset {args.offset}) -> {tasks_root}/", flush=True)

    done = skipped = errs = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(pull_one, key, t, tasks_root): t for t in subset}
        for i, fut in enumerate(as_completed(futs), 1):
            name, status, n = fut.result()
            if status == "ok":
                done += 1
            elif status == "skip":
                skipped += 1
            else:
                errs += 1
                print(f"  ! {name}: {status}", flush=True)
            if i % 25 == 0 or i == len(subset):
                print(f"  [{i}/{len(subset)}] ok={done} skip={skipped} err={errs}", flush=True)

    print(f"\nDone. downloaded={done} already-present={skipped} errors={errs}")
    print(f"Task trees:   {tasks_root}/<task_name>/")
    print(f"Start Codex in {args.out}/ and point it at PLAYBOOK.md")


# --------------------------------------------------------------------- docs ---
def write_docs(out):
    agents = os.path.join(out, "AGENTS.md")
    playbook = os.path.join(out, "PLAYBOOK.md")
    tmpl = os.path.join(out, "fix_report.template.json")
    with open(agents, "w") as f:
        f.write(AGENTS_MD)
    with open(playbook, "w") as f:
        f.write(PLAYBOOK_MD)
    with open(tmpl, "w") as f:
        f.write(FIX_REPORT_TEMPLATE)


AGENTS_MD = """\
# AGENTS.md — broken-oracle repair (read PLAYBOOK.md first)

You are fixing **broken-oracle** Terminal-Bench tasks. A broken oracle is a task
whose reference solution `solution/solve.sh` does **not** pass its own
`tests/` — the "correct" answer fails the grader, so the task is currently
unsolvable and must be repaired (or, rarely, marked for culling).

## Where things are
- `tasks/<task_name>/` — one standard TB2 task tree per task:
  - `task.toml` — metadata + timeouts (do not casually change limits)
  - `instruction.md` — what the agent is asked to do (the source of truth for intent)
  - `environment/Dockerfile` — build/runtime env
  - `tests/test.sh` (+ `tests/test_outputs.py`) — the verifier
  - `solution/solve.sh` — the reference "oracle" solution (the thing that must pass)
- `manifest.csv` — the full bucket (name, id, difficulty, category, Studio link)
- `fix_report.template.json` — copy to `tasks/<task_name>/fix_report.json` per task

## The one rule that matters
Make the **reference solution pass the test _honestly_**. The test must still
require the real work — never delete/weaken an assertion just to force a green.
See PLAYBOOK.md §3 for the decision tree (fix solve.sh vs. fix test vs. fix env vs. cull).

## Loop per task
1. Read instruction.md + tests + solve.sh + Dockerfile.
2. Reproduce the failure (PLAYBOOK.md §2 — build, run solve.sh, run verifier).
3. Diagnose which layer is broken and apply the minimal correct fix.
4. Re-run until the oracle scores 1 and the untouched container still scores 0.
5. Write `tasks/<task_name>/fix_report.json`.

Work one task fully before starting the next. Do not edit files outside
`tasks/<task_name>/`.
"""

PLAYBOOK_MD = r"""# PLAYBOOK — repairing a broken oracle

## 0. What you're fixing
`solution/solve.sh` is the reference solution. It should score **1.0** on the
task's own verifier (`tests/test.sh`). For every task in `tasks/`, it currently
scores < 1.0. Your job: find why and fix it so the reference passes *for the
right reason* — the test must still fail an empty/no-op container.

## 1. Read before you touch
For `tasks/<name>/`:
- `instruction.md` — the intended task. This is the arbiter of "correct". If the
  test asserts something the instruction never asked for, the *test* is suspect.
- `tests/test.sh` and `tests/test_outputs.py` — how the grader decides pass/fail.
- `solution/solve.sh` — the reference. Note what artifacts/commands it produces.
- `environment/Dockerfile` — base image, installed tools, working dir.

## 2. Reproduce the failure (verification harness)
Verification runs the task in Docker. Two options:

### A. Local harness (preferred — this repo already has it)
From the terminal-bench-qc repo root (two levels up from this export dir):
```bash
python skills/behavioral-qc/scripts/check_behavioral.py \
    _local/broken_oracle_export/tasks --only <task_name> --execute --yes
```
- The **oracle** trial builds the image, runs `solution/solve.sh`, then the
  verifier. Expected **PASS (score 1)**. If it FAILs -> that's the break to fix.
- The **no-op** trial runs the verifier on the untouched container. Expected
  **FAIL (score 0)**. After your fix this must still FAIL (else you weakened the test).
- Drop `--execute` for a dry run that just prints the docker commands.
- Requires Docker (start colima: `colima start`). On Apple Silicon, amd64-only
  tasks may need `--native-arch` or are only conclusively testable on amd64 — if a
  build is inconclusive here, note it in the fix_report and rely on the Studio re-run.

### B. Manual (when you want to poke inside)
```bash
cd tasks/<task_name>
docker build -t bo/<task_name> environment/
# oracle: run solve.sh then the verifier, mounting tests + solution at runtime
docker run --rm -v "$PWD/tests:/tests:ro" -v "$PWD/solution:/solution:ro" \
    bo/<task_name> bash -c "bash /solution/solve.sh; bash /tests/test.sh; echo EXIT=$?"
```
Read the assertion output to see exactly which check fails and why.

## 3. Diagnose + fix (decision tree)
Once you can see the failing assertion, classify the root cause:

- **Reference solution is wrong/incomplete** (most common): solve.sh produces the
  wrong output, writes to the wrong path, misses a step, or uses a tool that isn't
  installed. **Fix `solution/solve.sh`** so it fully satisfies the instruction.
- **Test is wrong / too strict / drifted**: the test asserts something the
  instruction never required, hard-codes a brittle value (timestamp, ordering,
  absolute path), or is nondeterministic. Fix `tests/` — but ONLY to match the
  instruction, and NEVER by simply removing the assertion. If you loosen a check,
  loosen it to exactly what the instruction requires, no more.
- **Environment defect**: a package/binary the solution needs isn't installed, or
  the base image drifted. **Fix `environment/Dockerfile`** (add the dep / pin it).
- **Genuinely unfixable** (instruction is self-contradictory, or the task depends
  on data that no longer exists): don't force it. Set `"outcome": "cull"` in the
  fix_report with a one-line reason. Do not fabricate a solution.

Prefer the **smallest** change that makes the oracle pass honestly. If both the
solution and a test are wrong, fix both, but keep each change minimal.

## 4. Verify the fix
Re-run the harness (§2A). A fix is DONE only when:
- oracle trial => **PASS (score 1)**, and
- no-op trial => **FAIL (score 0)** (the test still requires the work).
If the no-op now passes, you weakened the test too far — revert and re-scope.

## 5. Record the outcome
Copy `fix_report.template.json` to `tasks/<name>/fix_report.json` and fill it:
- `outcome`: `fixed` | `cull` | `inconclusive`
- `root_cause`: `solution` | `test` | `environment` | `mixed` | `unfixable`
- `files_changed`: list of edited paths (relative to the task dir)
- `summary`: 1-3 sentences on what was wrong and what you changed
- `oracle_score_after`, `noop_score_after`: from the harness (or "not-run" if
  Docker was inconclusive on this arch)

## 6. Guardrails (do not violate)
- Never make a test pass by deleting/gutting assertions. The empty container must
  still fail.
- Don't relax `task.toml` timeouts to "fix" a slow verifier unless the instruction
  clearly implies a longer budget; note it if you do.
- Don't edit anything outside `tasks/<task_name>/`.
- Don't touch other tasks' files. One task at a time; write its fix_report before moving on.
"""

FIX_REPORT_TEMPLATE = """\
{
  "task_name": "",
  "outcome": "fixed | cull | inconclusive",
  "root_cause": "solution | test | environment | mixed | unfixable",
  "files_changed": [],
  "summary": "",
  "oracle_score_after": "1 | 0 | not-run",
  "noop_score_after": "0 | 1 | not-run"
}
"""


if __name__ == "__main__":
    main()
