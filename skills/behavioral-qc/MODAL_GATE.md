# Behavioral gate on Modal — how to run it

The local behavioral gate (`check_behavioral.py`) builds each task in Docker on your
machine. That's fine for a handful of tasks but too slow (and arch-wrong on Apple
Silicon) for a whole delivery. **Modal** runs the same oracle / no-op gate on native
amd64 with hundreds of containers in parallel — a 1,000-task corpus gates in ~20–40 min.

Two scripts (in `scripts/`):

- **`modal_gate.py`** — pass/fail gate. Per task: build `environment/Dockerfile` as a
  Modal image, attach `tests/` + `solution/`, then run **no-op** (untouched → must FAIL)
  and **oracle** (`solve.sh` then `test.sh` → must PASS). Emits `OK` / `ORACLE-FAIL` /
  `NOOP-PASS` / `BUILD-FAIL`. Resumable.
- **`modal_capture.py`** — dumps the full oracle log per task (for triaging failures),
  ensures pytest/jsonschema are present, injects `[verifier.env]`, and with `--old`
  runs each task's **original** (`origin/main`) tests to tell a refactor regression
  apart from a pre-existing defect.

## 1. One-time setup

```bash
# Modal client + auth to the delivery workspace
python3.12 -m venv _local/modalenv
_local/modalenv/bin/pip install modal
_local/modalenv/bin/modal token new          # browser login
_local/modalenv/bin/modal profile list       # confirm workspace = mercor-data-delivery
```

Point a `TASKS_DIR` at a checkout of the corpus (each `<task>/` has `environment/`,
`tests/`, `solution/`). For `--old` you must run from inside the **git repo** so the
script can `git archive origin/main` the pre-refactor tests.

## 2. Run the oracle / no-op gate over a corpus

```bash
V=_local/modalenv/bin/python
ls TASKS_DIR/tasks > all_tasks.txt            # or any subset, one task-id per line

$V skills/behavioral-qc/scripts/modal_gate.py TASKS_DIR all_tasks.txt \
    --workers 200 \
    --state _local/oracle_done.txt \
    --out   _local/oracle_results.txt
```

- `--state` is the resume file: re-running skips tasks already recorded, so a killed
  run picks up where it left off. **Keep only `OK` lines as "done"** if you want to
  re-gate everything that wasn't a clean pass:
  `grep -P '\tOK\t' oracle_results.txt | cut -f1 > oracle_done.txt`
- `--workers 200` is comfortable; the workspace has plenty of headroom. Drop it if you
  see queueing.
- Results file is TSV: `task_id \t verdict \t detail`. Tally with
  `cut -f2 _local/oracle_results.txt | sort | uniq -c`.

## 3. Triage failures (full logs + regression vs pre-existing)

```bash
# full oracle log per failing task (pytest present, verifier.env injected)
grep -P '\tORACLE-FAIL\t' _local/oracle_results.txt | cut -f1 > fails.txt
$V skills/behavioral-qc/scripts/modal_capture.py TASKS_DIR fails.txt \
    --logdir _local/fail_logs --workers 100
#   -> _local/fail_logs/<task>.log  +  _summary.tsv (OK once deps present, else FAIL)

# is a failure OUR regression or pre-existing? run the ORIGINAL tests on the oracle:
$V skills/behavioral-qc/scripts/modal_capture.py TASKS_DIR fails.txt \
    --logdir _local/oldgate_logs --old --workers 100
#   original FAIL  -> pre-existing defect (not caused by a refactor) -> exclude/flag
#   original OK    -> the change regressed it -> fix it
```

## Gotchas (learned the hard way)

- **Local Docker vs Modal.** Colima only mounts your home dir, so `docker run -v`
  against a `/private/tmp` checkout yields empty mounts (verifier "file not found").
  Modal uploads the context via `add_local_dir`, so it just works — prefer Modal.
- **pytest isn't always baked in.** Some base images lack `pytest`/`jsonschema`/`pyyaml`;
  a bare `modal_gate.py` will read that as `ORACLE-FAIL`. `modal_capture.py` pip-installs
  them first (as real Harbor does) — re-check failures there before believing them.
- **`/logs`.** Harbor mounts a writable `/logs`; the scripts `mkdir -p /logs` (gate) so a
  non-root container's `test.sh` reward write doesn't error. If you adapt the gate, keep
  a writable `/logs`.
- **`[verifier.env]`.** Harbor injects `task.toml`'s `[verifier.env]` vars; `modal_capture.py`
  exports them. If a task fails on a missing `$VAR`, that's a harness gap, not a defect.
- **Verdicts, not scores.** The gate treats any pytest failure / non-zero `test.sh` as
  not-passing. For per-check granularity, read the captured log.
- **Cost.** Sandboxes are `cpu=2, memory=4096`, terminated immediately after each task.
  Watch live containers with `modal app list` (app `r35k-oracle-gate`).

## Feeding the completeness checklist

This gate answers dimensions 8–9 (`oracle-passes`, `noop-fails`) of the QC checklist
([`../../QC_CHECKLIST.md`](../../QC_CHECKLIST.md)). To let `aggregate.py --require-complete`
see that a task's oracle/no-op were actually run, convert the results TSV into
`qc_out/behavioral_signals.json` — a map `{task: {"oracle": 1|0, "noop": 1|0}}`
(1 = oracle passed, 0 = no-op failed, i.e. the good outcomes):

```bash
# OK -> oracle:1,noop:0 ; ORACLE-FAIL -> oracle:0 ; NOOP-PASS -> noop:1
python3 - _local/oracle_results.txt qc_out/behavioral_signals.json <<'PY'
import json, sys
sig = {}
for line in open(sys.argv[1]):
    parts = line.rstrip("\n").split("\t")
    if len(parts) < 2:
        continue
    task, verdict = parts[0], parts[1]
    d = sig.setdefault(task, {})
    if verdict == "OK":
        d["oracle"], d["noop"] = 1, 0
    elif verdict == "ORACLE-FAIL":
        d["oracle"] = 0
    elif verdict == "NOOP-PASS":
        d["noop"] = 1
json.dump(sig, open(sys.argv[2], "w"), indent=2)
print(f"wrote {len(sig)} tasks -> {sys.argv[2]}")
PY
```

A task without an entry here stays `qc-incomplete` under `--require-complete` — the
gate will not let it pass on the reviewer alone.
