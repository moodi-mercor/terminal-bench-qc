#!/usr/bin/env python3
"""Smoke: one native-Harbor task, Terminus-2 + GLM-5.2 (Vercel gateway), Modal,
force_ephemeral_modal=True so the sandbox is built fresh with our local keys
(the deployed app lacks them -> that was the 401). Prints the reward score.

Run with the lighthouse venv, .env sourced:
  cd code-qa-evals/benchmark-code-qa-ext && set -a && . ./.env && set +a
  .venv/bin/python /path/to/glm_harbor_smoke.py <task_dir>
"""
import asyncio, sys
from pathlib import Path
from lighthouse.core.options import HarnessExecutionOptions, SandboxType
from lighthouse.harnesses.harbor.adapter import HarborAdapter
from lighthouse.run_harbor_eval import _build_native_task, _resolve_single_task_dir

MODEL = "vercel_ai_gateway/zai/glm-5.2"


async def main():
    task_dir = _resolve_single_task_dir(sys.argv[1])
    task = _build_native_task(task_dir)
    opts = HarnessExecutionOptions(
        model=MODEL,
        sandbox_type=SandboxType.MODAL,
        run_rubric=False,
        analyze_trajectory=False,
        force_ephemeral_modal=True,
    )
    print(f"TASK {task_dir.name} | MODEL {MODEL} | modal(ephemeral) | Terminus-2", flush=True)
    res = await HarborAdapter().run_execution(task=task, options=opts)
    score = getattr(res.test_summary, "score", None) if res.test_summary else None
    print(f"STATUS={res.status} SCORE={score}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
