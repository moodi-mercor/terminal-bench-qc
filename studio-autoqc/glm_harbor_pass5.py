#!/usr/bin/env python3
"""GLM-5.2 pass@N over native-Harbor terminal tasks, on our own Modal (no Studio).

Terminus-2 agent + GLM-5.2 (vercel_ai_gateway/zai/glm-5.2), Modal ephemeral sandbox
(force_ephemeral_modal=True so our local Vercel keys reach the sandbox — otherwise 401).

Each attempt is wrapped in a HARD timeout so a hung/slow GLM call can't block a slot
forever (that was the smoke-test failure). Only attempts that finish with a real reward
score count as "genuine"; timeouts/errors/None are retried up to runs*cap.

Score convention: Reflection tasks grade via reward (1 pass / 0 fail). pass = score > 0.
bo5 = number of the N genuine attempts that passed. Strong/weak split: GLM bo5 < 3.

Run in the lighthouse venv with .env sourced (Vercel keys):
  cd code-qa-evals/benchmark-code-qa-ext && set -a && . ./.env && set +a
  .venv/bin/python glm_harbor_pass5.py --base <tasks_dir> --tasks names.txt \
      --runs 5 --conc 20 --timeout 1200 --out <outdir> --execute
Resumable via <outdir>/state.json.
"""
import argparse, asyncio, json, os, sys, time, traceback
from pathlib import Path

import litellm
from lighthouse.core.options import HarnessExecutionOptions, SandboxType
from lighthouse.harnesses.harbor.adapter import HarborAdapter
from lighthouse.run_harbor_eval import _build_native_task

MODEL = "vercel_ai_gateway/zai/glm-5.2"

# The Vercel GLM gateway intermittently 401s / rate-limits under concurrency (verified:
# clean at conc<=4, ~33% 401 + queuing at conc 12). harbor treats a single 401 as fatal
# to the whole rollout, so wrap litellm.acompletion (harbor calls it by module attribute)
# to retry transient failures with backoff. A genuinely bad key still fails after N tries.
_RETRY_EXC = tuple(e for e in (
    getattr(litellm, "AuthenticationError", None),
    getattr(litellm, "RateLimitError", None),
    getattr(litellm, "Timeout", None),
    getattr(litellm, "APIConnectionError", None),
    getattr(litellm, "ServiceUnavailableError", None),
    getattr(litellm, "InternalServerError", None),
    getattr(litellm, "APIError", None),
) if isinstance(e, type))
_orig_acompletion = litellm.acompletion
_CALL_TIMEOUT = float(os.environ.get("GLM_CALL_TIMEOUT", "120"))


async def _acompletion_retry(*args, **kwargs):
    # Bound each call: the gateway serves most calls in <10s but a ~20% minority hang
    # (queue indefinitely). Cap at _CALL_TIMEOUT and retry — hung calls succeed fast on
    # retry, so one straggler can't stall the whole rollout. Also retries 401/rate-limit.
    kwargs.setdefault("timeout", _CALL_TIMEOUT)
    delay, last = 2.0, None
    for _ in range(10):
        try:
            return await asyncio.wait_for(
                _orig_acompletion(*args, **kwargs), timeout=_CALL_TIMEOUT + 15
            )
        except (asyncio.TimeoutError, *_RETRY_EXC) as e:
            last = e
            await asyncio.sleep(delay)
            delay = min(delay * 1.6, 20)
    raise last if last else RuntimeError("acompletion retries exhausted")


litellm.acompletion = _acompletion_retry


async def one_attempt(task_dir: Path, timeout: int, max_steps: int):
    """Run one Terminus-2 + GLM-5.2 rollout on Modal. Returns (verdict, score).
    verdict: 'genuine' (finished, has reward) | 'timeout' | 'error'.
    max_steps caps Terminus-2 turns (adapter maps max_steps->max_turns) so an unsolved
    weak-model rollout terminates and the verifier scores it (bounded-effort fail = 0)."""
    opts = HarnessExecutionOptions(
        model=MODEL, sandbox_type=SandboxType.MODAL,
        run_rubric=False, analyze_trajectory=False, force_ephemeral_modal=True,
        max_steps=max_steps,
    )
    task = _build_native_task(task_dir)
    try:
        res = await asyncio.wait_for(
            HarborAdapter().run_execution(task=task, options=opts), timeout=timeout
        )
        score = getattr(res.test_summary, "score", None) if res.test_summary else None
        if score is None:
            return "error", None
        return "genuine", float(score)
    except asyncio.TimeoutError:
        return "timeout", None
    except Exception as e:
        sys.stderr.write(f"[attempt-error] {task_dir.name}: {type(e).__name__}: {str(e)[:160]}\n")
        return "error", None


def load_state(path, tasks):
    s = json.load(open(path)) if os.path.isfile(path) else {}
    s.setdefault("genuine", {}); s.setdefault("attempts", {})
    for t in tasks:
        s["genuine"].setdefault(t, []); s["attempts"].setdefault(t, 0)
    return s


