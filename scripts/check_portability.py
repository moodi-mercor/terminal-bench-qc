#!/usr/bin/env python3
"""Layer 1 — solve.sh & test portability screen (deterministic, read-only).

Catches the statically-decidable `solve.sh`/`test` defects that dominated the
customer's validation tail — failures that show up as reward=0 on a non-Docker
sandbox but are visible by reading the files:

solve.sh:
  - backgrounded-daemon-no-redirect : `... &` with no stdout redirect and no
        top-level `exec </dev/null >...`. The daemon inherits bash's stdout pipe;
        the runner waits on EOF and times out. (~250 tasks; the #1 lever.)
  - pip-no-break-system-packages    : `pip install` without --break-system-packages
        / a venv — aborts under PEP 668 on Ubuntu 24.04 with `set -e`. (~250.)
  - server-defined-not-started      : writes a server/app but never launches it. (~80.)
  - redis-no-daemonize              : `redis-server <conf>` runs in foreground and
        blocks the rest of solve.sh.
  - mixed-bash-python-solve         : a python shebang on a script run as `bash`.
  - broad-pkill                     : `pkill -f <pattern>` that can match the
        sandbox launcher and kill the run.

tests / Dockerfile:
  - systemd-assumption              : tests use systemctl/journalctl/systemd —
        absent in most non-Docker sandboxes.
  - cmd-entrypoint-reliance         : a daemon started via Docker CMD/ENTRYPOINT
        that solve.sh doesn't start itself (CMD isn't run by non-Docker runtimes).

Mostly WARN (candidates a real run confirms). Usage:
    python check_portability.py <tasks-dir> [--out findings_portability.json]
Emits findings with area="solution" (solve.sh) and "tests" (test/CMD).
"""
import argparse
import glob
import os
import re

from common import WARN, PASS, finding, emit, read_text, discover_tasks, task_paths

SERVER_MARK = re.compile(r"\b(uvicorn|gunicorn|fastapi|flask|grpc\.server|"
                         r"add_insecure_port|http\.server|app\.run\(|HTTPServer|"
                         r"aiohttp|tornado|\.serve\(|serve_forever|socketserver)\b", re.I)
LAUNCH_MARK = re.compile(r"(\bnohup\b|\buvicorn\b|\bgunicorn\b|&\s*$|&\s*\n|"
                         r"\bserve\b|systemctl\s+start|service\s+\S+\s+start|"
                         r"\bpython3?\s+\S*(server|app|main|daemon|run)\S*\.py|"
                         r"-m\s+\S+\s|\.\/\S+\s*&)", re.I)
HEAVY = re.compile(r"\b(spark|pyspark|neo4j|elasticsearch|hadoop|milvus|clickhouse|"
                   r"-Xmx|cassandra)\b", re.I)


def _solution_text(root):
    parts = [read_text(task_paths(root)["solve.sh"])]
    for h in glob.glob(os.path.join(root, "solution", "**", "*"), recursive=True):
        if os.path.isfile(h) and not h.endswith("solve.sh"):
            parts.append(read_text(h))
    return "\n".join(parts)


