#!/usr/bin/env python3
"""Behavioral gate (OPT-IN) — oracle / no-op / reward-isolation by RUNNING the task.

This is the one check that EXECUTES the task, so it catches the defect static and
semantic review cannot: "the verifier doesn't actually require the fix." Every
client's #1 complaint (MAI no-op, GDM oracle, Reflection null-agent) lives here.

It is EXPENSIVE (builds a Docker image and runs the verifier per task, minutes each)
and it is the only part of this skill that runs anything — so it is strictly
**opt-in and confirm-to-run**:
  - by default it does NOTHING but print the Docker plan (a dry run);
  - it only executes when you pass **`--execute`**.
Run it targeted (a few flagged tasks or a sample), never the whole set as a habit;
the authoritative version is the client's delivery-stage gate (harbor + Modal).

Three trials per task (all read environment/ + tests/ + solution/):
  - no-op   : build the image, run the verifier on the UNTOUCHED container.
              Must FAIL. If it PASSES -> `no-op-passes` (the verifier is vacuous).
  - oracle  : fresh container, run solution/solve.sh, then the verifier.
              Must PASS. If it FAILS -> `oracle-fails` (reference can't solve its
              own task, or an env/harbor-conversion defect).
  - reward-isolation (--reward-iso): before the verifier, write a fake passing
              reward/score file; the verifier must still FAIL. If it PASSES ->
              `reward-signal-gameable` (agent could just write the reward).

NOTE: this is the single-container approximation of harbor's *separate-verifier*
model — enough to catch the dominant no-op/oracle defects pre-delivery. The
verifier invocation (`--verifier-cmd`) may need to match your harness; default
runs tests/test.sh.

Usage:
    # plan only (safe, runs nothing):
    python check_behavioral.py <tasks-dir> --only name1,name2
    # actually run (opt-in):
    python check_behavioral.py <tasks-dir> --only name1,name2 --execute [--reward-iso]

Emits findings with area="behavioral".
"""
import argparse
import os
import re
import shutil
import subprocess
import tempfile

from common import (FAIL, PASS, WARN, finding, emit,
                    discover_tasks, task_paths, read_text)


def _docker_ok():
    return shutil.which("docker") is not None


def _run(cmd, timeout):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    except Exception as e:
        return 1, str(e)


def _verifier_passed(rc, out):
    """Pass = verifier exit 0 and no pytest/reward failure signature."""
    if rc != 0:
        return False
    if re.search(r"\b\d+ failed\b|FAILED|AssertionError|reward.{0,8}0\b", out):
        return False
    return True


def _trial_cmd(mode, wd, verifier):
    steps = [f"cp -r /tests/. {wd}/tests/ 2>/dev/null || true"]
    if mode == "oracle":
        steps = [f"cd {wd} && bash /solution/solve.sh"] + steps
    if mode == "reward-iso":
        steps += [f"echo 1 > {wd}/reward.txt; echo 1 > {wd}/score; "
                  f"echo pass > {wd}/status 2>/dev/null || true"]
    steps += [f"cd {wd} && {verifier}"]
    return " ; ".join(steps)


def plan_task(name, root, args):
    p = task_paths(root)
    tag = "qcbeh-" + re.sub(r"[^a-z0-9]+", "-", name.lower())[:40]
    modes = ["no-op", "oracle"] + (["reward-iso"] if args.reward_iso else [])
    cmds = [f"docker build -t {tag} {p['environment']}"]
    for m in modes:
        cmds.append(f"docker run --rm -v {p['tests']}:/tests:ro -v {p['solution']}:/solution:ro "
                    f"{tag} bash -lc '{_trial_cmd(m, args.workdir, args.verifier_cmd)}'")
    return [finding(name, "behavioral", PASS, "behavioral-plan",
                    detail=" || ".join(cmds), location=root)]


