#!/usr/bin/env python3
"""avg@8 difficulty eval on MODAL (mercor-data-delivery) — native agent rollouts,
NOT Studio orchestration. Opus-4.8 (Anthropic API, billed to ANTHROPIC_API_KEY)
drives a bash tool inside each task's Modal sandbox; we then grade with the hidden
verifier. Per task = N rollouts; avg reward = difficulty (low = hard).

Validity: the agent image is built WITHOUT tests/ or solution/ (so the agent can't
read the answer). After the rollout we inject tests/ into the sandbox (chunked
base64) and run tests/test.sh to score. Fresh sandbox per rollout.

Usage:
  modalenv/bin/python modal_eval_avg8.py <repo> <task_list> [--runs 8] [--workers 20]
      [--limit N] [--state ...] [--out ...] [--max-steps 30]
"""
import argparse, base64, concurrent.futures as cf, io, json, os, re, sys, tarfile, threading, time
import urllib.request, urllib.error
import modal

API_URL = "https://api.anthropic.com/v1/messages"; VER = "2023-06-01"; MODEL = "claude-opus-4-8"
lock = threading.Lock(); APP = "mercor-eval-avg8"
SYS = ("You are an autonomous software engineer completing a task inside a Linux container. "
       "You have one tool: `bash`, which runs a shell command in the container and returns its "
       "output. The task's files are already in the container (typically under /app). Work "
       "step by step: explore, implement, and verify your solution. Do NOT ask questions — act. "
       "When the task is fully complete, reply with a short final message and stop calling tools.")
BASH_TOOL = [{"name": "bash", "description": "Run a bash command in the task container.",
              "input_schema": {"type": "object", "properties": {"command": {"type": "string"}},
                               "required": ["command"]}}]


def load_key():
    for v in ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"):
        if os.environ.get(v):
            return os.environ[v].strip()
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        p = os.path.join(here, ".env")
        if os.path.isfile(p):
            for ln in open(p):
                if ln.startswith("ANTHROPIC_API_KEY="):
                    return ln.split("=", 1)[1].strip().strip('"').strip("'")
        here = os.path.dirname(here)
    sys.exit("no ANTHROPIC_API_KEY")


def anthropic(key, messages, max_tokens=4096, retries=5):
    body = json.dumps({"model": MODEL, "max_tokens": max_tokens, "system": SYS,
                       "tools": BASH_TOOL, "messages": messages}).encode()
    req = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "x-api-key": key, "anthropic-version": VER, "content-type": "application/json"})
    for a in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503, 529) and a < retries - 1:
                time.sleep(3 * (a + 1) ** 2); continue
            return {"_err": f"{e.code}:{e.read()[:120].decode(errors='replace')}"}
        except Exception as e:
            if a < retries - 1:
                time.sleep(2 * (a + 1)); continue
            return {"_err": str(e)[:120]}


def sh(sb, command, timeout=120):
    try:
        p = sb.exec("bash", "-lc", command, timeout=timeout)
        out = p.stdout.read(); p.wait()
        return out[-6000:]
    except Exception as e:
        return f"[exec error: {str(e)[:200]}]"


def rollout(key, sb, instruction, max_steps):
    """Drive the agent for up to max_steps bash calls. Returns step count."""
    msgs = [{"role": "user", "content": f"Complete this task:\n\n{instruction}"}]
    for step in range(max_steps):
        r = anthropic(key, msgs)
        if "_err" in r:
            return step, r["_err"]
        content = r.get("content", [])
        msgs.append({"role": "assistant", "content": content})
        tool_uses = [b for b in content if b.get("type") == "tool_use"]
        if r.get("stop_reason") != "tool_use" or not tool_uses:
            return step, "done"
        results = []
        for tu in tool_uses:
            cmd = (tu.get("input") or {}).get("command", "")
            out = sh(sb, cmd) if cmd else "[no command]"
            results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": out or "(no output)"})
        msgs.append({"role": "user", "content": results})
    return max_steps, "max-steps"


def tests_tar_b64(tdir):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(os.path.join(tdir, "tests"), arcname="tests")
    return base64.b64encode(buf.getvalue()).decode()


