#!/usr/bin/env python3
"""Audit the all-fail tasks (0% model pass) — broken verifier vs fine-but-hard.

These tasks were never LLM-audited (the trajectory FN run needed a sibling pass to
prove solvability; all-fail tasks have none). For each, fetch instruction + verifier
+ the ORACLE solution from the Studio snapshot, then ask a local Anthropic judge ONE
question: would running the official solution make the verifier PASS?
  - NO  -> the task is BROKEN (verifier rejects correct work / unsatisfiable / bad key).
  - YES -> the task is FINE, just hard. Keep it.

Studio fetches are governed under the 10k req/hr cap (snapshot list + file-url count;
the S3 download + Anthropic calls do NOT). Judging is local via ANT_KEY — no Studio
limit. Resumable: results.jsonl is truth. Files cached under src/<task>/.

Usage:
  python audit_allfail.py                       # full run
  python audit_allfail.py --limit 5             # smoke
  python audit_allfail.py --model claude-opus-4-8
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
OUT = f"{ROOT}/_local/allfail_audit"
MAXREQ, WINDOW = 9000, 3600.0
_req = collections.deque()
_govlock = threading.Lock()
_writelock = threading.Lock()


def envkey(name):
    for l in open(f"{ROOT}/.env"):
        if l.startswith(name + "="):
            return l.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit(f"no {name}")


SH = {"Authorization": f"Bearer {envkey('RLS_KEY')}", "X-Campaign-Id": "camp_4e196b1414a1499db54b43233104b0a7",
      "X-Company-Id": "comp_2fa4115109d741cd94a3c409ed89e61f",
      "X-Account-Id": "acct_85b680d4c5ba49a29f19c173672aebea", "User-Agent": "curl/8.7.1"}
AH = {"x-api-key": envkey("ANT_KEY"), "anthropic-version": "2023-06-01", "content-type": "application/json"}


def govern():
    while True:
        with _govlock:
            now = time.time()
            while _req and now - _req[0] > WINDOW:
                _req.popleft()
            if len(_req) < MAXREQ:
                _req.append(now)
                return
            wait = WINDOW - (now - _req[0]) + 1
        time.sleep(max(1, wait))


def sjson(path, **params):
    govern()
    return requests.get(f"{SAPI}{path}", headers=SH, params=params or None, timeout=60).json()


def fetch_task(tid, name):
    """Cache instruction.md, tests/*, solution/solve.sh under src/<name>/. Returns dict of texts."""
    dst = f"{OUT}/src/{name}"
    want = lambda rel: rel == "instruction.md" or rel.startswith("tests/") or rel == "solution/solve.sh"
    try:
        files = sjson(f"/snapshots/task/{tid}/input-files").get("files", [])
    except Exception as e:
        return {"_err": f"list: {e}"}
    texts = {}
    for f in files:
        raw = f["key"]; i = raw.find("filesystem/")
        fs = raw[i:] if i >= 0 else raw
        rel = fs[len("filesystem/"):] if fs.startswith("filesystem/") else fs
        if not want(rel):
            continue
        local = f"{dst}/{rel}"
        if os.path.exists(local):
            texts[rel] = open(local, errors="replace").read(); continue
        try:
            j = sjson(f"/snapshots/task/{tid}/file-url", file_path=fs)
            r = requests.get(j["url"], timeout=120); r.raise_for_status()
            os.makedirs(os.path.dirname(local), exist_ok=True)
            open(local, "wb").write(r.content)
            texts[rel] = r.content.decode(errors="replace")
        except Exception as e:
            texts[rel] = f"<fetch error: {e}>"
    return texts


JUDGE = """You are auditing a terminal-coding benchmark task that NO model could solve (0% pass rate). \
Your job: decide whether the task is BROKEN or just HARD.

A task is BROKEN if running its OFFICIAL reference solution (solve.sh) would NOT make the verifier pass — e.g. the \
verifier checks something the solution doesn't produce, demands an exact value/format the spec underdetermines, \
references missing files/paths, is unsatisfiable, or the reference solution is itself wrong/incomplete/missing.

A task is HARD (not broken) if the reference solution looks like it WOULD satisfy the verifier in a clean run — \
the task is just too difficult for current models.

Read these three components:

=== instruction.md ===
{instruction}

=== solution/solve.sh (the ORACLE / official answer) ===
{solution}

=== tests/ (the verifier) ===
{tests}

Reason about whether the oracle solution, run from a clean environment, would make every verifier check pass. \
Then output ONLY a JSON object on the last line:
{{"verdict": "broken" | "hard" | "uncertain", "issue": "<one-line root cause if broken, else empty>", "confidence": "high"|"medium"|"low"}}"""


def judge(texts, model):
    # Caps set well above real file sizes (~5-10KB) so we NEVER truncate and create a
    # false "solution looks truncated -> broken" verdict. A flag marks any real overflow.
    def cap(s, n):
        return (s[:n] + f"\n<<TRUNCATED for prompt at {n} chars; original {len(s)}>>") if len(s) > n else s
    instruction = cap(texts.get("instruction.md", "<missing>"), 40000)
    solution = cap(texts.get("solution/solve.sh", "<missing — no oracle found>"), 60000)
    tests = "\n\n".join(f"--- {k} ---\n{cap(v, 40000)}" for k, v in texts.items()
                        if k.startswith("tests/")) or "<missing>"
    prompt = JUDGE.format(instruction=instruction, solution=solution, tests=tests)
    body = {"model": model, "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}]}
    for attempt in range(4):
        r = requests.post(AAPI, headers=AH, data=json.dumps(body), timeout=120)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(20 * (attempt + 1)); continue
        if r.status_code >= 300:
            return {"verdict": "uncertain", "issue": f"api {r.status_code}", "confidence": "low"}
        txt = "".join(b.get("text", "") for b in r.json().get("content", []))
        for line in reversed(txt.strip().splitlines()):
            line = line.strip().strip("`")
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except Exception:
                    break
        return {"verdict": "uncertain", "issue": "unparseable", "confidence": "low", "raw": txt[:200]}
    return {"verdict": "uncertain", "issue": "retries exhausted", "confidence": "low"}


def work(tid, name, model):
    texts = fetch_task(tid, name)
    has_oracle = "solution/solve.sh" in texts and not texts["solution/solve.sh"].startswith("<")
    v = judge(texts, model)
    return {"task_id": tid, "task_name": name, "verdict": v.get("verdict"),
            "issue": v.get("issue", ""), "confidence": v.get("confidence"), "has_oracle": has_oracle}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    pairs = []
    for line in open(f"{OUT}/task_names.txt"):
        tid, _, name = line.strip().partition("\t")
        if tid:
            pairs.append((tid, name or tid))
    res = f"{OUT}/results.jsonl"
    have = set()
    if os.path.exists(res):
        have = set(json.loads(l)["task_id"] for l in open(res))
    todo = [(t, n) for t, n in pairs if t not in have]
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(pairs)} all-fail tasks, {len(have)} done, auditing {len(todo)} "
          f"({args.workers} workers, {args.model})", flush=True)
    counts = collections.Counter()
    done = 0
    f = open(res, "a")
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(work, tid, name, args.model): tid for tid, name in todo}
        for fut in as_completed(futs):
            try:
                rec = fut.result()
            except Exception as e:
                rec = {"task_id": futs[fut], "verdict": "uncertain", "issue": f"work err: {e}", "confidence": "low"}
            with _writelock:
                f.write(json.dumps(rec) + "\n"); f.flush()
            counts[rec["verdict"]] += 1
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(todo)}  {dict(counts)}", flush=True)
    f.close()
    print(f"DONE. verdicts: {dict(counts)}", flush=True)


if __name__ == "__main__":
    main()