def _check_solve(root, name):
    out = []
    sh = task_paths(root)["solve.sh"]
    if not os.path.isfile(sh):
        return out
    text = read_text(sh)
    lines = text.splitlines()

    # python shebang on a bash-run script
    if lines and re.match(r"#!.*python", lines[0]):
        out.append(finding(name, "solution", WARN, "mixed-bash-python-solve",
                           detail="solve.sh has a python shebang but the harness runs it "
                                  "with `bash` — the implementation won't execute.",
                           location="solution/solve.sh:1",
                           fix="Make solve.sh a real bash script that invokes python "
                               "(write the python to a file, then `python3 file.py`)."))

    # backgrounded daemon without stdout redirect (and no top exec redirect)
    has_exec_redirect = any(re.search(r"\bexec\b[^\n]*>", l) for l in lines[:12])
    if not has_exec_redirect:
        for i, l in enumerate(lines, 1):
            s = l.strip()
            if s.endswith("&") and not s.endswith("&&") and ">" not in s:
                out.append(finding(name, "solution", WARN, "backgrounded-daemon-no-redirect",
                                   detail=f"line {i} backgrounds a process (`&`) without "
                                          "redirecting stdout — it can hold the runner's pipe "
                                          "open and hang the run.",
                                   location=f"solution/solve.sh:{i}",
                                   fix="Redirect: `nohup CMD >/var/log/svc.log 2>&1 &` (or add "
                                       "`exec </dev/null >/tmp/solve.log 2>&1` at the top)."))
                break

    # pip install without --break-system-packages and no venv
    if re.search(r"(?<!uv )\bpip3?\s+install\b", text):
        has_flag = "--break-system-packages" in text or "PIP_BREAK_SYSTEM_PACKAGES" in text
        has_venv = re.search(r"(python3?\s+-m\s+venv|virtualenv|/activate|conda\s+activate|"
                             r"\buv\s+pip)", text)
        if not has_flag and not has_venv:
            out.append(finding(name, "solution", WARN, "pip-no-break-system-packages",
                               detail="solve.sh runs `pip install` without "
                                      "--break-system-packages or a venv — aborts under PEP 668 "
                                      "on Ubuntu 24.04 (`set -e` then skips the rest).",
                               location="solution/solve.sh",
                               fix="Add `--break-system-packages` (or PIP_BREAK_SYSTEM_PACKAGES=1), "
                                   "or install into a venv."))

    # redis-server in foreground
    for i, l in enumerate(lines, 1):
        if re.search(r"\bredis-server\b", l) and "--daemonize" not in l \
                and not l.strip().endswith("&") and "nohup" not in l:
            out.append(finding(name, "solution", WARN, "redis-no-daemonize",
                               detail=f"line {i} starts redis-server in the foreground — it "
                                      "blocks the rest of solve.sh.",
                               location=f"solution/solve.sh:{i}",
                               fix="Use `redis-server <conf> --daemonize yes` (or background it "
                                   "with a redirect)."))
            break

    # broad pkill
    for i, l in enumerate(lines, 1):
        if re.search(r"\bpkill\s+-f\b", l):
            out.append(finding(name, "solution", WARN, "broad-pkill",
                               detail=f"line {i} uses `pkill -f` — the pattern can match the "
                                      "sandbox supervising launcher and kill the run.",
                               location=f"solution/solve.sh:{i}",
                               fix="Use an anchored pattern or `pgrep -f ... | xargs -r kill` "
                                   "with PID filtering that excludes the launcher."))
            break

    # config edited but daemon never restarted/started afterward (~40 tasks, 1st-5k)
    CFG = re.compile(r"(redis\.conf|postgresql\.conf|pg_hba\.conf|my\.cnf|mysqld?\.cnf|"
                     r"nginx\.conf|httpd\.conf|mosquitto\.conf|supervisord?\.conf|"
                     r"/etc/[\w./-]+\.conf)")
    EDIT = re.compile(r"(\bsed\s+-i|>>\s*\S|\btee\b|\bcrudini\b|\baugtool\b)")
    RESTART = re.compile(r"(systemctl\s+(restart|reload)|service\s+\S+\s+(restart|reload|start)|"
                         r"/etc/init\.d/\S+\s+(restart|reload|start)|kill\s+-HUP|-s\s+reload|"
                         r"\bnginx\s+-s\s+reload|pg_ctl\b|CONFIG\s+SET|--daemonize|"
                         r"\bredis-server\b|\bmysqld|\bmongod\b|supervisorctl\s+(restart|reload|update))",
                         re.I)
    edit_line = next((i for i, l in enumerate(lines) if EDIT.search(l) and CFG.search(l)), None)
    if edit_line is not None and not RESTART.search("\n".join(lines[edit_line:])):
        out.append(finding(name, "solution", WARN, "config-edit-no-restart",
                           detail=f"line {edit_line+1} edits a service config but solve.sh never "
                                  "restarts/reloads the daemon afterward — the running daemon keeps "
                                  "the old config and the test sees stale state.",
                           location=f"solution/solve.sh:{edit_line+1}",
                           fix="Restart or reload the daemon after the edit (e.g. `service <svc> "
                               "restart` / `kill -HUP` / re-launch), or edit before first start."))

    # server defined but never started
    sol = _solution_text(root)
    if SERVER_MARK.search(sol) and not LAUNCH_MARK.search(text):
        out.append(finding(name, "solution", WARN, "server-defined-not-started",
                           detail="solve.sh writes a server/app (uvicorn/flask/grpc/...) but "
                                  "has no command that starts it — tests hitting the port get "
                                  "connection-refused.",
                           location="solution/solve.sh",
                           fix="Start the server explicitly: `nohup <cmd> >/var/log/svc.log 2>&1 &` "
                               "+ a wait-for-port loop. Confirm with a real run."))
    return out


def _check_tests_and_cmd(root, name):
    out = []
    ttext = read_text(task_paths(root)["test.sh"]) + "\n" + \
        read_text(task_paths(root)["test_outputs.py"])
    if re.search(r"\b(systemctl|journalctl)\b|/run/systemd", ttext):
        out.append(finding(name, "tests", WARN, "systemd-assumption",
                           detail="tests use systemctl/journalctl — absent in most non-Docker "
                                  "sandboxes, so the check can't run there.",
                           location="tests/",
                           fix="Replace with a generic daemon start/health probe, or tag the "
                               "task as requiring systemd."))
    # Docker CMD/ENTRYPOINT starting a daemon that solve.sh doesn't
    df = read_text(task_paths(root)["Dockerfile"])
    m = re.search(r"^\s*(CMD|ENTRYPOINT)\s+(.+)$", df, re.M)
    if m:
        cmd = m.group(2)
        daemonish = re.search(r"(supervisor|uvicorn|gunicorn|server|daemon|nginx|"
                              r"redis-server|mysqld|postgres|mongod|mlflow|service)", cmd, re.I)
        trivial = re.search(r"(sleep\s+infinity|tail\s+-f|/bin/bash|/bin/sh\b|\bbash\b\s*$)", cmd)
        if daemonish and not trivial:
            sol = read_text(task_paths(root)["solve.sh"])
            key = daemonish.group(1).lower()
            if key not in sol.lower():
                out.append(finding(name, "tests", WARN, "cmd-entrypoint-reliance",
                                   detail=f"Dockerfile {m.group(1)} starts `{key}`, but solve.sh "
                                          "doesn't — non-Docker runtimes don't run the image CMD, "
                                          "so the daemon never comes up.",
                                   location="environment/Dockerfile",
                                   fix="Start the daemon explicitly in solve.sh instead of "
                                       "relying on the image CMD/ENTRYPOINT."))
    return out


def check_task(name, root):
    out = []
    out += _check_solve(root, name)
    out += _check_tests_and_cmd(root, name)
    areas = {f["area"] for f in out}
    if "solution" not in areas:
        out.append(finding(name, "solution", PASS, "solve-portability-clean"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks")
    ap.add_argument("--out", default="findings_portability.json")
    args = ap.parse_args()
    findings = []
    for name, root in discover_tasks(args.tasks):
        findings.extend(check_task(name, root))
    n = emit(findings, args.out)
    warns = sum(1 for f in findings if f["severity"] == WARN)
    print(f"[portability] {n} findings, {warns} WARN -> {args.out}")


if __name__ == "__main__":
    main()