def grade(sb, tests_b64):
    """Inject tests/ into the sandbox (chunked) at /, run the verifier, return reward 0/1."""
    sh(sb, "rm -f /tmp/t.b64", timeout=30)
    for i in range(0, len(tests_b64), 50000):
        chunk = tests_b64[i:i + 50000]
        sh(sb, f"printf %s '{chunk}' >> /tmp/t.b64", timeout=60)
    out = sh(sb, "base64 -d /tmp/t.b64 | tar xzf - -C / 2>/dev/null; "
                 "mkdir -p /logs/verifier /logs/tests 2>/dev/null; "
                 "bash /tests/test.sh >/tmp/v.log 2>&1; echo VRC=$?; "
                 "cat /logs/verifier/reward.txt 2>/dev/null; cat /logs/tests/reward.txt 2>/dev/null",
             timeout=600)
    rt = re.findall(r"(?m)^\s*([01](?:\.0)?)\s*$", out)
    m = re.search(r"VRC=(\d+)", out)
    if rt:
        return 1 if float(rt[-1]) >= 1 else 0
    return 1 if (m and m.group(1) == "0") else 0


def eval_task(key, app, repo, task, runs, max_steps, timeout):
    tdir = os.path.join(repo, "tasks", task)
    try:
        img = modal.Image.from_dockerfile(os.path.join(tdir, "environment", "Dockerfile"),
                                          context_dir=os.path.join(tdir, "environment"))
        instruction = open(os.path.join(tdir, "instruction.md"), errors="replace").read()
        tb64 = tests_tar_b64(tdir)
    except Exception as e:
        return task, None, 0, f"setup-err:{str(e)[:80]}"
    rewards = []
    for _ in range(runs):
        sb = None
        try:
            sb = modal.Sandbox.create(app=app, image=img, timeout=timeout, cpu=2, memory=4096)
            rollout(key, sb, instruction, max_steps)
            rewards.append(grade(sb, tb64))
        except Exception as e:
            rewards.append(0)
        finally:
            if sb is not None:
                try: sb.terminate()
                except Exception: pass
    avg = sum(rewards) / len(rewards) if rewards else None
    return task, avg, len(rewards), f"rewards={rewards}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo"); ap.add_argument("task_list")
    ap.add_argument("--runs", type=int, default=8)
    ap.add_argument("--workers", type=int, default=20)
    ap.add_argument("--max-steps", type=int, default=30)
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--state", default="_local/eval_avg8_done.txt")
    ap.add_argument("--out", default="_local/eval_avg8_scores.tsv")
    a = ap.parse_args()
    key = load_key()
    names = [t for t in open(a.task_list).read().split() if t]
    done = set()
    if os.path.isfile(a.state):
        done = {l.split("\t")[0] for l in open(a.state) if l.strip()}
    todo = [t for t in names if t not in done
            and os.path.isdir(os.path.join(a.repo, "tasks", t, "environment"))]
    if a.limit:
        todo = todo[:a.limit]
    print(f"[eval avg@{a.runs}] {len(todo)} tasks x {a.workers} workers, max_steps={a.max_steps}, "
          f"Opus-4.8 on Modal. Billed: Opus->API key, sandboxes->Modal.", flush=True)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    app = modal.App.lookup(APP, create_if_missing=True)
    sf = open(a.state, "a"); of = open(a.out, "a"); n = 0; t0 = time.time()
    with cf.ThreadPoolExecutor(a.workers) as ex:
        futs = [ex.submit(eval_task, key, app, a.repo, t, a.runs, a.max_steps, a.timeout) for t in todo]
        for f in cf.as_completed(futs):
            task, avg, k, detail = f.result(); n += 1
            with lock:
                sf.write(f"{task}\t{avg}\t{k}\t{detail}\n"); sf.flush()
                of.write(f"{task}\t{avg}\t{k}\t{detail}\n"); of.flush()
            print(f"  [{n}/{len(todo)}] {task} avg@{k}={avg} {detail[:50]}", flush=True)
    print(f"DONE in {(time.time()-t0)/60:.1f} min -> {a.out}", flush=True)


if __name__ == "__main__":
    main()
