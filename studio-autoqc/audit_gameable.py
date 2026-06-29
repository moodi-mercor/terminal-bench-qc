#!/usr/bin/env python3
"""Strict reward-hack + answer-leakage audit (buckets 5+6: healthy tasks).

Two questions per task, erring toward FLAGGING:
  A) GAMEABLE  — could a trivial/hardcoded/empty/wrong solution still score reward=1?
                 (existence-only checks, substring asserts, constant-returnable values,
                  agent-writable reward/grader, weak tolerances, etc.)
  B) LEAKED    — can the agent get the answer WITHOUT the work, because the expected
                 output / ground truth / test logic / solution is visible in its image?
                 (Dockerfile COPYs tests/ or solution/ or expected_*.json into the agent
                  image; environment/ contains the answer; grader is agent-writable.)

The agent only sees files baked into its image from environment/ (per the Dockerfile);
tests/ and solution/ are grading-only. So the judge gets: full file TREE + instruction
+ Dockerfile + verifier + solution, and reasons about reachability.

Local Anthropic judge (ANT_KEY) — no Studio rate limit on judging. Studio fetches are
governed <9k/hr. Parallel workers. Resumable (results.jsonl). Validate flags against the
FP ground truth before trusting at scale.

Usage:
  python audit_gameable.py --limit 30          # smoke
  python audit_gameable.py --workers 12        # full
"""
import argparse
import collections
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
SAPI = "https://api.studio.mercor.com"
AAPI = "https://api.anthropic.com/v1/messages"
OUT = f"{ROOT}/_local/gameable_audit"
MAXREQ, WINDOW = 9000, 3600.0
_req = collections.deque(); _gl = threading.Lock(); _wl = threading.Lock()


def envkey(n):
    for l in open(f"{ROOT}/.env"):
        if l.startswith(n + "="):
            return l.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit(f"no {n}")


SH = {"Authorization": f"Bearer {envkey('RLS_KEY')}", "X-Campaign-Id": "camp_4e196b1414a1499db54b43233104b0a7",
      "X-Company-Id": "comp_2fa4115109d741cd94a3c409ed89e61f",
      "X-Account-Id": "acct_85b680d4c5ba49a29f19c173672aebea", "User-Agent": "curl/8.7.1"}
AH = {"x-api-key": envkey("ANT_KEY"), "anthropic-version": "2023-06-01", "content-type": "application/json"}


def govern():
    while True:
        with _gl:
            now = time.time()
            while _req and now - _req[0] > WINDOW:
                _req.popleft()
            if len(_req) < MAXREQ:
                _req.append(now); return
            w = WINDOW - (now - _req[0]) + 1
        time.sleep(max(1, w))


def sget(path, **p):
    govern()
    return requests.get(f"{SAPI}{path}", headers=SH, params=p or None, timeout=60)


def fetch(tid, name):
    """Return (file_tree:list[str], texts:{rel:content}) — instruction, Dockerfile, tests, solve."""
    dst = f"{OUT}/src/{name}"
    try:
        files = sget(f"/snapshots/task/{tid}/input-files").json().get("files", [])
    except Exception as e:
        return [], {"_err": str(e)}
    tree, want = [], []
    for f in files:
        raw = f["key"]; i = raw.find("filesystem/")
        fs = raw[i:] if i >= 0 else raw
        rel = fs[len("filesystem/"):] if fs.startswith("filesystem/") else fs
        if not rel:
            continue
        tree.append(rel)
        if (rel == "instruction.md" or rel.endswith("Dockerfile") or rel.startswith("tests/")
                or rel == "solution/solve.sh" or rel.endswith("/expected") or "expected" in rel.lower()):
            want.append((rel, fs))
    texts = {}
    for rel, fs in want:
        local = f"{dst}/{rel}"
        if os.path.exists(local):
            texts[rel] = open(local, errors="replace").read(); continue
        try:
            j = sget(f"/snapshots/task/{tid}/file-url", file_path=fs).json()
            r = requests.get(j["url"], timeout=120); r.raise_for_status()
            os.makedirs(os.path.dirname(local), exist_ok=True); open(local, "wb").write(r.content)
            texts[rel] = r.content.decode(errors="replace")
        except Exception as e:
            texts[rel] = f"<err {e}>"
    return tree, texts


