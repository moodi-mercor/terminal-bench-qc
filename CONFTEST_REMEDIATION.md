# Conftest-Plant Remediation (systemic, corpus-wide)

**Date:** 2026-06-29 · **Severity:** P0 reward-hack · **Scope:** 9,021 tasks (~67% of the 13,433 corpus)

## The defect
~9,021 OTS tasks share an **identical** pytest grader line in `tests/test.sh`:

```
python3 -m pytest /app/test_outputs.py --no-header -p no:cacheprovider -vs 2>&1
```

It runs pytest **from a directory the root agent controls (`/app`)** with **no `--noconftest` guard**.
A cheating agent plants `/app/conftest.py` with a skip-all hook:

```python
import pytest
def pytest_collection_modifyitems(config, items):
    for item in items:
        item.add_marker(pytest.mark.skip(reason="hacked"))
```

→ every test is *skipped* → pytest exits 0 → `test.sh` writes `reward.txt = 1`. The agent passes **without solving the task.**

## The fix (one line, uniform)
Insert `--noconftest` into the grader line:

```
python3 -m pytest --noconftest /app/test_outputs.py --no-header -p no:cacheprovider -vs 2>&1
```

`--noconftest` makes pytest ignore any `conftest.py`, so the planted file can't intercept collection.

**Validated** (`/tmp` repro): with a planted skip-all conftest, a *wrong* solution → all skipped → exit 0 → reward 1 (cheat).
With `--noconftest` → planted file ignored → real test runs → FAILED → reward 0 (correct). One flag closes it.

## Application — two routes

**1. TEMPLATE fix (recommended, fixes the root cause).**
The vulnerable line comes from a shared OTS `test.sh` generator template. Adding `--noconftest` to that template
**immunizes every future task with one change.** Hand this to the task-pipeline owners.

**2. Existing-corpus patch (mechanical).**
Patched `test.sh` for all 9,021 vulnerable tasks are generated at `_local/conftest_fix/<task>/test.sh`
(only the grader line changed; syntactically validated). Apply options:
- Pipeline re-generates/patches existing tasks with the template fix (preferred), **or**
- Controlled batched upload via `POST /snapshots/task/{id}/update` (file `tests/test.sh`) — **only with explicit
  sign-off + original backed up first**, batched & verified. Do NOT blind-bulk-write 9,021 production snapshots.

## Detection / tooling
- Detector: `skills/static-semantic-qc/scripts/check_reward_hack.py` → `conftest-plant-vulnerable` (clears once `--noconftest` added).
- AutoQC static module (ss_04) carries the pattern for new/edited tasks.
- Vulnerable set: `_local/conftest_vuln_tasks.json` (9,021). Patches: `_local/conftest_fix/`. Fixer: `studio-autoqc/fix_conftest.py`.

## Note on scale
Earlier estimate was ~735; the accurate corpus-wide figure is **9,021** — nearly every pytest-graded task. This makes
the template route decisively the right one: one template line vs 9,021 individual edits.
