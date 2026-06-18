# Golden eval set

The labeled set used to measure QC **precision / recall** (action item 2). Two parts:

1. **Synthetic fixtures** (`fixtures/`, committed) — tiny TB2 tasks, deterministic,
   no network. One clean **pass** task and one **fail** task per FAIL-class defect.
   They always reproduce the defect, so they test the checks regardless of Studio
   state. Each maps to a real pattern from the client validation reports.
2. **Real OTS examples** (`ots_tasks.txt`, pulled on demand) — actual Studio tasks:
   two currently-defective (`cloud-cost-anomaly-auditor`, `dra-calibration-integrity-pipeline`)
   and five that the reports flagged but Studio has since remediated (truth moved to
   `tests/.truth/` / `/tmp`) — now good examples. Pulling needs an `RLS_KEY`.

Ground truth is `golden_labels.csv` (`task,is_defect,expected_verdict,expected_title,kind,source,notes`).

## Run against the fixtures (offline, deterministic)

```bash
python scripts/run_static_qc.py eval/fixtures --out-dir /tmp/fx_qc
python scripts/score_qc.py /tmp/fx_qc/review-ssot.csv eval/golden_labels.csv
```
Expected: the 6 `fail/*` fixtures score FAIL, `pass/clean-records-etl` scores PASS →
precision = recall = 1.0 on the overlap.

## Run against the real OTS examples (needs the key)

```bash
cp .env.example .env          # then put your RL Studio key in it: RLS_KEY=...
python scripts/studio_pull.py --names @eval/ots_tasks.txt --out tasks_cache
python scripts/run_static_qc.py tasks_cache --out-dir qc_out
python scripts/score_qc.py qc_out/review-ssot.csv eval/golden_labels.csv
```
`score_qc.py` only scores tasks present in both the QC output and the labels, so it
ignores tasks you didn't pull.

## Fixture → report-pattern map

| Fixture | Defect | Report pattern |
|---|---|---|
| `fail/truth-baked` | answer baked to an agent-readable path the verifier reads | first/second-5k "truth at agent-readable paths" (290 + 49) |
| `fail/reference-reads-truth` | `solve.sh` reads the verifier's expected file | second-5k Pattern 4 (14 tasks) |
| `fail/unconditional-reward` | reward written without a success check; verifier `\|\| true` | MAI no-op / vacuous tests |
| `fail/copies-solution` | Dockerfile copies `solution/` into the image | anti-cheat / leakage |
| `fail/missing-solution` | required file absent | structure |
| `fail/bad-metadata` | missing timeout, seconds-as-minutes, generic tags | metadata |
| `pass/clean-records-etl` | none | the shape a clean task should have |

> Behavioral defects (oracle≠1, OOM, timeout) aren't in this static eval — they're
> confirmed by the delivery-stage run, not by reading files.