JUDGE = """You are a STRICT anti-cheat reviewer for a terminal benchmark task. Err toward FLAGGING.

How grading works: the AGENT only sees files baked into its image from environment/ (per the Dockerfile). \
The tests/ and solution/ directories are NOT given to the agent — they are mounted only at grading time. \
The verifier writes reward=1 (pass) or 0 (fail).

Judge TWO things:

A) GAMEABLE — could a trivial, empty, hardcoded, or deliberately WRONG solution still make the verifier output \
reward=1? Look for: existence-only checks, substring/regex-only asserts, constant-returnable expected values, \
exit-code-only checks, weak or absent assertions, over-broad tolerances, or a reward/grader the agent could write directly.

B) LEAKED — can the agent get the answer WITHOUT doing the work, because the expected output / ground truth / \
test logic / reference solution is reachable from its environment? Look for: the Dockerfile COPYing tests/, \
solution/, or an expected_*/answer/ground-truth file INTO the agent image; environment/ containing the answer; \
the verifier or reward file being agent-writable; the test reading a file the agent can also read and edit.

=== FILE TREE (every path in the task; anything under environment/ or COPYed by the Dockerfile is agent-visible) ===
{tree}

=== instruction.md ===
{instruction}

=== Dockerfile (what is built into the AGENT image) ===
{dockerfile}

=== tests/ (the verifier — NOT given to agent) ===
{tests}

=== solution/solve.sh (reference) ===
{solution}

Reason briefly, then output ONLY this JSON on the last line:
{{"gameable": true|false, "leaked": true|false, "vector": "<one concrete shortcut a cheating agent would use, else empty>", "confidence": "high"|"medium"|"low"}}"""


def cap(s, n):
    return (s[:n] + f"\n<<cut at {n}; orig {len(s)}>>") if len(s) > n else s


def judge(tree, texts, model):
    dockerfile = "\n".join(f"--- {k} ---\n{cap(v,12000)}" for k, v in texts.items() if k.endswith("Dockerfile")) or "<none found>"
    tests = "\n".join(f"--- {k} ---\n{cap(v,30000)}" for k, v in texts.items() if k.startswith("tests/")) or "<none>"
    prompt = JUDGE.format(tree="\n".join(sorted(tree))[:8000],
                          instruction=cap(texts.get("instruction.md", "<missing>"), 20000),
                          dockerfile=dockerfile, tests=tests,
                          solution=cap(texts.get("solution/solve.sh", "<missing>"), 20000))
    body = {"model": model, "max_tokens": 1024, "messages": [{"role": "user", "content": prompt}]}
    for a in range(4):
        r = requests.post(AAPI, headers=AH, data=json.dumps(body), timeout=120)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(20 * (a + 1)); continue
        if r.status_code >= 300:
            return {"gameable": None, "leaked": None, "vector": f"api {r.status_code}", "confidence": "low"}
        txt = "".join(b.get("text", "") for b in r.json().get("content", []))
        for line in reversed(txt.strip().splitlines()):
            line = line.strip().strip("`")
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except Exception:
                    break
        return {"gameable": None, "leaked": None, "vector": "unparseable", "confidence": "low"}
    return {"gameable": None, "leaked": None, "vector": "retries", "confidence": "low"}


def work(tid, name, model):
    tree, texts = fetch(tid, name)
    v = judge(tree, texts, model)
    return {"task_id": tid, "task_name": name, "gameable": v.get("gameable"), "leaked": v.get("leaked"),
            "vector": v.get("vector", ""), "confidence": v.get("confidence")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=12)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    pairs = [l.strip().split("\t") for l in open(f"{OUT}/task_names.txt") if l.strip()]
    pairs = [(p[0], p[1] if len(p) > 1 else p[0]) for p in pairs]
    res = f"{OUT}/results.jsonl"
    have = set(json.loads(l)["task_id"] for l in open(res)) if os.path.exists(res) else set()
    todo = [(t, n) for t, n in pairs if t not in have]
    if a.limit:
        todo = todo[:a.limit]
    print(f"{len(pairs)} tasks, {len(have)} done, auditing {len(todo)} ({a.workers}w, {a.model})", flush=True)
    c = collections.Counter(); done = 0
    f = open(res, "a")
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(work, t, n, a.model): t for t, n in todo}
        for fut in as_completed(futs):
            try:
                rec = fut.result()
            except Exception as e:
                rec = {"task_id": futs[fut], "gameable": None, "leaked": None, "vector": f"err {e}", "confidence": "low"}
            with _wl:
                f.write(json.dumps(rec) + "\n"); f.flush()
            if rec.get("gameable"): c["gameable"] += 1
            if rec.get("leaked"): c["leaked"] += 1
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(todo)}  gameable={c['gameable']} leaked={c['leaked']}", flush=True)
    print(f"DONE. gameable={c['gameable']} leaked={c['leaked']} of {done}", flush=True)


if __name__ == "__main__":
    main()
