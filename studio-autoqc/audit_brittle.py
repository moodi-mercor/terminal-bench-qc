#!/usr/bin/env python3
"""Brittle-verifier audit on the ALL-FAIL tasks (the angle we missed).

All-fail = no model solved it, but the ORACLE passes. That can mean the task is just
HARD, or that the verifier is BRITTLE — only the exact official output passes, and the
models' correct-but-different solutions were wrongly rejected. The FN audit skipped these
(it needed a passing sibling), so they were never checked this way.

Per task we take the highest-effort FAILED model attempt and ask: did this attempt actually
satisfy the instruction's intent (=> verifier wrongly failed it => BRITTLE), or did it
genuinely not solve the task (=> FAIR, just hard)?

Inputs: instruction + verifier (reused from _local/allfail_audit/src cache when present,
else fetched), plus the attempt's diff + which checks failed + error (from the trajectory).
Local Anthropic judge (ANT_KEY). Governed Studio fetch. Parallel. Resumable.
"""
import argparse, collections, json, os, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
SAPI = "https://api.studio.mercor.com"
AAPI = "https://api.anthropic.com/v1/messages"
OUT = f"{ROOT}/_local/brittle_audit"
CACHE = f"{ROOT}/_local/allfail_audit/src"   # instruction + tests already pulled here
MAXREQ, WINDOW = 4000, 3600.0                 # reduced — coexists with the gameable audit
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


def cached(name, rel):
    p = f"{CACHE}/{name}/{rel}"
    return open(p, errors="replace").read() if os.path.exists(p) else None


def fetch_files(tid, name):
    """instruction + tests/* — from cache, else snapshot."""
    out = {}
    inst = cached(name, "instruction.md")
    if inst is None:
        try:
            govern()
            files = requests.get(f"{SAPI}/snapshots/task/{tid}/input-files", headers=SH, timeout=60).json().get("files", [])
            for f in files:
                raw = f["key"]; i = raw.find("filesystem/"); fs = raw[i:] if i >= 0 else raw
                rel = fs[len("filesystem/"):] if fs.startswith("filesystem/") else fs
                if rel == "instruction.md" or rel.startswith("tests/"):
                    govern()
                    j = requests.get(f"{SAPI}/snapshots/task/{tid}/file-url", headers=SH, params={"file_path": fs}, timeout=60).json()
                    out[rel] = requests.get(j["url"], timeout=120).content.decode(errors="replace")
        except Exception as e:
            out["_err"] = str(e)
    else:
        out["instruction.md"] = inst
        for fn in ("test_outputs.py", "test.sh"):
            t = cached(name, f"tests/{fn}")
            if t: out[f"tests/{fn}"] = t
    return out


def fetch_attempt(traj):
    govern()
    try:
        o = requests.get(f"{SAPI}/trajectories/{traj}", headers=SH, timeout=90).json().get("trajectory_output") or {}
    except Exception as e:
        return {"_err": str(e)}
    ts = o.get("test_statuses") or {}
    return {"diff": o.get("solution") or "", "failed": [k for k, v in ts.items() if str(v).lower() != "pass"],
            "passed": [k for k, v in ts.items() if str(v).lower() == "pass"],
            "error": o.get("error_message") or "", "exit": o.get("exit_code")}


