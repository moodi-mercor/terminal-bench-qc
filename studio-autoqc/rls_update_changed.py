#!/usr/bin/env python3
"""Update RLS task snapshots + metadata for tasks the eng changed on delivery-validated-pool.

For each changed task: re-upload its branch filesystem (overwrites edits), remove files that
the branch deleted, then refresh custom_fields from the branch task.toml. Existing RLS task_id
is reused (snapshot update is versioned/immutable). Resumable via a done-file.
"""
import json, os, re, time, requests

API = "https://api.studio.mercor.com"
CAMP = "camp_4e196b1414a1499db54b43233104b0a7"; COMP = "comp_2fa4115109d741cd94a3c409ed89e61f"
ACCT = "acct_85b680d4c5ba49a29f19c173672aebea"
ROOT = "/Users/mahmoodmapara/Desktop/terminal-bench-qc"
BRANCH = "/tmp/branch_tasks_src/eval-candidate-pool/tasks"          # eng's version (changed tasks)
MINE = f"{ROOT}/_local/refl_eval_pool/eval-candidate-pool/tasks"    # RLS-state version
IDMAP = f"{ROOT}/_local/qc_out_eval_pool/rls_taskids.json"
LIST = "/tmp/changed_shared.txt"
DONE = f"{ROOT}/_local/qc_out_eval_pool/rls_update_done.txt"
K = [l.split("=", 1)[1].strip().strip('"').strip("'") for l in open(f"{ROOT}/.env") if l.startswith("RLS_KEY=")][0]
H = {"Authorization": f"Bearer {K}", "X-Campaign-Id": CAMP, "X-Company-Id": COMP,
     "X-Account-Id": ACCT, "User-Agent": "curl/8.7.1"}
HJ = {**H, "Content-Type": "application/json"}
ck = lambda f: f"custom_fields->>'{f}'"


def rels(root):
    out = {}
    for dp, _, fns in os.walk(root):
        for fn in fns:
            if fn.endswith((".orig", ".refactored", ".bak")): continue
            full = os.path.join(dp, fn)
            out[os.path.relpath(full, root)] = full
    return out


def meta_val(txt, keys):
    for k in keys:
        m = re.search(rf'(?m)^\s*{k}\s*=\s*"?([^"\n]+?)"?\s*$', txt)
        if m: return m.group(1).strip()
    return None


def upload(tid, branch_dir, removed):
    paths = rels(branch_dir)
    for att in range(6):
        fh = []
        try:
            files = []
            for rel, full in paths.items():
                f = open(full, "rb"); fh.append(f)
                files.append(("files", (f"filesystem/{rel}", f, "application/octet-stream")))
            data = [("remove_files", f"filesystem/{r}") for r in removed]
            r = requests.post(f"{API}/snapshots/task/{tid}/update", headers=H,
                              files=files, data=data or None, timeout=240)
            if r.status_code == 201: return True, ""
            if r.status_code == 429: time.sleep(13 * (att + 1)); continue
            return False, f"{r.status_code}:{r.text[:100]}"
        except Exception as e:
            if att < 5: time.sleep(5 * (att + 1)); continue
            return False, str(e)[:100]
        finally:
            for f in fh:
                try: f.close()
                except: pass
    return False, "429-exhausted"


def main():
    idmap = json.load(open(IDMAP))
    tasks = [t for t in open(LIST).read().split() if t]
    done = set(open(DONE).read().split()) if os.path.exists(DONE) else set()
    tasks = [t for t in tasks if t not in done]
    print(f"updating {len(tasks)} changed tasks", flush=True)
    cf_updates = []
    ok = 0
    for i, t in enumerate(tasks, 1):
        tid = idmap.get(t)
        bdir = f"{BRANCH}/{t}"
        if not tid or not os.path.isdir(bdir):
            print(f"  [skip] {t} (no id/dir)"); continue
        removed = sorted(set(rels(f"{MINE}/{t}")) - set(rels(bdir)))  # files eng deleted
        good, err = upload(tid, bdir, removed)
        if not good:
            print(f"  [FAIL] {t}: {err}", flush=True); continue
        # metadata refresh
        txt = open(f"{bdir}/task.toml", errors="replace").read()
        u = []
        for key, fid in [("difficulty", "field_difficulty"), ("category", "field_category"),
                         ("subcategory", "field_subcategory")]:
            v = meta_val(txt, [key, "diversity_" + key])
            if v: u.append({"column_key": ck(fid), "value": v})
        for key, fid in [("expert_time_estimate_hours", "field_expert_hours"), ("avg_at_8", "field_avg_at_8")]:
            v = meta_val(txt, [key])
            if v:
                try: u.append({"column_key": ck(fid), "value": float(v)})
                except ValueError: pass
        if u: cf_updates.append({"task_id": tid, "updates": u})
        open(DONE, "a").write(t + "\n"); ok += 1
        if i % 25 == 0 or i == len(tasks): print(f"  [{i}/{len(tasks)}] uploaded (ok {ok})", flush=True)
    # bulk metadata
    if cf_updates:
        r = requests.post(f"{API}/tasks/bulk-update", headers=HJ,
                          data=json.dumps({"updates": cf_updates}), timeout=180)
        print("metadata bulk-update ->", r.status_code,
              sum(1 for x in r.json().get("results", []) if x.get("success")) if r.status_code == 200 else r.text[:150])
    print(f"DONE: {ok} tasks updated")


if __name__ == "__main__":
    main()
