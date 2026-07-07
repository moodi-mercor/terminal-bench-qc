#!/usr/bin/env python3
"""Capture full oracle failure logs (refactored tests) on Modal for the fail set.
Ensures pytest/jsonschema present so base-image artifacts don't masquerade as fails.
Writes one <task>.log per task into --logdir; re-classifies OK if it actually passes.

Usage: modalenv/bin/python modal_capture.py <repo> <task_list> --logdir <dir> [--workers 100]
"""
import argparse, concurrent.futures as cf, os, re, shlex, shutil, subprocess, tempfile, threading
import modal
lock = threading.Lock()

def old_tests_dir(repo, task):
    """Materialize origin/main tests/ for the task into a temp dir; return path or None."""
    tmp = tempfile.mkdtemp(prefix="oldt-")
    r = subprocess.run(["git", "-C", repo, "archive", "origin/main", f"tasks/{task}/tests"], capture_output=True)
    if r.returncode != 0:
        return None, tmp
    subprocess.run(["tar", "-x", "-C", tmp], input=r.stdout)
    p = os.path.join(tmp, "tasks", task, "tests")
    return (p if os.path.isdir(p) else None), tmp
RUN = ("mkdir -p /logs 2>/dev/null; "
       "python3 -c 'import pytest' 2>/dev/null || pip install -q pytest jsonschema pyyaml 2>/dev/null; "
       "bash /solution/solve.sh >/tmp/s 2>&1; echo SOLVE_RC=$?; "
       "bash /tests/test.sh 2>&1; echo ORACLE_RC=$?")

def verifier_env(task_dir):
    """Parse [verifier.env] from task.toml -> shell export prefix (Harbor injects these)."""
    p = os.path.join(task_dir, "task.toml")
    try:
        s = open(p).read()
    except OSError:
        return ""
    m = re.search(r"\[verifier\.env\](.*?)(\n\[|\Z)", s, re.S)
    if not m:
        return ""
    exports = []
    for line in m.group(1).splitlines():
        mm = re.match(r'\s*(\w+)\s*=\s*"?([^"\n]*)"?\s*$', line)
        if mm:
            exports.append(f"export {mm.group(1)}={shlex.quote(mm.group(2))};")
    return " ".join(exports)

def one(app, repo, task, logdir, timeout, use_old=False):
    td = os.path.join(repo, "tasks", task)
    tests_path, tmp = (old_tests_dir(repo, task) if use_old else (td+"/tests", None))
    if use_old and not tests_path:
        return task, "NO-OLD", ""
    img = (modal.Image.from_dockerfile(td+"/environment/Dockerfile", context_dir=td+"/environment")
           .add_local_dir(tests_path, remote_path="/tests")
           .add_local_dir(td+"/solution", remote_path="/solution"))
    sb = None
    try:
        sb = modal.Sandbox.create(app=app, image=img, timeout=timeout, cpu=2, memory=4096)
        p = sb.exec("bash", "-lc", verifier_env(td) + RUN); out = p.stdout.read(); p.wait()
        open(os.path.join(logdir, task + ".log"), "w").write(out)
        m = re.search(r"ORACLE_RC=(\d+)", out)
        rc = int(m.group(1)) if m else 1
        failed = re.findall(r"(?:::)?(test_\w+) FAILED", out)
        return task, ("OK" if rc == 0 else "FAIL"), ",".join(sorted(set(failed)))
    except Exception as e:
        return task, "EXC", str(e)[-100:]
    finally:
        if sb is not None:
            try: sb.terminate()
            except Exception: pass
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo"); ap.add_argument("tasks")
    ap.add_argument("--logdir", required=True); ap.add_argument("--workers", type=int, default=100)
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument("--old", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.logdir, exist_ok=True)
    repo = os.path.abspath(a.repo)
    app = modal.App.lookup("r35k-oracle-gate", create_if_missing=True)
    tasks = [t for t in open(a.tasks).read().split() if t]
    summ = os.path.join(a.logdir, "_summary.tsv")
    counts = {}
    with cf.ThreadPoolExecutor(a.workers) as ex:
        futs = [ex.submit(one, app, repo, t, a.logdir, a.timeout, a.old) for t in tasks]
        for i, f in enumerate(cf.as_completed(futs), 1):
            task, kind, detail = f.result()
            counts[kind] = counts.get(kind, 0) + 1
            with lock:
                open(summ, "a").write(f"{task}\t{kind}\t{detail}\n")
            if i % 25 == 0 or i == len(futs):
                print(f"[{i}/{len(futs)}] {counts}", flush=True)
    print("DONE", counts, "->", summ)

if __name__ == "__main__":
    main()
