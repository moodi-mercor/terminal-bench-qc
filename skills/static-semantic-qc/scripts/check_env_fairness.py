#!/usr/bin/env python3
"""Layer 1 — static environment-fairness screen (deterministic, read-only).

The statically-decidable half of "task fairness / agent cheating potential" (#7):
confirm the agent's starting context is only the intended input, by reconstructing
what the build leaves in the agent-visible image and flagging things that
shouldn't be there. No task run required.

Flags:
  - leftover-generator      a data/answer generator (create_*/generate*/mutate*)
                            COPY'd or written into the image and NOT rm'd — the
                            agent can read how the data (and answers) are made.
  - uncleaned-setup-script  a setup script (setup_commands.sh, ...) left in the
                            image after the build ran it.
  - git-history-exposed     `git clone` in the build with no `.git` removal —
                            exposes history / future commits / the fix.
  - runtime-network         test.sh / entrypoint hits an external URL at run time
                            (live-service fragility + a fetch-the-answer vector).

What this CANNOT do statically: confirm by probing that the agent truly can't
reach something at runtime, and container/network isolation (an infra guarantee).
Those stay in the delivery-stage run. (Reading tests/ or solution/ at solve time
is architecturally impossible — they're verify-time mounts.)

Usage:
    python check_env_fairness.py <tasks-dir> [--out findings_env_fairness.json]

Emits findings with area="anti_cheat".
"""
import argparse
import glob
import os
import re

from common import (FAIL, WARN, PASS, finding, emit, read_text, discover_tasks,
                    load_toml, get, is_reflection_schema)

# bakeable runtime dependency installs (spec: "pip download runs in the build, never
# at runtime"; "test.sh must not depend on live network installs")
RUNTIME_INSTALL = re.compile(
    r"\b(pip[0-9]?\s+install|python[0-9]?\s+-m\s+pip\s+install|uv\s+pip\s+install|"
    r"apt(?:-get)?\s+install|conda\s+install|npm\s+(?:install|ci)\b|"
    r"curl\b[^\n]*\|\s*(?:sudo\s+)?sh)\b")
# a genuine external need that justifies allow_internet=true per the spec
GENUINE_NET = re.compile(
    r"hugging\s?face|download (?:a |the )?model|fetch[^\n]*from the internet|"
    r"external (?:api|service|registry)|live (?:api|data|feed)", re.I)

BUILD_SCRIPTS = ("setup_commands.sh", "setup.sh", "setup_env.sh", "init.sh",
                 "bootstrap.sh", "prestart_setup.sh", "entrypoint.sh",
                 "docker-entrypoint.sh", "build.sh")

# generator-style filenames whose presence in the image is a fairness risk
GEN = re.compile(r"\b((?:create|generate|gen|mutate|seed|populate|synth|make)"
                 r"[\w-]*\.(?:py|sh))\b", re.I)
SETUP_NAMES = re.compile(r"\b(setup_commands\.sh|setup\.sh|setup_env\.sh|init\.sh|"
                         r"bootstrap\.sh|prestart_setup\.sh|build\.sh)\b")
# a file materialised into the image: COPY/ADD src, or a redirect/heredoc/cp/mv dest
MATERIALIZE = re.compile(r"""(?:
    COPY\s+(?:--\S+\s+)*([^\s]+)\s+\S+        |  # COPY src dest  -> src
    ADD\s+(?:--\S+\s+)*([^\s]+)\s+\S+         |
    (?:>|>>)\s*(\S+)                          |  # > dest
    cat\s*<<[^\n]*>\s*(\S+)                   |  # heredoc > dest
    \b(?:cp|mv)\s+[^\n]*?\s(\S+)\s*$            # cp/mv ... dest
)""", re.VERBOSE | re.MULTILINE)
EXT_URL = re.compile(r"\b(curl|wget)\b[^\n]*https?://(?!localhost|127\.0\.0\.1)")


def _build_text(root):
    env = os.path.join(root, "environment")
    parts = [read_text(os.path.join(env, "Dockerfile"))]
    for n in BUILD_SCRIPTS:
        for h in glob.glob(os.path.join(env, "**", n), recursive=True):
            parts.append(read_text(h))
    return "\n".join(parts)