JUDGE = """A benchmark task could not be solved by ANY AI model, yet its official solution passes. \
You must decide if the VERIFIER IS BRITTLE (too strict — only the exact official output passes, wrongly \
rejecting other correct solutions) or if the task is simply HARD (models genuinely failed).

Below is a REAL model attempt that scored 0. Judge ONLY this: does the attempt genuinely satisfy the \
instruction's actual requirements, such that a FAIR verifier should have passed it?
 - BRITTLE: the attempt meets the instruction's intent, but the verifier failed it for a superficial reason \
(exact string/format/whitespace/ordering mismatch, byte-exact reproduction of an underdetermined output, \
overly tight tolerance, checking an unstated implementation detail).
 - FAIR: the attempt is incomplete, wrong, missing required behavior, or crashes — the verifier correctly failed it. \
(This is the common case for a genuinely hard task.)
Be conservative: default to FAIR unless the attempt clearly does the required work.

=== instruction.md ===
{instruction}

=== verifier (tests) ===
{tests}

=== checks that FAILED ===
{failed}
=== checks that PASSED ===
{passed}
=== verifier error_message ===
{error}

=== the model's attempt (diff / final solution) ===
{diff}

Reason briefly, then output ONLY this JSON on the last line:
{{"verdict": "brittle"|"fair"|"uncertain", "issue": "<one line: what the verifier rejected, if brittle>", "confidence": "high"|"medium"|"low"}}"""


def cap(s, n):
    return (s[:n] + f"\n<<cut at {n}; orig {len(s)}>>") if len(s) > n else s


def work(tid, traj, name, model):
    ff = fetch_files(tid, name)
    at = fetch_attempt(traj)
    tests = "\n".join(f"--- {k} ---\n{cap(v,25000)}" for k, v in ff.items() if k.startswith("tests/")) or "<none>"
    prompt = JUDGE.format(instruction=cap(ff.get("instruction.md", "<missing>"), 16000), tests=tests,
                          failed=", ".join(at.get("failed", []))[:1500] or "<unknown>",
                          passed=", ".join(at.get("passed", []))[:1500] or "<none>",
                          error=cap(at.get("error", "") or "<none>", 3000),
                          diff=cap(at.get("diff", "") or "<empty>", 45000))
    body = {"model": model, "max_tokens": 1024, "messages": [{"role": "user", "content": prompt}]}
    for a in range(4):
        r = requests.post(AAPI, headers=AH, data=json.dumps(body), timeout=120)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(20 * (a + 1)); continue
        if r.status_code >= 300:
            v = {"verdict": "uncertain", "issue": f"api {r.status_code}", "confidence": "low"}; break
        txt = "".join(b.get("text", "") for b in r.json().get("content", []))
        v = {"verdict": "uncertain", "issue": "unparseable", "confidence": "low"}
        for line in reversed(txt.strip().splitlines()):
            line = line.strip().strip("`")
            if line.startswith("{"):
                try:
                    v = json.loads(line); break
                except Exception:
                    pass
        break
    else:
        v = {"verdict": "uncertain", "issue": "retries", "confidence": "low"}
    return {"task_id": tid, "task_name": name, "verdict": v.get("verdict"),
            "issue": v.get("issue", ""), "confidence": v.get("confidence"),
            "n_failed": len(at.get("failed", [])), "n_passed": len(at.get("passed", []))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    tgt = [l.strip().split("\t") for l in open(f"{OUT}/targets.txt") if l.strip()]
    tgt = [(x[0], x[1], x[2] if len(x) > 2 else x[0]) for x in tgt]
    res = f"{OUT}/results.jsonl"
    have = set(json.loads(l)["task_id"] for l in open(res)) if os.path.exists(res) else set()
    todo = [t for t in tgt if t[0] not in have]
    if a.limit:
        todo = todo[:a.limit]
    print(f"{len(tgt)} all-fail tasks, {len(have)} done, judging {len(todo)} attempts ({a.workers}w, {a.model})", flush=True)
    c = collections.Counter(); done = 0
    f = open(res, "a")
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(work, t, tr, n, a.model): t for t, tr, n in todo}
        for fut in as_completed(futs):
            try:
                rec = fut.result()
            except Exception as e:
                rec = {"task_id": futs[fut], "verdict": "uncertain", "issue": f"err {e}", "confidence": "low"}
            with _wl:
                f.write(json.dumps(rec) + "\n"); f.flush()
            c[rec["verdict"]] += 1; done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(todo)}  {dict(c)}", flush=True)
    print(f"DONE. {dict(c)}", flush=True)


if __name__ == "__main__":
    main()
