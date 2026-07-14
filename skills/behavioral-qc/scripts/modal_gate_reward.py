#!/usr/bin/env python3
"""Reward-file-aware oracle/no-op gate on Modal (Reflection/nexus convention).

Reflection tasks grade via /logs/tests/reward.txt (test.sh writes 0/1 and often
`exit 0` regardless), NOT via test.sh's exit code. Judging by exit code (modal_gate.py)
misreads always-exit-0 tasks: a genuine no-op failure looks like NOOP-PASS, and a
genuine oracle failure can hide inside OK. This gate reads reward.txt as the source of
truth (exit codes kept only as diagnostics).

Per task, in one fresh container:
  1. no-op  : bash /tests/test.sh on the untouched container -> capture reward NR (must be 0)
  2. oracle : bash /solution/solve.sh, then bash /tests/test.sh -> capture reward OR (must be 1)

Verdict (reward-based):
  OR != 1          -> ORACLE-FAIL     (reference solution does not score the task)
  NR == 1          -> NOOP-PASS       (untouched container already scores -> real leak/vacuous)
  else             -> OK

Resumable via the state file. Same out format: <task>\t<kind>\t<detail>.
Usage: modalenv/bin/python modal_gate_reward.py <repo> <task_list> --state S --out O [--workers 300]
"""
import argparse, concurrent.futures as cf, os, re, threading, time

import modal

lock = threading.Lock()
APP_NAME = "refl-reward-gate"


def gate_one(app, repo, task, timeout):
    tdir = os.path.join(repo, "tasks", task)
    img = (modal.Image.from_dockerfile(
                os.path.join(tdir, "environment", "Dockerfile"),
                context_dir=os.path.join(tdir, "environment"))
           .add_local_dir(os.path.join(tdir, "tests"), remote_path="/tests")
           .add_local_dir(os.path.join(tdir, "solution"), remote_path="/solution"))
    sb = None
    try:
        sb = modal.Sandbox.create(app=app, image=img, timeout=timeout, cpu=2, memory=4096)
        script = (
            "mkdir -p /logs/tests 2>/dev/null; "
            "bash /tests/test.sh >/tmp/noop.log 2>&1; NRC=$?; "
            "NR=$(cat /logs/tests/reward.txt 2>/dev/null | tr -dc '0-9.'); "
            "rm -f /logs/tests/reward.txt; "
            "bash /solution/solve.sh >/tmp/solve.log 2>&1; SRC=$?; "
            "bash /tests/test.sh >/tmp/oracle.log 2>&1; ORC=$?; "
            "OR=$(cat /logs/tests/reward.txt 2>/dev/null | tr -dc '0-9.'); "
            "echo \"NR=[$NR] OR=[$OR] NRC=$NRC SRC=$SRC ORC=$ORC\"; "
            "echo '--- oracle tail ---'; tail -c 500 /tmp/oracle.log | LC_ALL=C tr -c '\11\12\15\40-\176' '?'"
        )
        p = sb.exec("bash", "-lc", script)
        out = p.stdout.read()
        p.wait()
        m = re.search(r"NR=\[([\d.]*)\] OR=\[([\d.]*)\] NRC=(\d+) SRC=(\d+) ORC=(\d+)", out)
        if not m:
            return "EXEC-ERROR", out[-160:].replace("\n", " ")
        nr, orr, nrc, src, orc = m.groups()
        tail = out.split("--- oracle tail ---", 1)[-1][-140:].replace("\n", " ")
        def as1(x):
            try: return abs(float(x) - 1.0) < 1e-9
            except Exception: return False
        if not as1(orr):
            return "ORACLE-FAIL", f"reward={orr or 'MISSING'} solve_rc={src} orc={orc} " + tail
        if as1(nr):
            return "NOOP-PASS", f"noop_reward={nr}"
        return "OK", ""
    except Exception as e:
        msg = str(e)[-160:].replace("\n", " ")
        if "image build" in msg.lower() or "Build" in type(e).__name__:
            return "BUILD-FAIL", msg
        return "EXC", msg
    finally:
        if sb is not None:
            try: sb.terminate()
            except Exception: pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo"); ap.add_argument("tasks")
    ap.add_argument("--workers", type=int, default=300)
    ap.add_argument("--timeout", type=int, default=1500)
    ap.add_argument("--state", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    repo = os.path.abspath(a.repo)
    app = modal.App.lookup(APP_NAME, create_if_missing=True)
    done = set(open(a.state).read().split()) if os.path.exists(a.state) else set()
    tasks = [t for t in open(a.tasks).read().split() if t and t not in done]
    print(f"{len(tasks)} to gate (reward-aware) on Modal ({len(done)} already done)", flush=True)
    counts = {}
    t0 = time.time()
    with cf.ThreadPoolExecutor(a.workers) as ex:
        futs = {ex.submit(gate_one, app, repo, t, a.timeout): t for t in tasks}
        for i, f in enumerate(cf.as_completed(futs), 1):
            task = futs[f]
            try:
                kind, detail = f.result()
            except Exception as e:
                kind, detail = "EXC", str(e)[-120:]
            counts[kind] = counts.get(kind, 0) + 1
            with lock:
                open(a.state, "a").write(task + "\n")
                open(a.out, "a").write(f"{task}\t{kind}\t{detail}\n")
            if kind not in ("OK",):
                print(f"[{kind}] {task}: {detail[:100]}", flush=True)
            if i % 50 == 0 or i == len(futs):
                rate = i / (time.time() - t0) * 60
                print(f"[{i}/{len(futs)}] {counts} ({rate:.0f}/min)", flush=True)
    print("DONE", counts)


if __name__ == "__main__":
    main()
