#!/usr/bin/env python3
"""Shared foundation for the trajectory-audit skill (Layer 2).

Two concerns live here so the rest of the scripts stay short:
  1. The canonical findings schema — re-exported from the repo-wide
     `shared/common.py` (the SINGLE source of truth), so trajectory-audit
     findings aggregate into the SAME report as the other QC layers.
  2. Studio API access (base URL, auth, campaign/company headers, GET helper) —
     this is trajectory-audit specific and lives only here.

This is the SEAM between layers: trajectory-audit stays atomic (its own pull +
triage), but emits the same finding shape so one aggregator (`shared/aggregate.py`)
can fold static, semantic, behavioral, and trajectory findings into a single SSOT.
"""
import importlib.util as _ilu
import os
import sys
import time

import requests

# ----------------------------------------------- canonical finding schema ----
# Single source of truth in shared/common.py — re-export PASS/WARN/FAIL, worst,
# finding, emit, AREAS so every layer emits an identical finding shape.
_CANON = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "shared", "common.py"))
_spec = _ilu.spec_from_file_location("qc_shared_common", _CANON)
_schema = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_schema)
PASS, WARN, FAIL = _schema.PASS, _schema.WARN, _schema.FAIL
SEV_RANK, AREAS = _schema.SEV_RANK, _schema.AREAS
worst, finding, emit = _schema.worst, _schema.finding, _schema.emit
AREA_LAYER, layer_of = _schema.AREA_LAYER, _schema.layer_of
# task-tree helpers (used by difficulty.py to read recorded avg_at_8 from task.toml)
discover_tasks, task_paths = _schema.discover_tasks, _schema.task_paths
load_toml, get, read_text = _schema.load_toml, _schema.get, _schema.read_text

# ----------------------------------------------------------------- config ---
API = os.environ.get("STUDIO_API", "https://api.studio.mercor.com")
CAMPAIGN = os.environ.get("STUDIO_CAMPAIGN", "camp_4e196b1414a1499db54b43233104b0a7")  # [OTS] Terminal Bench
COMPANY = os.environ.get("STUDIO_COMPANY", "comp_2fa4115109d741cd94a3c409ed89e61f")


# -------------------------------------------------------------- studio io ----
def find_env():
    here = os.path.abspath(os.path.dirname(__file__))
    for _ in range(6):
        cand = os.path.join(here, ".env")
        if os.path.isfile(cand):
            return cand
        here = os.path.dirname(here)
    return None


def load_key():
    if os.environ.get("RLS_KEY"):
        return os.environ["RLS_KEY"]
    env = find_env()
    if not env:
        sys.exit("RLS_KEY not set and no .env found.")
    for line in open(env):
        line = line.strip()
        if line.startswith("RLS_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("RLS_KEY not found in .env")


def headers(key):
    return {"Authorization": f"Bearer {key}",
            "X-Campaign-Id": CAMPAIGN, "X-Company-Id": COMPANY}


def get_json(path, key, retries=3, **kw):
    """GET {API}{path} with auth; retry transient failures. `path` starts '/'."""
    url = path if path.startswith("http") else f"{API}{path}"
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=headers(key), timeout=120, **kw)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
