#!/usr/bin/env python3
"""Local oracle/no-op runner — a minimal harbor-equivalent built on plain Docker.

`harbor` (the Mercor runner) is internal and not pip-installable, but a TB2 task
fully specifies its own run contract, so we reconstruct it with `docker`:

  build:   docker build -f environment/Dockerfile  (context = environment/)
  oracle:  start container -> copy solution/ in -> run solution/solve.sh
           -> overlay tests/ onto /tests -> run tests/test.sh -> read reward
  no-op:   start container -> overlay tests/ -> run tests/test.sh -> read reward
           (no solve.sh applied)

Tests are COPIED into /tests (not bind-mounted) so build-time-generated content
under /tests (e.g. mutated_raw/) is preserved — matching harbor's overlay.

Reward = `/logs/verifier/reward.txt` if test.sh writes it, else exit-code (0 ->
1.0, non-zero -> 0.0). Same verdict rules and finding titles as
behavioral_gates.py, so results aggregate together.

Needs a working Docker daemon (e.g. `colima start`). Emits area="behavioral".

Usage:
    python local_harbor.py <tasks-dir> --runs 3 \
        --log-dir qc_out/behavioral_logs --out qc_out/findings_behavioral.json
"""
import argparse
import os
import re
import subprocess
import sys

from common import FAIL, WARN, PASS, finding, emit, discover_tasks

SCORE_RE = re.compile(r"(\d+)\s+passed")
FAIL_RE = re.compile(r"(\d+)\s+failed")


def sh(cmd, timeout=1800, log=None, env=None):
    """Run a command; return (exit_code, combined_output)."""
    runenv = None
    if env:
        runenv = dict(os.environ)
        runenv.update(env)
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, timeout=timeout, env=runenv)
        out = p.stdout
        rc = p.returncode
    except subprocess.TimeoutExpired as e:
        out = (e.output or "") + "\n[TIMEOUT]"
        rc = 124
    except FileNotFoundError:
        return 127, "docker not found on PATH"
    if log:
        with open(log, "a") as f:
            f.write("$ " + " ".join(cmd) + f"\n{out}\n(exit {rc})\n\n")
    return rc, out


def docker_ok():
    rc, _ = sh(["docker", "info"], timeout=30)
    return rc == 0


def build(task_dir, tag, platform, log):
    env = os.path.join(task_dir, "environment")
    df = os.path.join(env, "Dockerfile")
    # Legacy builder (DOCKER_BUILDKIT=0) loads the image into the run store;
    # under colima's containerd snapshotter, BuildKit needs --load and otherwise
    # leaves `docker run` unable to find the image. Platform is pinned in the
    # task's Dockerfile FROM, but we pass --platform too for tasks that don't.
    cmd = ["docker", "build", "--platform", platform, "-t", tag, "-f", df, env]
    rc, out = sh(cmd, timeout=2400, log=log, env={"DOCKER_BUILDKIT": "0"})
    return rc == 0, out


def reward_from(container, exit_code, log):
    rc, out = sh(["docker", "exec", container, "cat", "/logs/verifier/reward.txt"],
                 timeout=30, log=log)
    if rc == 0 and out.strip() in ("0", "1"):
        return float(out.strip())
    return 1.0 if exit_code == 0 else 0.0


def one_run(task_dir, tag, platform, apply_solution, log, run_timeout):
    """Run oracle (apply_solution=True) or no-op once; return score or None."""
    cname = "qc_" + os.path.basename(task_dir.rstrip("/")) + ("_o" if apply_solution else "_n")
    cname = re.sub(r"[^a-zA-Z0-9_.-]", "_", cname)
    sh(["docker", "rm", "-f", cname], timeout=60)
    # start a long-lived container (CMD overridden, mirroring client infra).
    # No --platform here: the built image is already the right platform, and
    # passing --platform on `run` makes docker re-resolve and attempt a pull.
    rc, out = sh(["docker", "run", "-d", "--name", cname,
                  tag, "sleep", "infinity"], timeout=120, log=log)
    if rc != 0:
        return None, "container start failed"
    try:
        if apply_solution:
            sh(["docker", "exec", cname, "mkdir", "-p", "/solution"], log=log)
            rc, _ = sh(["docker", "cp", os.path.join(task_dir, "solution") + "/.",
                        f"{cname}:/solution/"], log=log)
            rc, _ = sh(["docker", "exec", cname, "bash", "/solution/solve.sh"],
                       timeout=run_timeout, log=log)
            if rc != 0:
                # oracle solution itself errored — still run verifier to capture score
                pass
        # overlay tests onto /tests (preserve build-generated content)
        sh(["docker", "exec", cname, "mkdir", "-p", "/tests"], log=log)
        sh(["docker", "cp", os.path.join(task_dir, "tests") + "/.",
            f"{cname}:/tests/"], log=log)
        rc, out = sh(["docker", "exec", cname, "bash", "/tests/test.sh"],
                     timeout=run_timeout, log=log)
        score = reward_from(cname, rc, log)
        return score, None
    finally:
        sh(["docker", "rm", "-f", cname], timeout=60)


