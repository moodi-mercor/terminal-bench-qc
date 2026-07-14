#!/usr/bin/env python3
"""Backfill RLS custom-field values on the Reflection delivery_2 world so the
dashboard views (difficulty/category/etc.) actually filter.

Correct column_key format is `custom_fields->>'field_id'` (per tasks router
_parse_column_key). Reads task.toml [metadata] with a light regex parser (no
tomllib, runs on system python3). Idempotent; batches of 500.
"""
import json, os, re, time, requests

API = "https://api.studio.mercor.com"
CAMP = "camp_4e196b1414a1499db54b43233104b0a7"
COMP = "comp_2fa4115109d741cd94a3c409ed89e61f"
ACCT = "acct_85b680d4c5ba49a29f19c173672aebea"
ROOT = "/Users/mahmoodmapara/Desktop/terminal-bench-qc"
TASKS = f"{ROOT}/_local/refl_eval_pool/eval-candidate-pool/tasks"
IDMAP = f"{ROOT}/_local/qc_out_eval_pool/rls_taskids.json"

K = [l.split("=", 1)[1].strip().strip('"').strip("'")
     for l in open(f"{ROOT}/.env") if l.startswith("RLS_KEY=")][0]
H = {"Authorization": f"Bearer {K}", "X-Campaign-Id": CAMP, "X-Company-Id": COMP,
     "X-Account-Id": ACCT, "User-Agent": "curl/8.7.1", "Content-Type": "application/json"}
ck = lambda f: f"custom_fields->>'{f}'"


def meta_val(txt, keys):
    """First matching `key = "value"` or `key = value` from the toml text."""
    for k in keys:
        m = re.search(rf'(?m)^\s*{k}\s*=\s*"?([^"\n]+?)"?\s*$', txt)
        if m:
            return m.group(1).strip()
    return None


def main():
    idmap = json.load(open(IDMAP))
    updates = []
    for name, tid in idmap.items():
        fp = f"{TASKS}/{name}/task.toml"
        if not os.path.isfile(fp):
            continue
        txt = open(fp, errors="replace").read()
        cf = []
        diff = meta_val(txt, ["difficulty"])
        cat = meta_val(txt, ["category", "diversity_category"])
        sub = meta_val(txt, ["subcategory", "diversity_subcategory"])
        hrs = meta_val(txt, ["expert_time_estimate_hours"])
        a8 = meta_val(txt, ["avg_at_8"])
        if diff: cf.append({"column_key": ck("field_difficulty"), "value": diff})
        if cat: cf.append({"column_key": ck("field_category"), "value": cat})
        if sub: cf.append({"column_key": ck("field_subcategory"), "value": sub})
        if hrs:
            try: cf.append({"column_key": ck("field_expert_hours"), "value": float(hrs)})
            except ValueError: pass
        if a8:
            try: cf.append({"column_key": ck("field_avg_at_8"), "value": float(a8)})
            except ValueError: pass
        cf.append({"column_key": ck("field_reflection_batch"), "value": "reflection-eval-2026-07-08"})
        updates.append({"task_id": tid, "updates": cf})

    print(f"updating {len(updates)} tasks", flush=True)
    B, okc, fails = 500, 0, 0
    for i in range(0, len(updates), B):
        body = {"updates": updates[i:i + B]}
        for att in range(6):
            r = requests.post(f"{API}/tasks/bulk-update", headers=H, data=json.dumps(body), timeout=180)
            if r.status_code == 429:
                time.sleep(15 * (att + 1)); continue
            break
        if r.status_code != 200:
            print("ERR", r.status_code, r.text[:200]); break
        res = r.json()["results"]
        okc += sum(1 for x in res if x.get("success"))
        fails += sum(1 for x in res if not x.get("success"))
        print(f"  {i + len(updates[i:i+B])}/{len(updates)}  (ok {okc}, fail {fails})", flush=True)
        time.sleep(13)
    print(f"DONE custom fields: {okc} ok / {fails} failed / {len(updates)} total")


if __name__ == "__main__":
    main()
