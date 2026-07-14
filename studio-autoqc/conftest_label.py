#!/usr/bin/env python3
"""Label the conftest-plant-vulnerable tasks in RLS — DEDICATED merge-safe flag.

Writes qc_conftest_* keys onto each vulnerable task's custom_fields WITHOUT touching
qc_status/qc_priority/qc_bucket (those already hold other QC verdicts on many of these
tasks — overwriting would clobber more-severe labels). Idempotent + resumable (skips
tasks already flagged). Default DRY-RUN; --apply to write.

Issue: tests/test.sh runs `pytest` without --noconftest from agent-controlled /app, so a
planted /app/conftest.py (skip-all) makes the verifier pass without solving. Fix: --noconftest.
"""
import argparse
import json
import os
import sys
import time
import requests

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
API = "https://api.studio.mercor.com"
FLAG = {"qc_conftest_vulnerable": "true",
        "qc_conftest_issue": "pytest verifier runs without --noconftest; agent can plant /app/conftest.py to skip all tests -> reward=1 without solving",
        "qc_conftest_fix": "add --noconftest to the pytest grader line",
        "qc_conftest_run": "conftest-audit-2026-06-29"}


def envkey(n):
    if os.environ.get(n):
        return os.environ[n]
    for l in open(f"{ROOT}/.env"):
        if l.startswith(n + "="):
            return l.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit(f"no {n}")


H = {"Authorization": f"Bearer {envkey('RLS_WRITE_KEY')}",
     "X-Campaign-Id": "camp_4e196b1414a1499db54b43233104b0a7",
     "X-Company-Id": "comp_2fa4115109d741cd94a3c409ed89e61f",
     "Content-Type": "application/json"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default=f"{ROOT}/_local/conftest_name2id.json")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=0.1)
    a = ap.parse_args()
    n2i = json.load(open(a.map))
    items = list(n2i.items())
    if a.limit:
        items = items[:a.limit]
    print(f"{len(items)} conftest-vulnerable tasks | mode: {'APPLY' if a.apply else 'DRY-RUN'}")
    applied = skipped = failed = 0
    for i, (name, tid) in enumerate(items, 1):
        try:
            cf = requests.get(f"{API}/tasks/{tid}", headers=H, timeout=60).json().get("custom_fields") or {}
        except Exception as e:
            failed += 1; print(f"  [GET fail] {name}: {e}"); continue
        if cf.get("qc_conftest_vulnerable") == "true":
            skipped += 1; continue
        if not a.apply:
            applied += 1
            if applied <= 3:
                print(f"  would flag {name} -> {tid}")
            continue
        merged = {**cf, **FLAG}
        try:
            r = requests.patch(f"{API}/tasks/{tid}", headers=H, data=json.dumps({"custom_fields": merged}), timeout=60)
            if r.status_code == 200:
                applied += 1
            else:
                failed += 1; print(f"  [PATCH {r.status_code}] {name}: {r.text[:100]}")
        except Exception as e:
            failed += 1; print(f"  [PATCH fail] {name}: {e}")
        time.sleep(a.delay)
        if i % 250 == 0:
            print(f"  ... {i}/{len(items)} (applied {applied}, skipped {skipped}, failed {failed})", flush=True)
    verb = "flagged" if a.apply else "would flag"
    print(f"done: {verb} {applied}, skipped(already) {skipped}, failed {failed}")


if __name__ == "__main__":
    main()