def gate_task(name, task_dir, runs, platform, log_dir, run_timeout):
    out = []
    tdir = os.path.join(log_dir, name)
    os.makedirs(tdir, exist_ok=True)
    blog = os.path.join(tdir, "build.log")
    tag = "qc_" + re.sub(r"[^a-z0-9_.-]", "_", name.lower())

    print(f"  building {name} ...", flush=True)
    ok, _ = build(task_dir, tag, platform, blog)
    if not ok:
        return [finding(name, "behavioral", FAIL, "build-failed",
                        detail="docker build failed — task does not build on this toolchain.",
                        location=f"{blog}",
                        fix="Inspect the build log; fix the Dockerfile / missing COPY sources.")]

    oracle, nop = [], []
    for i in range(runs):
        print(f"  oracle run {i+1}/{runs} ...", flush=True)
        s, err = one_run(task_dir, tag, platform, True,
                         os.path.join(tdir, f"oracle_{i+1}.log"), run_timeout)
        if err:
            out.append(finding(name, "behavioral", FAIL, "oracle-run-error",
                               detail=f"oracle run {i+1}: {err}.",
                               location=f"{tdir}/oracle_{i+1}.log", fix="Inspect the log."))
        else:
            oracle.append(s)
    for i in range(runs):
        print(f"  no-op run {i+1}/{runs} ...", flush=True)
        s, err = one_run(task_dir, tag, platform, False,
                         os.path.join(tdir, f"nop_{i+1}.log"), run_timeout)
        if err:
            out.append(finding(name, "behavioral", FAIL, "nop-run-error",
                               detail=f"no-op run {i+1}: {err}.",
                               location=f"{tdir}/nop_{i+1}.log", fix="Inspect the log."))
        else:
            nop.append(s)

    if oracle:
        if all(abs(s - 1.0) < 1e-9 for s in oracle):
            out.append(finding(name, "behavioral", PASS, "oracle-passes",
                               detail=f"oracle scored 1.0 on {len(oracle)}/{runs} runs (docker)."))
        elif len(set(oracle)) > 1:
            out.append(finding(name, "behavioral", FAIL, "oracle-flaky",
                               detail=f"oracle scores inconsistent: {oracle} (docker).",
                               location=f"{tdir}/",
                               fix="Remove non-determinism (sleeps, unseeded randomness, races)."))
        else:
            out.append(finding(name, "behavioral", FAIL, "oracle-fails",
                               detail=f"oracle scored {oracle[0]} (expected 1.0) (docker). "
                                      "Broken solve path or unfair verifier.",
                               location=f"{tdir}/oracle_1.log",
                               fix="Fix solution/solve.sh or the verifier until oracle=1.0."))
    if nop:
        if all(abs(s) < 1e-9 for s in nop):
            out.append(finding(name, "behavioral", PASS, "nop-fails",
                               detail=f"no-op scored 0.0 on {len(nop)}/{runs} runs (docker)."))
        else:
            out.append(finding(name, "behavioral", FAIL, "nop-passes",
                               detail=f"no-op scored {nop} (expected 0.0) (docker). "
                                      "Tests pass without the fix — non-verifying tests / reward leak.",
                               location=f"{tdir}/nop_1.log",
                               fix="Tighten vacuously-passing checks; each test must require agent work."))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--platform", default="linux/amd64")
    ap.add_argument("--log-dir", default="behavioral_logs")
    ap.add_argument("--out", default="findings_behavioral.json")
    ap.add_argument("--run-timeout", type=int, default=1800)
    args = ap.parse_args()
    os.makedirs(args.log_dir, exist_ok=True)

    if not docker_ok():
        sys.exit("Docker daemon not reachable. Start it first (e.g. `colima start`).")

    findings = []
    for name, root in discover_tasks(args.tasks):
        print(f"[local_harbor] {name}", flush=True)
        findings.extend(gate_task(name, root, args.runs, args.platform,
                                  args.log_dir, args.run_timeout))
    n = emit(findings, args.out)
    fails = sum(1 for f in findings if f["severity"] == FAIL)
    print(f"[local_harbor] {n} findings, {fails} FAIL -> {args.out} (logs in {args.log_dir}/)")


if __name__ == "__main__":
    main()
