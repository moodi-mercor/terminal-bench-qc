#!/usr/bin/env python3
"""Shared helpers for the GLM-5.2 pass@5 retry loop.

Studio miscategorises Fireworks/Baseten rate-limit errors as trajectory_status
"failed" (glossary says failed = "model failure, part of dataset"), so we CANNOT
trust status alone. A run only counts as a *genuine graded attempt* when the model
actually produced tokens and there is no infra error_message. Everything else
(rate_limit / BadGateway / Timeout / 5xx / zero-token / missing score) must be retried.
"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

API = "https://api.studio.mercor.com"
CAMP = "camp_4e196b1414a1499db54b43233104b0a7"
COMP = "comp_2fa4115109d741cd94a3c409ed89e61f"
ACCT = "acct_85b680d4c5ba49a29f19c173672aebea"
ROOT = "/Users/mahmoodmapara/Desktop/terminal-bench-qc"

INFRA_MARKERS = ("ratelimit", "rate_limit", "rate limit", "badgateway", "bad gateway",
                 "timeout", "apierror", "serviceunavailable", "502", "503", "504",
                 "no fallback model group", "overloaded", "connection")


def key():
    for l in open(f"{ROOT}/.env"):
        if l.startswith("RLS_KEY="):
            return l.split("=", 1)[1].strip().strip('"').strip("'")


H = {"Authorization": f"Bearer {key()}", "X-Campaign-Id": CAMP, "X-Company-Id": COMP,
     "X-Account-Id": ACCT, "User-Agent": "curl/8.7.1", "Content-Type": "application/json"}


def _get_page(bid, page):
    for attempt in range(6):
        try:
            r = requests.get(f"{API}/trajectories/batch/{bid}", headers=H,
                             params={"limit": "100", "offset": str((page - 1) * 100)},
                             timeout=120)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"batch page fetch failed after retries: {bid} p{page}")


def list_batch(bid):
    """Lean list of (trajectory_id, task_id, task_name, status) for a batch."""
    rows, page = [], 1
    while True:
        d = _get_page(bid, page)
        items = d.get("trajectories", [])
        if not items:
            break
        for it in items:
            rows.append({"id": it["trajectory_id"], "task_id": it.get("task_id"),
                         "task": it.get("task_name"), "status": it.get("trajectory_status")})
        pg = d.get("pagination", {})
        if page >= pg.get("total_pages", page):
            break
        page += 1
    return rows


def classify(tid):
    """Return dict: {id, genuine: bool, score, retry_reason}."""
    for _ in range(4):
        try:
            r = requests.get(f"{API}/trajectories/{tid}", headers=H, timeout=120)
            if r.status_code == 200:
                to = r.json().get("trajectory_output") or {}
                em = str(to.get("error_message") or "").lower()
                tok = (to.get("usage_metrics") or {}).get("total_tokens", 0) or 0
                score = to.get("score")
                # Agent hit the rollout time budget with real work done: that is an
                # unsolved run under Terminal-Bench semantics, not an infra retry —
                # retrying just gives the model extra chances and re-times-out.
                if "agenttimeouterror" in em and tok > 0:
                    return {"id": tid, "genuine": True, "score": 0.0, "retry_reason": None}
                infra = any(m in em for m in INFRA_MARKERS)
                if infra:
                    return {"id": tid, "genuine": False, "score": None, "retry_reason": "infra"}
                if score is None:
                    return {"id": tid, "genuine": False, "score": None, "retry_reason": "no_score"}
                if tok == 0 and em:
                    return {"id": tid, "genuine": False, "score": None, "retry_reason": "zero_tok_err"}
                # genuine graded attempt (model ran; tests passed or failed for real)
                return {"id": tid, "genuine": True, "score": float(score), "retry_reason": None}
        except Exception:
            pass
        time.sleep(1.5)
    return {"id": tid, "genuine": False, "score": None, "retry_reason": "probe_err"}


def classify_many(ids, workers=10):
    out = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for f in as_completed([ex.submit(classify, t) for t in ids]):
            out.append(f.result())
    return out


def batch_status(bid):
    from collections import Counter
    return Counter(r["status"] for r in list_batch(bid))
