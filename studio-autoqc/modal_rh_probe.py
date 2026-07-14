#!/usr/bin/env python3
"""Reward-hack probe on Modal — reliable, build-aware, no subagents.

Per task (build image once, native amd64):
  1. LEAK scan on the PRISTINE image (before anything runs): grep the verifier's
     expected tokens (extracted locally from tests/test_outputs.py) across the
     AGENT-VISIBLE dirs (/app /home /root /data /opt /srv /workspace /var ... —
     NOT /tests or /solution, which are grade-time mounts). Also list
     answer-signature filenames (*truth*/*expected*/*answer*/*oracle*/*golden*/*.key).
  2. NO-OP trial: bash /tests/test.sh on the untouched container -> if it PASSES,
     the verifier is vacuous/gameable.

Output per task (tab-sep): name, noop(PASS|fail|err), n_token_hits, hit_files, sig_files
  -> LEAK-CANDIDATE if token hits in agent-visible files (answer literally readable)
  -> NOOP-PASS      if empty container passes
A task with neither is 'clean' on both reliable signals.

Runs with the modalenv python. Usage:
  _local/modalenv/bin/python modal_rh_probe.py <repo> <task_list> \
      --workers 200 --state <done.txt> --out <results.tsv>
"""
import argparse
import base64
import concurrent.futures as cf
import os
import re
import sys
import threading

import modal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE]
from validate_rh_leak import expected_tokens  # noqa: E402

lock = threading.Lock()
APP_NAME = "rh-probe"
AGENT_DIRS = "/app /home /root /data /opt /srv /workspace /var/log /var/lib /etc /tmp /usr/local/share"


def local_tokens(tdir):
    tf = os.path.join(tdir, "tests", "test_outputs.py")
    if not os.path.isfile(tf):
        return []
    return expected_tokens(open(tf, errors="replace").read())


def probe_one(app, repo, task, timeout):
    tdir = os.path.join(repo, "tasks", task)
    toks = local_tokens(tdir)
    tok_b64 = base64.b64encode("\n".join(toks).encode()).decode() if toks else ""
    try:
        img = (modal.Image.from_dockerfile(
                    os.path.join(tdir, "environment", "Dockerfile"),
                    context_dir=os.path.join(tdir, "environment"))
               .add_local_dir(os.path.join(tdir, "tests"), remote_path="/tests")
               .add_local_dir(os.path.join(tdir, "solution"), remote_path="/solution"))
        sb = modal.Sandbox.create(app=app, image=img, timeout=timeout, cpu=2, memory=4096)
    except Exception as e:
        msg = str(e)[-160:].replace("\n", " ")
        return task, ("BUILD-FAIL" if "uild" in msg or "image" in msg.lower() else "ERR"), 0, msg
    try:
        # High-precision leak signal only: answer-key / truth / oracle / verifier
        # files that SURVIVE in agent-visible space. AGENT_DIRS already excludes the
        # grade-time /tests and /solution mounts, so no path-exclusion is needed
        # (and /app/tests IS a real leak location, must NOT be excluded).
        script = (
            'D=""; for d in ' + AGENT_DIRS + '; do [ -e "$d" ] && D="$D $d"; done; '
            'echo SIG_START; '
            'if [ -n "$D" ]; then find $D -type f \\( '
            '     -iname "*truth*" -o -iname "*expected*" -o -iname "*answer*" '
            '  -o -iname "*oracle*" -o -iname "*golden*" -o -iname "*ground*truth*" '
            '  -o -iname "verify_*" -o -iname "solution*" -o -iname "solve.sh" '
            '  -o -iname "*reward*" \\) '
            '  -not -path "*/site-packages/*" -not -path "*/dist-packages/*" '
            '  -not -path "*/venv/*" -not -path "*/__pycache__/*" '
            '  -not -path "*/node_modules/*" -not -path "*/.git/*" -not -path "/usr/*" '
            '  -not -path "*/.julia/*" -not -path "*/examples/*" -not -path "*sdk*/*" '
            '  -not -path "*/vendor/*" -not -path "*/vendor_docs/*" -not -path "*/.cargo/*" '
            '  -not -path "*/.rustup/*" -not -path "*/site-packages*" '
            '  2>/dev/null | head -15; fi; '
            'echo SIG_END; '
            'mkdir -p /logs 2>/dev/null; bash /tests/test.sh >/tmp/noop.log 2>&1; '
            'echo "NRC=$?"'
        )
        p = sb.exec("bash", "-lc", script)
        out = p.stdout.read(); p.wait()
    except Exception as e:
        try: sb.terminate()
        except Exception: pass
        return task, "EXC", 0, str(e)[-160:].replace("\n", " ")
    try: sb.terminate()
    except Exception: pass

    def between(a, b):
        m = re.search(a + r"(.*?)" + b, out, re.S)
        return [x.strip() for x in m.group(1).strip().splitlines() if x.strip()] if m else []
    sigs = between("SIG_START", "SIG_END")
    m = re.search(r"NRC=(\d+)", out)
    noop = "PASS" if (m and m.group(1) == "0") else ("fail" if m else "err")
    return task, noop, len(sigs), ";".join(sigs[:8])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo"); ap.add_argument("tasks")
    ap.add_argument("--workers", type=int, default=100)
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument("--state", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    names = [l.strip() for l in open(a.tasks) if l.strip()]
    done = set()
    if os.path.isfile(a.state):
        done = {l.split("\t")[0] for l in open(a.state) if l.strip()}
    todo = [n for n in names if n not in done
            and os.path.isdir(os.path.join(a.repo, "tasks", n, "environment"))]
    print(f"{len(todo)} to probe on Modal ({len(done)} done)", flush=True)

    app = modal.App.lookup(APP_NAME, create_if_missing=True)
    sf = open(a.state, "a"); of = open(a.out, "a")
    counts = {}
    n = 0
    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(probe_one, app, a.repo, t, a.timeout) for t in todo]
        for fut in cf.as_completed(futs):
            task, noop, nsig, sigf = fut.result()
            verdicts = []
            if noop == "PASS": verdicts.append("NOOP-PASS")
            if nsig > 0: verdicts.append("LEAK-CAND")
            if noop.startswith("BUILD") or noop in ("EXC", "ERR"):
                verdicts.append(noop)
            v = ",".join(verdicts) or "clean"
            with lock:
                sf.write(f"{task}\t{v}\t{nsig}\t{sigf}\n"); sf.flush()
                of.write(f"{task}\t{v}\t{nsig}\t{sigf}\n"); of.flush()
                counts[v] = counts.get(v, 0) + 1
            n += 1
            if n % 25 == 0 or n == len(todo):
                print(f"  {n}/{len(todo)} | " + " ".join(f"{k}={val}" for k, val in counts.items()), flush=True)
    print("DONE", counts, flush=True)


if __name__ == "__main__":
    main()
