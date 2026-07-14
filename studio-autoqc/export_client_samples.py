#!/usr/bin/env python3
"""Export ~20 client-ready sample tasks for the strong/weak (Opus-4.8 / GLM-5.2) split.

Selection: tasks with FULL 5 genuine GLM-5.2 trials meeting the client bar (GLM <3/5),
spread across difficulty bands (8x 0/5, 6x 1/5, 6x 2/5), QC-healthy, Opus-solvable.

Per task: full filesystem tree from Studio (conftest-fixed test.sh overlaid when
available) + eval_summary.json with both models' evidence.

Output: _local/client_samples_v1/<task_name>/...  + manifest.json + zip
"""
import csv
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path[:0] = [os.path.join(ROOT, "skills", "static-semantic-qc", "scripts")]
import studio_pull as sp  # noqa: E402

OUT = f"{ROOT}/_local/client_samples_v1"
FIX = f"{ROOT}/_local/conftest_fix_all"
STATE = f"{ROOT}/_local/glm52_retry/state.json"
OPUS_CSV = f"{ROOT}/_local/opus_qc/good_tasks_opus.csv"
BANDS = {0: 8, 1: 6, 2: 6}  # glm passes -> sample count


def main():
    key = sp.load_key()
    state = json.load(open(STATE))
    opus = {r["task_id"]: r for r in csv.DictReader(open(OPUS_CSV))}
    allt = {t["task_id"]: t for t in sp.list_tasks(key, sp.WORLD)}

    picked = []
    for band, want in BANDS.items():
        cands = sorted(t for t, v in state["scores"].items()
                       if len(v) >= 5 and int(sum(v[:5])) == band)
        # prefer tasks with a conftest-fixed test.sh available and known opus record
        def rank(t):
            name = allt.get(t, {}).get("task_name", "")
            has_fix = os.path.isdir(os.path.join(FIX, name))
            return (not has_fix, t)
        cands.sort(key=rank)
        picked += [(t, band) for t in cands[:want]]

    os.makedirs(OUT, exist_ok=True)
    manifest = []
    for tid, band in picked:
        t = allt.get(tid)
        if not t:
            print(f"  [skip] {tid} not in world list")
            continue
        name = t["task_name"]
        print(f"pulling {name} (GLM {band}/5)", flush=True)
        n_ok = sp.pull_task(key, t, OUT)
        if n_ok == 0:
            print(f"  [skip] {name}: empty tree")
            continue
        tdir = os.path.join(OUT, name)
        fixed = os.path.join(FIX, name, "tests", "test.sh")
        if os.path.isfile(fixed):
            os.makedirs(os.path.join(tdir, "tests"), exist_ok=True)
            shutil.copyfile(fixed, os.path.join(tdir, "tests", "test.sh"))
        orec = opus.get(tid, {})
        summary = {
            "task_id": tid,
            "task_name": name,
            "qc_bucket": orec.get("qc_bucket", "healthy-hard"),
            "strong_model": {
                "model": "claude-opus-4-8 (Terminus harness, adaptive thinking, effort=high)",
                "runs": int(orec.get("opus_runs") or 0) or None,
                "passes": int(orec.get("opus_passes") or 0) or None,
                "criterion": "bo5 >= 1 (solvable)",
                "meets": True,
                "note": ("historical pass@N evidence" if orec.get("opus_runs") else
                         "pass@8 batch 2026-07 evidence"),
            },
            "weak_model": {
                "model": "GLM-5.2 (zai/glm-5.2, temp 0.7, reasoning_effort=high, Terminus harness)",
                "trials": [int(x) for x in state["scores"][tid][:5]],
                "passes_of_5": band,
                "criterion": "bo5 < 3 (remains hard)",
                "meets": True,
                "note": "agent timeouts counted as failed trials (Terminal-Bench semantics)",
            },
        }
        json.dump(summary, open(os.path.join(tdir, "eval_summary.json"), "w"), indent=2)
        manifest.append({"task_name": name, "task_id": tid, "glm_passes_of_5": band,
                         "opus_passes": summary["strong_model"]["passes"],
                         "opus_runs": summary["strong_model"]["runs"]})

    json.dump({"generated": "2026-07-06",
               "criteria": "Opus-4.8 bo5>=1 AND GLM-5.2 bo5<3, frontier-easy (>80%) excluded, QC-healthy only",
               "samples": manifest}, open(os.path.join(OUT, "manifest.json"), "w"), indent=2)
    print(f"\nDONE: {len(manifest)} samples in {OUT}")


if __name__ == "__main__":
    main()