def check_task(name, root):
    out = []
    btxt = _build_text(root)
    rm_text = "\n".join(ln for ln in btxt.splitlines() if re.search(r"\brm\b", ln)
                        or "-exec rm" in ln or "rmtree" in ln or "os.remove" in ln)

    def cleaned(basename):
        return basename in rm_text

    # collect materialised file basenames
    materialised = set()
    for m in MATERIALIZE.finditer(btxt):
        tok = next((g for g in m.groups() if g), None)
        if tok:
            materialised.add(os.path.basename(tok.strip().strip('"\'')))

    # 1. leftover generators
    gen_left = sorted({b for b in materialised if GEN.search(b) and not cleaned(b)})
    for b in gen_left:
        out.append(finding(name, "anti_cheat", FAIL, "leftover-generator",
                           detail=f"generator `{b}` is left in the agent image (no rm) — "
                                  "the agent can read how the data/answers are produced.",
                           location="environment/",
                           fix=f"`rm` {b} at the end of the build step that runs it, or "
                               "generate inline so no named generator persists."))

    # 2. uncleaned setup scripts (only those actually copied/materialised in)
    setup_left = sorted({b for b in materialised if SETUP_NAMES.search(b) and not cleaned(b)})
    for b in setup_left:
        out.append(finding(name, "anti_cheat", FAIL, "uncleaned-setup-script",
                           detail=f"setup script `{b}` remains in the agent image after the "
                                  "build — it may reveal how the environment/answers were set up.",
                           location="environment/",
                           fix=f"`rm` {b} after running it in the same RUN layer."))

    # 3. git history exposed
    if re.search(r"\bgit\s+clone\b", btxt):
        removes_git = (".git" in rm_text) or re.search(r"-name\s+['\"]?\.git", btxt)
        if not removes_git:
            out.append(finding(name, "anti_cheat", FAIL, "git-history-exposed",
                               detail="`git clone` in the build with no `.git` removal — "
                                      "the agent can read git history (future commits / the fix).",
                               location="environment/Dockerfile",
                               fix="After cloning, `rm -rf <repo>/.git` (or export a tree "
                                   "without history). A pinned shallow clone reduces but "
                                   "doesn't eliminate the risk."))

    # 4. runtime network in the verifier / entrypoint
    for rel in ("tests/test.sh",):
        t = read_text(os.path.join(root, rel))
        if EXT_URL.search(t):
            out.append(finding(name, "anti_cheat", FAIL, "runtime-network",
                               detail=f"`{rel}` fetches an external URL at run time — live "
                                      "dependency (flaky) and a possible fetch-the-answer path.",
                               location=rel,
                               fix="Vendor the resource into the image at build time instead "
                                   "of fetching it during verification."))

    # 5. Reflection network policy (gated): network must be OFF by default unless a
    #    justified external need is documented; bakeable installs belong in the build.
    #    OTS tasks are not flagged here (internet-on is the norm there → noise).
    toml_path = os.path.join(root, "task.toml")
    d = load_toml(toml_path)
    if d and is_reflection_schema(d):
        raw = read_text(toml_path)
        allow_net = get(d, "environment.allow_internet")
        if allow_net is True:
            net_line = next((l for l in raw.splitlines()
                             if "allow_internet" in l and "true" in l), "")
            documented = "#" in net_line  # inline justification comment
            instr = read_text(os.path.join(root, "instruction.md"))
            genuine = bool(GENUINE_NET.search(instr))
            if not documented and not genuine:
                out.append(finding(name, "anti_cheat", FAIL, "internet-on-undocumented",
                                   detail="allow_internet=true with no documented justification. "
                                          "Reflection requires network OFF by default; any exception "
                                          "must be explicit, minimal, and justified per task.",
                                   location="task.toml",
                                   fix="Set allow_internet=false (bake deps at build), or add an "
                                       "inline justification noting the genuine external need."))
        # bakeable runtime installs in the verifier or the reference solution
        for rel in ("tests/test.sh", "solution/solve.sh"):
            t = read_text(os.path.join(root, rel))
            # ignore full-line shell comments — a documented "install baked in the
            # Dockerfile" note is not a runtime install.
            t = "\n".join(l for l in t.splitlines() if not l.lstrip().startswith("#"))
            m = RUNTIME_INSTALL.search(t)
            if m:
                out.append(finding(name, "anti_cheat", FAIL, "bakeable-runtime-install",
                                   detail=f"`{rel}` installs dependencies at run time "
                                          f"(`{m.group(0)[:40]}`). Reflection bakes these at build "
                                          "(pip download / wheels in the image), never at runtime.",
                                   location=rel,
                                   fix="Move the install into the Dockerfile build (network is "
                                       "available there) — bake the package / wheels / build "
                                       "backend so the runtime needs no network."))

    if not out:
        out.append(finding(name, "anti_cheat", PASS, "env-fairness-static-clean"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks")
    ap.add_argument("--out", default="findings_env_fairness.json")
    args = ap.parse_args()
    findings = []
    tasks = discover_tasks(args.tasks)
    for name, root in tasks:
        findings.extend(check_task(name, root))
    n = emit(findings, args.out)
    fails = sum(1 for f in findings if f["severity"] == FAIL)
    warns = sum(1 for f in findings if f["severity"] == WARN)
    print(f"[env_fairness] {len(tasks)} tasks, {n} findings, {fails} FAIL, {warns} WARN -> {args.out}")


if __name__ == "__main__":
    main()
