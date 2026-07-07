#!/usr/bin/env python3
"""Oracle/no-op gate on Modal (mercor-data-delivery workspace).

Per task: build its environment/Dockerfile as a Modal image (native amd64 — truer
than local arm64), attach tests/ + solution/, create a Sandbox, then run:
  1. no-op  : bash /tests/test.sh on the untouched container  -> must FAIL
  2. oracle : bash /solution/solve.sh, then bash /tests/test.sh -> must PASS
(no-op runs first so the oracle's mutations can't help it; test.sh side effects
before solve are limited to /logs + copying test files, same as the local gate's
single-container approximation.)

Same state/result format as oracle_gate.py -> fully resumable across both.

Usage: modalenv/bin/python modal_gate.py <repo> <task_list> [--workers 48]
       [--state ...oracle_done.txt] [--out ...oracle_results.txt]
"""
import argparse, concurrent.futures as cf, os, threading, time

import modal

lock = threading.Lock()
APP_NAME = "r35k-oracle-gate"


def gate_one(app, repo, task, timeout):
    tdir = os.path.join(repo, "tasks", task)
    img = (modal.Image.from_dockerfile(
                os.path.join(tdir, "environment", "Dockerfile"),
                context_dir=os.path.join(tdir, "environment"))
           .add_local_dir(os.path.join(tdir, "tests"), remote_path="/tests")
           .add_local_dir(os.path.join(tdir, "solution"), remote_path="/solution"))
    sb = None
    try:
        sb = modal.Sandbox.create(app=app, image=img, timeout=timeout,
                                  cpu=2, memory=4096)
        script = (
            "mkdir -p /logs 2>/dev/null; "
            "bash /tests/test.sh >/tmp/noop.log 2>&1; NRC=$?; "
            "bash /solution/solve.sh >/tmp/solve.log 2>&1; SRC=$?; "
            "bash /tests/test.sh >/tmp/oracle.log 2>&1; ORC=$?; "
            "echo \"NRC=$NRC SRC=$SRC ORC=$ORC\"; "
            "echo '--- oracle tail ---'; tail -c 600 /tmp/oracle.log"
        )
        p = sb.exec("bash", "-lc", script)
        out = p.stdout.read()
        p.wait()
        import re
        m = re.search(r"NRC=(\d+) SRC=(\d+) ORC=(\d+)", out)
        if not m:
            return "EXEC-ERROR", out[-160:].replace("\n", " ")
        nrc, src, orc = map(int, m.groups())
        tail = out.split("--- oracle tail ---", 1)[-1][-160:].replace("\n", " ")
        if orc != 0:
            return "ORACLE-FAIL", f"solve_rc={src} " + tail
        if nrc == 0:
            return "NOOP-PASS", ""
        return "OK", ""
    except Exception as e:
        msg = str(e)[-160:].replace("\n", " ")
        if "image build" in msg.lower() or "Build" in type(e).__name__:
            return "BUILD-FAIL", msg
        return "EXC", msg
    finally:
        if sb is not None:
            try:
                sb.terminate()
            except Exception:
                pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo"); ap.add_argument("tasks")
    ap.add_argument("--workers", type=int, default=48)
    ap.add_argument("--timeout", type=int, default=1500)
    ap.add_argument("--state", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    repo = os.path.abspath(a.repo)
    app = modal.App.lookup(APP_NAME, create_if_missing=True)
    done = set(open(a.state).read().split()) if os.path.exists(a.state) else set()
    tasks = [t for t in open(a.tasks).read().split() if t and t not in done]
    print(f"{len(tasks)} to gate on Modal ({len(done)} already done)", flush=True)
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
            if kind != "OK":
                print(f"[{kind}] {task}: {detail[:100]}", flush=True)
            if i % 25 == 0 or i == len(futs):
                rate = i / (time.time() - t0) * 60
                print(f"[{i}/{len(futs)}] {counts} ({rate:.0f}/min)", flush=True)
    print("DONE", counts)


if __name__ == "__main__":
    main()
