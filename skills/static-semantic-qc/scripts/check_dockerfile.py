#!/usr/bin/env python3
"""Layer 0 — Dockerfile reproducibility / build-hygiene lint (deterministic).

Catches the build-reproducibility smells that make a task flaky or non-reproducible
across rebuilds — the kind of thing that passes on Modal today and breaks on the
client's infra next month. All findings are WARN (non-blocking, per the verdict
scale: "unpinned test dep" is a WARN); they enrich the report without inflating the
FAIL-based defect rate. Structure (no FROM / trivial Dockerfile) stays in
check_structure.py; COPY-of-solution/tests leaks stay in check_leakage.py.

Flags:
  - unpinned-base-image     FROM ... :latest or no tag (drifts across rebuilds)
  - apt-no-update           apt-get install with no apt-get update in the file
  - unpinned-pip            pip install <pkg> with no == version pin
  - add-remote-url          ADD http(s)://... (fetches at build = non-reproducible)
  - curl-pipe-sh            curl|wget piped into sh/bash (unpinned remote script)

Usage:
    python check_dockerfile.py <tasks-dir> [--out findings_dockerfile.json]

Emits findings with area="dockerfile".
"""
import argparse
import re

from common import WARN, PASS, finding, emit, read_text, discover_tasks, task_paths

FROM_RE = re.compile(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)", re.I | re.M)
PIP_RE = re.compile(r"\bpip3?\s+install\b([^\n&|]*)", re.I)
APT_INSTALL = re.compile(r"\b(?:apt-get|apt)\s+(?:-y\s+)?install\b", re.I)
APT_UPDATE = re.compile(r"\b(?:apt-get|apt)\s+update\b", re.I)
ADD_URL = re.compile(r"^\s*ADD\s+(?:--\S+\s+)*(https?://\S+)", re.I | re.M)
CURL_PIPE = re.compile(r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?(?:ba)?sh\b", re.I)


def _unpinned_base(text):
    bad = []
    for m in FROM_RE.finditer(text):
        ref = m.group(1)
        if ref.startswith("$") or ref.lower() == "scratch":
            continue  # ARG-driven or scratch — not our call to make
        name = ref.split("@")[0]  # ignore digest (digest IS pinned)
        if "@sha256" in ref:
            continue
        tag = name.rsplit(":", 1)[1] if ":" in name.rsplit("/", 1)[-1] else ""
        if not tag or tag.lower() == "latest":
            bad.append(ref)
    return bad


def _unpinned_pip(text):
    pkgs = []
    for m in PIP_RE.finditer(text):
        for tok in m.group(1).split():
            if tok.startswith("-") or tok in (".", "..") or "/" in tok or "://" in tok:
                continue  # flags, local installs, requirement files, URLs
            if any(op in tok for op in ("==", ">=", "<=", "~=", "@", "<", ">")):
                continue  # version-constrained
            if re.match(r"^[A-Za-z0-9_.\[\]-]+$", tok):
                pkgs.append(tok)
    return pkgs


def check_task(name, root):
    out = []
    df = task_paths(root)["Dockerfile"]
    text = read_text(df)
    if not text.strip():
        return [finding(name, "dockerfile", PASS, "dockerfile-repro-ok")]
    loc = "environment/Dockerfile"

    base = _unpinned_base(text)
    if base:
        out.append(finding(name, "dockerfile", WARN, "unpinned-base-image",
                           detail=f"base image(s) {base} use :latest or no tag — the "
                                  "build drifts as upstream moves.",
                           location=loc,
                           fix="Pin to an explicit version or @sha256 digest."))

    if APT_INSTALL.search(text) and not APT_UPDATE.search(text):
        out.append(finding(name, "dockerfile", WARN, "apt-no-update",
                           detail="`apt-get install` with no `apt-get update` in the "
                                  "Dockerfile — installs can fail on a stale cache.",
                           location=loc,
                           fix="Run `apt-get update` in the same RUN before install."))

    pip = _unpinned_pip(text)
    if pip:
        shown = pip[:6]
        out.append(finding(name, "dockerfile", WARN, "unpinned-pip",
                           detail=f"pip install without a version pin: {shown}"
                                  f"{' …' if len(pip) > 6 else ''} — non-reproducible.",
                           location=loc,
                           fix="Pin each package (`pkg==x.y.z`) or use a locked requirements file."))

    url = ADD_URL.search(text)
    if url:
        out.append(finding(name, "dockerfile", WARN, "add-remote-url",
                           detail=f"`ADD {url.group(1)}` fetches over the network at build "
                                  "time — non-reproducible and a supply-chain risk.",
                           location=loc,
                           fix="Vendor the artifact, or download+verify a checksum in a RUN."))

    if CURL_PIPE.search(text):
        out.append(finding(name, "dockerfile", WARN, "curl-pipe-sh",
                           detail="curl/wget piped into sh/bash — runs an unpinned remote "
                                  "script at build; not reproducible or auditable.",
                           location=loc,
                           fix="Download a pinned version, verify a checksum, then run it."))

    # MAI contractual rule: their infra overrides container startup (replaces it
    # with `sleep infinity`), so a task that relies on ENTRYPOINT to bring up a
    # service silently fails there. Use CMD only / start services in solve.sh.
    if re.search(r"^\s*ENTRYPOINT\b", text, re.M):
        out.append(finding(name, "dockerfile", WARN, "dockerfile-entrypoint",
                           detail="Dockerfile sets ENTRYPOINT — client infra (e.g. MAI) "
                                  "overrides startup with `sleep infinity`, so anything "
                                  "ENTRYPOINT launches never comes up.",
                           location=loc,
                           fix="Use CMD instead, and start any service explicitly in solve.sh."))

    # test framework baked into the AGENT image (TB rubric: test deps belong in
    # the verifier / run-tests.sh, not the agent's build).
    if re.search(r"(?:pip3?|uv\s+pip)\s+install\b[^\n]*\b(pytest|unittest2|nose2?)\b", text, re.I):
        out.append(finding(name, "dockerfile", WARN, "test-deps-in-image",
                           detail="the agent image installs a test framework (pytest/nose) — "
                                  "test-only deps should be installed by the verifier "
                                  "(tests/test.sh), not baked into the agent's image.",
                           location=loc,
                           fix="Move the test-dependency install into tests/test.sh."))

    if not out:
        out.append(finding(name, "dockerfile", PASS, "dockerfile-repro-ok"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks")
    ap.add_argument("--out", default="findings_dockerfile.json")
    args = ap.parse_args()
    findings = []
    tasks = discover_tasks(args.tasks)
    for name, root in tasks:
        findings.extend(check_task(name, root))
    n = emit(findings, args.out)
    warns = sum(1 for f in findings if f["severity"] == WARN)
    print(f"[dockerfile] {len(tasks)} tasks, {n} findings, {warns} WARN -> {args.out}")


if __name__ == "__main__":
    main()
