#!/usr/bin/env python3
"""Read-only pre-flight for moving TB-QC Layers 2/3 into Studio modular AutoQC.

NO writes — only GET. Reuses the same auth as studio_pull.py (RLS_KEY from .env).

A1 (access + inventory + type):
  - GET /campaigns/                 resolve company_id + account_id
  - GET /qc-specs/?campaign_id=     modular AutoQC reachable + existing modules (stay additive)
  - GET /worlds/{id}               world readiness + task_spec_config
A2 (the real go/no-go for Layers 2/3 — can an autograder SEE the verifier + ref soln?):
  - GET /tasks/world/{id}/full      sample a real task
  - GET /snapshots/task/{id}/input-files   does the staged FS contain tests/, solution/,
        instruction.md, task.toml, environment/Dockerfile ?

Usage: python studio-autoqc/preflight.py
"""
import os
import sys
import json
import requests

API = "https://api.studio.mercor.com"
CAMPAIGN = "camp_4e196b1414a1499db54b43233104b0a7"   # [OTS] Terminal Bench
COMPANY = "comp_2fa4115109d741cd94a3c409ed89e61f"
WORLD = "world_2c7cdb23737845ad83a9acfa1aa8c25b"

NEED = ["instruction.md", "task.toml", "environment/Dockerfile", "tests/", "solution/"]


def load_key():
    if os.environ.get("RLS_KEY"):
        return os.environ["RLS_KEY"]
    here = os.path.abspath(os.path.dirname(__file__))
    for _ in range(6):
        cand = os.path.join(here, ".env")
        if os.path.isfile(cand):
            for line in open(cand):
                line = line.strip()
                if line.startswith("RLS_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        here = os.path.dirname(here)
    sys.exit("RLS_KEY not set and not found in .env")


def hdr(key, account_id=None):
    h = {"Authorization": f"Bearer {key}", "User-Agent": "curl/8.7.1",
         "X-Campaign-Id": CAMPAIGN, "X-Company-Id": COMPANY}
    if account_id:
        h["X-Account-Id"] = account_id
    return h


def get(url, key, account_id=None, **kw):
    r = requests.get(url, headers=hdr(key, account_id), timeout=90, **kw)
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)


def main():
    key = load_key()
    print("== A1.1 Campaign access ==")
    st, camps = get(f"{API}/campaigns/", key)
    if st != 200 or not isinstance(camps, list):
        print(f"  FAIL GET /campaigns/ -> {st}: {str(camps)[:200]}"); sys.exit(1)
    row = next((c for c in camps if c.get("campaign_id") == CAMPAIGN), None)
    if not row:
        print(f"  FAIL: campaign {CAMPAIGN} not visible to this key (saw {len(camps)} campaigns)."); sys.exit(1)
    company_id = row.get("company_id"); account_id = row.get("account_id")
    print(f"  OK  name={row.get('name')!r}  company={company_id}  account={account_id}")

    print("== A1.2 Modular AutoQC (qc-specs) reachable + existing modules ==")
    st, specs = get(f"{API}/qc-specs/", key, account_id, params={"campaign_id": CAMPAIGN})
    if st != 200:
        print(f"  FAIL GET /qc-specs/ -> {st}: {str(specs)[:300]}"); sys.exit(1)
    mods = specs.get("specs", []) if isinstance(specs, dict) else (specs or [])
    latest = [m for m in mods if m.get("is_latest", True)]
    print(f"  OK  {len(latest)} existing latest module(s):")
    for m in latest:
        print(f"     - {str(m.get('name')):32s} scope={str(m.get('scope_type')):8s} "
              f"subject={str(m.get('subject_kind')):10s} v{m.get('version')} ({m.get('qc_spec_id')})")
    if not latest:
        print("     (none — clean slate, fully additive)")

    print("== A1.2b Existing module spec (what's already covered) ==")
    for m in latest:
        sid = m.get("qc_spec_id")
        st, spec = get(f"{API}/qc-specs/{sid}", key, account_id)
        if st != 200:
            print(f"  WARN GET /qc-specs/{sid} -> {st}"); continue
        rub = (spec.get("spec") or spec).get("rubric", {}) if isinstance(spec, dict) else {}
        secs = rub.get("sections", [])
        print(f"  {m.get('name')!r}: {len(secs)} section(s)")
        for s in secs:
            dims = s.get("dimensions", [])
            print(f"     section {s.get('name')!r}: {len(dims)} dims -> "
                  + ", ".join(d.get("dim_id", d.get("name", "?")) for d in dims))

    print("== A1.3 World readiness ==")
    st, world = get(f"{API}/worlds/{WORLD}", key, account_id)
    if st != 200:
        print(f"  FAIL GET /worlds/{WORLD} -> {st}: {str(world)[:200]}"); sys.exit(1)
    wname = world.get("name") if isinstance(world, dict) else "?"
    tsc = world.get("task_spec_config") if isinstance(world, dict) else None
    print(f"  OK  world={wname!r}  task_spec_config={'present' if tsc else 'absent'}")

    print("== A2 FS exposure probe (THE go/no-go for Layers 2/3) ==")
    tlist = []
    for ep, params in [
        (f"{API}/tasks/world/{WORLD}", {"limit": 3}),
        (f"{API}/tasks/world/{WORLD}", None),
        (f"{API}/tasks/world/{WORLD}/full", None),
    ]:
        st, tasks = get(ep, key, account_id, params=params)
        cand = tasks.get("tasks", tasks if isinstance(tasks, list) else []) if isinstance(tasks, (dict, list)) else []
        if st == 200 and cand:
            tlist = cand
            print(f"  (tasks via {ep.split('/world/')[0]+'/world/'+WORLD}{'?'+str(params) if params else ''} -> {len(cand)})")
            break
        print(f"  try {ep} {params or ''} -> {st}")
    if not tlist:
        print(f"  FAIL: could not sample tasks."); sys.exit(1)
    sample = tlist[0]
    tid = sample.get("task_id") or sample.get("id")
    print(f"  sample task: {(sample.get('task_name') or sample.get('name'))!r} ({tid})")
    st, snap = get(f"{API}/snapshots/task/{tid}/input-files", key, account_id)
    files = snap.get("files", []) if isinstance(snap, dict) else []
    paths = [(f.get("key") or f.get("path")) if isinstance(f, dict) else str(f) for f in files]
    paths = [p.split("filesystem/", 1)[-1] if p and "filesystem/" in p else p for p in paths]
    paths = [p for p in paths if p]
    print(f"  staged FS: {len(paths)} files")
    for p in sorted(paths)[:40]:
        print(f"     {p}")
    print("  --- required-for-QC presence ---")
    blob = "\n".join(paths)
    for need in NEED:
        hit = need in blob if need.endswith("/") else any(p == need or p.endswith("/" + need) for p in paths)
        print(f"     {'OK ' if hit else 'MISSING'}  {need}")


if __name__ == "__main__":
    main()
