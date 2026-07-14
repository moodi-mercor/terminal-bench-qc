#!/usr/bin/env python3
"""Flag the final tb2400 delivery tasks in RLS + save QC metadata via merge-safe PATCH.

Writes custom_fields on each of the 2,400 delivered tasks:
  qc_tb2400            = delivery-candidate
  qc_tb2400_source     = airtable_pass | flash_gapfill | flash_backfill
  qc_difficulty_source = flash | opus_gpt5_proxy
  qc_flash_passes      = 0..4        (only when flash-measured)
  qc_flash_runs        = int         (only when flash-measured)
  qc_oracle            = OK
  qc_leak_probe        = clean | n/a
  qc_verdict           = READY

Dry-run default; --apply to PATCH. Resumable + concurrent.
"""
import json, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

API = "https://api.studio.mercor.com"; KEY = "rls-sk-iuA_SCYWiui2xXr_HD7Rab0BfF5VPmDNlK6eAyLzcYw"
CAMP = "camp_0c1f9a9809604271a534edd77c3cbec1"; COMP = "comp_2fa4115109d741cd94a3c409ed89e61f"
ACCT = "acct_85b680d4c5ba49a29f19c173672aebea"
ROOT = "/Users/mahmoodmapara/Desktop/terminal-bench-qc"; G = f"{ROOT}/_local/tb2400"
STATE = f"{G}/label_tb2400_state.txt"; WORKERS = 10
H = {"Authorization": f"Bearer {KEY}", "X-Campaign-Id": CAMP, "X-Company-Id": COMP,
     "X-Account-Id": ACCT, "User-Agent": "curl/8.7.1", "Content-Type": "application/json"}
HG = {k: v for k, v in H.items() if k != "Content-Type"}


def fields(v):
    f = {"qc_tb2400": "delivery-candidate",
         "qc_tb2400_source": v.get("source", ""),
         "qc_difficulty_source": v.get("difficulty_source", ""),
         "qc_oracle": "OK",
         "qc_leak_probe": v.get("leak_probe", "n/a") or "n/a",
         "qc_verdict": "READY"}
    fp, fr = v.get("flash_passes"), v.get("flash_runs")
    if str(fp) not in ("", "None") and str(fr) not in ("", "None"):
        f["qc_flash_passes"] = int(fp); f["qc_flash_runs"] = int(fr)
    return f


def one(tid, v):
    try:
        cur = requests.get(f"{API}/tasks/{tid}", headers=HG, timeout=60).json()
        cf = cur.get("custom_fields") or {}
        merged = {**cf, **fields(v)}
        for i in range(5):
            r = requests.patch(f"{API}/tasks/{tid}", headers=H,
                               data=json.dumps({"custom_fields": merged}), timeout=60)
            if r.status_code in (200, 201):
                return True
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(3 * (i + 1)); continue
            return False
    except Exception:
        return False
    return False


def main():
    apply = "--apply" in sys.argv
    final = json.load(open(f"{G}/final_2400.json"))
    done = set(open(STATE).read().split()) if os.path.exists(STATE) else set()
    todo = {t: v for t, v in final.items() if t not in done}
    print(f"{'APPLY' if apply else 'DRY-RUN'}: label {len(todo)} tasks ({len(done)} done)")
    print("fields:", list(fields(next(iter(final.values()))).keys()))
    if not apply:
        print("re-run with --apply"); return
    sf = open(STATE, "a"); ok = 0
    with ThreadPoolExecutor(WORKERS) as ex:
        futs = {ex.submit(one, t, v): t for t, v in todo.items()}
        for n, fut in enumerate(as_completed(futs), 1):
            t = futs[fut]
            if fut.result():
                sf.write(t + "\n"); sf.flush(); ok += 1
            if n % 200 == 0 or not fut.result():
                print(f"  [{n}/{len(todo)}] ok={ok}", flush=True)
    print(f"done. labeled {ok}/{len(todo)}")


if __name__ == "__main__":
    main()