def save_state(path, s):
    json.dump(s, open(path, "w"))


def deficit(s, tasks, runs, cap):
    """Tasks still needing genuine attempts (not yet `runs`, not past the attempt cap)."""
    d = {}
    for t in tasks:
        if len(s["genuine"][t]) >= runs:
            continue
        if s["attempts"][t] >= runs * cap:
            continue
        d[t] = runs - len(s["genuine"][t])
    return d


async def run(base, names, runs, conc, timeout, cap, out, max_steps):
    """Streaming pool: keep `conc` rollouts in flight continuously. The instant one
    finishes, a worker picks the next task still short of `runs` genuine attempts (fewest
    genuine+in-flight first) and starts a new rollout — no batch barrier, so a slow
    straggler never idles the other slots. State saved per completion (resumable)."""
    import collections
    os.makedirs(out, exist_ok=True)
    state_path = f"{out}/state.json"
    tasks = names
    s = load_state(state_path, tasks)
    lock = asyncio.Lock()
    inflight = collections.Counter()
    t0 = time.time()
    counters = {"g": 0, "to": 0, "er": 0}

    def pick():
        """Next task needing coverage (genuine+in-flight < runs), under the attempt cap."""
        best, bestcov = None, None
        for t in tasks:
            if len(s["genuine"][t]) >= runs or s["attempts"][t] >= runs * cap:
                continue
            cov = len(s["genuine"][t]) + inflight[t]
            if cov >= runs:
                continue
            if bestcov is None or cov < bestcov:
                best, bestcov = t, cov
        return best

    async def worker():
        while True:
            async with lock:
                name = pick()
                if name is None:
                    if sum(inflight.values()) == 0:
                        return  # nothing left and nothing in flight -> done
                    wait = True
                else:
                    inflight[name] += 1
                    wait = False
            if wait:
                await asyncio.sleep(5)
                continue
            verdict, score = await one_attempt(Path(base) / name, timeout, max_steps)
            async with lock:
                inflight[name] -= 1
                s["attempts"][name] += 1
                if verdict == "genuine":
                    counters["g"] += 1
                    if len(s["genuine"][name]) < runs:
                        s["genuine"][name].append(score)
                else:
                    counters["timeout" if verdict == "timeout" else "er"] = \
                        counters.get("timeout" if verdict == "timeout" else "er", 0) + 1
                save_state(state_path, s)
                done = sum(1 for t in tasks if len(s["genuine"][t]) >= runs)
                tot = sum(len(v) for v in s["genuine"].values())
            print(f"  {name[:44]:44} {verdict:8} score={score} | "
                  f"rollouts={tot} tasks5of5={done}/{len(tasks)} "
                  f"({(time.time()-t0)/60:.0f}m)", flush=True)

    await asyncio.gather(*[worker() for _ in range(conc)])

    # summary: bo{runs} per task = # genuine attempts that passed (score>0)
    summary = {}
    for t in tasks:
        scores = s["genuine"][t]
        summary[t] = {"n_genuine": len(scores), "boN_pass": sum(1 for x in scores if x > 0),
                      "scores": scores, "attempts": s["attempts"][t]}
    json.dump(summary, open(f"{out}/summary.json", "w"), indent=1)
    settled = sum(1 for t in tasks if len(s["genuine"][t]) >= runs)
    print(f"DONE | {settled}/{len(tasks)} tasks reached {runs} genuine | summary -> {out}/summary.json", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="dir containing task folders")
    ap.add_argument("--tasks", required=True, help="file: one task folder-name per line")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--conc", type=int, default=20)
    ap.add_argument("--timeout", type=int, default=1200, help="hard per-attempt timeout (s)")
    ap.add_argument("--max-steps", type=int, default=60, help="cap Terminus-2 turns (max_turns)")
    ap.add_argument("--cap", type=int, default=4, help="give up a task after runs*cap attempts")
    ap.add_argument("--out", required=True)
    ap.add_argument("--execute", action="store_true")
    a = ap.parse_args()
    names = [l.strip() for l in open(a.tasks) if l.strip()]
    missing = [n for n in names if not (Path(a.base) / n / "task.toml").exists()]
    if missing:
        print(f"WARN {len(missing)} task names have no task.toml under {a.base} (e.g. {missing[:3]})", flush=True)
        names = [n for n in names if n not in set(missing)]
    print(f"[pass{a.runs}] {len(names)} tasks | model={MODEL} | modal ephemeral | out={a.out}", flush=True)
    print(f"  max_steps(turns)={a.max_steps} conc={a.conc} timeout={a.timeout}s cap={a.runs*a.cap}", flush=True)
    if not a.execute:
        print("DRY-RUN (pass --execute)"); return
    asyncio.run(run(a.base, names, a.runs, a.conc, a.timeout, a.cap, a.out, a.max_steps))


if __name__ == "__main__":
    main()