def run_task(name, root, args):
    p = task_paths(root)
    env_dir, tests_dir, sol_dir = p["environment"], p["tests"], p["solution"]
    if not (os.path.isdir(env_dir) and os.path.isdir(tests_dir)):
        return [finding(name, "behavioral", WARN, "behavioral-skipped",
                        detail="missing environment/ or tests/ — cannot run.", location=root)]
    tag = "qcbeh-" + re.sub(r"[^a-z0-9]+", "-", name.lower())[:40]
    out = []
    build_cmd = ["docker", "build", "-q", "-t", tag, env_dir]
    if getattr(args, "native_arch", False):
        # strip the `FROM --platform=linux/amd64` pin so the image builds for the
        # host arch — avoids glacial qemu emulation off-amd64. Keeps the original
        # build context; results are arch-indicative for arch-sensitive tasks.
        stripped = re.sub(r"--platform=\S+\s*", "", read_text(os.path.join(env_dir, "Dockerfile")))
        tmp_df = os.path.join(tempfile.gettempdir(), tag + ".Dockerfile")
        with open(tmp_df, "w") as f:
            f.write(stripped)
        build_cmd = ["docker", "build", "-q", "-f", tmp_df, "-t", tag, env_dir]
    # build gets its own (longer) budget — an image build (apt/pip) takes minutes,
    # while --timeout stays short so a blocking verifier in a trial dies fast.
    rc, log = _run(build_cmd, args.build_timeout)
    if rc != 0:
        return [finding(name, "behavioral", FAIL, "build-fails",
                        detail=f"`docker build` failed: {log[-300:]}",
                        location="environment/Dockerfile",
                        fix="Fix the Dockerfile so the agent image builds.")]

    def trial(mode):
        return _run(["docker", "run", "--rm", "-v", f"{tests_dir}:/tests:ro",
                     "-v", f"{sol_dir}:/solution:ro", tag, "bash", "-lc",
                     _trial_cmd(mode, args.workdir, args.verifier_cmd)], args.timeout)

    nrc, nlog = trial("no-op")
    if _verifier_passed(nrc, nlog):
        out.append(finding(name, "behavioral", FAIL, "no-op-passes",
                           detail="verifier PASSES on the untouched container — it does not "
                                  "require the agent's work (no-op defect).",
                           location="tests/", fix="Make the verifier assert the actual "
                           "post-solution state; confirm no-op now scores 0."))
    orc, olog = trial("oracle")
    if not _verifier_passed(orc, olog):
        out.append(finding(name, "behavioral", FAIL, "oracle-fails",
                           detail=f"verifier FAILS after running solution/solve.sh (rc={orc}) — "
                                  f"the reference doesn't pass its own verifier: {olog[-200:]}",
                           location="solution/solve.sh", fix="Fix solve.sh or the "
                           "verifier/environment so the oracle scores 1.0."))
    if args.reward_iso:
        rrc, rlog = trial("reward-iso")
        if _verifier_passed(rrc, rlog):
            out.append(finding(name, "behavioral", FAIL, "reward-signal-gameable",
                               detail="verifier PASSES when a fake reward/score/status file is "
                                      "written — the pass signal is agent-writable.",
                               location="tests/", fix="Compute the verdict from real task state, "
                               "never from a file the agent can write."))
    _run(["docker", "rmi", "-f", tag], 60)
    if not out:
        out.append(finding(name, "behavioral", PASS, "behavioral-ok",
                           detail="oracle scored 1.0 and no-op scored 0."))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks")
    ap.add_argument("--only", default="", help="comma-separated task names to run")
    ap.add_argument("--execute", action="store_true",
                    help="ACTUALLY build+run in Docker (expensive). Without this, prints the plan only.")
    ap.add_argument("--reward-iso", action="store_true", help="also run the reward-isolation trial")
    ap.add_argument("--native-arch", action="store_true",
                    help="strip the FROM --platform pin and build for the host arch "
                         "(fast off-amd64; results are arch-indicative for arch-sensitive tasks)")
    ap.add_argument("--verifier-cmd", default="bash /tests/test.sh",
                    help="how to invoke the verifier inside the container")
    ap.add_argument("--workdir", default="/app")
    ap.add_argument("--timeout", type=int, default=600, help="per-trial cap (s); keep short to kill blocking verifiers")
    ap.add_argument("--build-timeout", type=int, default=600, help="docker build cap (s); builds take minutes")
    ap.add_argument("--no-resume", dest="resume", action="store_false",
                    help="ignore any existing --out file and re-run all tasks from scratch")
    ap.add_argument("--out", default="findings_behavioral.json")
    args = ap.parse_args()

    only = {s for s in args.only.split(",") if s}
    tasks = [(n, r) for n, r in discover_tasks(args.tasks) if not only or n in only]

    if not args.execute:
        print(f"[behavioral] PLAN ONLY — {len(tasks)} task(s). This gate RUNS the task in "
              f"Docker (expensive); nothing has run. Re-run with --execute to actually run.")
        findings = []
        for name, root in tasks:
            findings.extend(plan_task(name, root, args))
        emit(findings, args.out)
        return

    if not _docker_ok():
        raise SystemExit("docker not found — start colima/Docker, or drop --execute for the plan.")
    print(f"[behavioral] EXECUTING {len(tasks)} task(s) in Docker (targeted/expensive)…")
    # Resume-friendly: keep findings for tasks already recorded in args.out (a prior
    # interrupted run), and skip re-running them. Pass --no-resume to start clean.
    findings, done = [], set()
    if getattr(args, "resume", True) and os.path.isfile(args.out):
        try:
            findings = [f for f in __import__("json").load(open(args.out)) if f.get("task")]
            done = {f["task"] for f in findings}
        except Exception:
            findings, done = [], set()
    for i, (name, root) in enumerate(tasks, 1):
        if name in done:
            print(f"  [{i}/{len(tasks)}] {name}: (already recorded, skipping)")
            continue
        findings.extend(run_task(name, root, args))
        # persist after EACH task so a sleep/kill can't wipe completed work
        emit(findings, args.out)
        print(f"  [{i}/{len(tasks)}] {name}: {[f['title'] for f in findings if f['task']==name]}")
    fails = sum(1 for f in findings if f["severity"] == FAIL)
    print(f"[behavioral] {len(findings)} findings, {fails} FAIL -> {args.out}")


if __name__ == "__main__":
    main()
