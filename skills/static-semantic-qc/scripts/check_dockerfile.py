#!/usr/bin/env python3
"""Layer 0 — Dockerfile reproducibility / build-hygiene lint (deterministic).

Catches the build-reproducibility smells that make a task flaky or non-reproducible
across rebuilds — the kind of thing that passes on Modal today and breaks on the
client's infra next month — plus Reflection's explicit Dockerfile-structuring rules
(approved base images, digest pinning, single apt block, multi-stage builds for
compiled artifacts, no apt upgrade / broad chmod / heredoc source / opaque archives,
and a .dockerignore for non-trivial trees). All findings are WARN (non-blocking, per
the verdict scale); they enrich the report without inflating the FAIL-based defect
rate. Structure (no FROM / trivial Dockerfile) stays in check_structure.py;
COPY-of-solution/tests leaks stay in check_leakage.py.

Flags:
  - unpinned-base-image          FROM ... :latest or no tag (drifts across rebuilds)
  - base-image-not-digest-pinned FROM tag without @sha256 digest (Reflection: pin digests)
  - base-image-not-approved      FROM outside the pre-approved base-image set
  - apt-no-update                apt-get install with no apt-get update in the file
  - apt-not-consolidated         apt installs split across multiple RUN layers
  - apt-get-upgrade              apt-get upgrade (defeats digest pinning)
  - unpinned-pip                 pip install <pkg> with no == version pin
  - add-remote-url               ADD http(s)://... (fetches at build = non-reproducible)
  - curl-pipe-sh                 curl|wget piped into sh/bash (unpinned remote script)
  - missing-multistage-build     compiles an artifact but ships the toolchain (1 FROM)
  - broad-chmod                  chmod -R over a broad path (rewrites every file's mode)
  - dockerfile-heredoc-source    source embedded via RUN cat > f <<EOF (put it on disk)
  - archive-fixture-not-extracted COPY of a .tar.gz/.zip fixture (extract at build)
  - missing-dockerignore         non-trivial source tree with no .dockerignore
  - dockerfile-entrypoint        ENTRYPOINT set (client infra overrides startup)
  - test-deps-in-image           a test framework (pytest/nose) baked into the agent image

Usage:
    python check_dockerfile.py <tasks-dir> [--out findings_dockerfile.json]

Emits findings with area="dockerfile".
"""
import argparse
import os
import re

from common import (FAIL, WARN, PASS, finding, emit, read_text, discover_tasks,
                    task_paths, load_toml, is_reflection_schema)

FROM_RE = re.compile(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)", re.I | re.M)
PIP_RE = re.compile(r"\bpip3?\s+install\b([^\n&|]*)", re.I)
APT_INSTALL = re.compile(r"\b(?:apt-get|apt)\s+(?:-y\s+)?install\b", re.I)
APT_UPDATE = re.compile(r"\b(?:apt-get|apt)\s+update\b", re.I)
APT_UPGRADE = re.compile(r"\b(?:apt-get|apt)\s+(?:-y\s+)?(?:dist-)?upgrade\b", re.I)
ADD_URL = re.compile(r"^\s*ADD\s+(?:--\S+\s+)*(https?://\S+)", re.I | re.M)
CURL_PIPE = re.compile(r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?(?:ba)?sh\b", re.I)
BROAD_CHMOD = re.compile(r"\bchmod\s+-R\b", re.I)
HEREDOC_FILE = re.compile(r"(?:\b(?:cat|tee)\b[^\n]*<<|>\s*\S+\s*<<)", re.I)
COPY_ADD = re.compile(r"^\s*(?:COPY|ADD)\s+(.+)$", re.I | re.M)
ARCHIVE_EXT = re.compile(r"\.(?:tar\.gz|tgz|tar\.bz2|tar\.xz|tar|zip)\b", re.I)
# compiled-artifact build commands whose toolchain should not survive into runtime
BUILD_CMD = re.compile(r"\b(cargo build|mvn(?:\s+\S+)*\s+package|go build|"
                       r"dotnet publish|gradle(?:\s+\S+)*\s+build|npm\s+run\s+build|"
                       r"npm\s+ci\b|yarn\s+build)\b", re.I)
# pre-approved base-image repositories (Reflection base-images tab). All live under
# the AWS public ECR Docker mirror; we match the image name, not the exact digest
# (the listed digests rotate). A FROM outside this set is flagged for review.
APPROVED_IMAGES = {"golang", "python", "debian", "rust", "node", "ubuntu",
                   "eclipse-temurin", "ruby", "maven", "gcc"}
APPROVED_REGISTRY = "public.ecr.aws/docker/library/"


_HEREDOC_START = re.compile(r"<<[-~]?\s*[\"']?(\w+)[\"']?")


def _strip_heredocs(text):
    """Blank out heredoc bodies so embedded Python (`from x import y`) or SQL
    (`FROM table`) lines are not mis-read as Dockerfile FROM instructions."""
    out, marker = [], None
    for line in text.splitlines():
        if marker is None:
            out.append(line)
            m = _HEREDOC_START.search(line)
            if m:
                marker = m.group(1)
        else:
            if line.strip() == marker:   # closing delimiter alone on its line
                marker = None
            # otherwise drop the body line entirely
    return "\n".join(out)


def _logical_lines(text):
    """Heredoc-stripped, backslash-continuations folded into single logical
    instruction lines — so embedded shell/Python (`from x import`, `\\n\\`
    continuations inside a RUN) is not mis-read as a Dockerfile FROM."""
    lines, out, buf = _strip_heredocs(text).split("\n"), [], ""
    for ln in lines:
        cur = buf + ln
        if cur.rstrip().endswith("\\"):
            buf = cur.rstrip()[:-1] + " "
        else:
            out.append(cur); buf = ""
    if buf:
        out.append(buf)
    return out


def _from_refs(text):
    refs = []
    for ll in _logical_lines(text):
        m = FROM_RE.match(ll)
        if m:
            refs.append(m.group(1))
    return refs


def _image_name(ref):
    """The bare image name from a FROM ref (registry/owner stripped, tag/digest off)."""
    name = ref.split("@")[0].split(":")[0]
    return name.rsplit("/", 1)[-1].lower()


def _unpinned_base(ref):
    if ref.startswith("$") or ref.lower() == "scratch":
        return False
    if "@sha256" in ref:
        return False
    name = ref.split("@")[0]
    tag = name.rsplit(":", 1)[1] if ":" in name.rsplit("/", 1)[-1] else ""
    return (not tag) or tag.lower() == "latest"


def _run_blocks(text):
    """Yield each RUN instruction as one joined string (line continuations merged)."""
    blocks, buf, in_run = [], [], False
    for raw in text.splitlines():
        line = raw.rstrip()
        if re.match(r"\s*RUN\b", line, re.I):
            if buf:
                blocks.append(" ".join(buf))
            buf = [line]
            in_run = not line.rstrip().endswith("\\")
            if not in_run:
                continue
            blocks.append(" ".join(buf)); buf = []
        elif buf and buf[-1].rstrip().endswith("\\"):
            buf.append(line)
            if not line.rstrip().endswith("\\"):
                blocks.append(" ".join(buf)); buf = []
    if buf:
        blocks.append(" ".join(buf))
    return blocks


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


def _nontrivial_env(root):
    """A source tree worth a .dockerignore: environment/ has a subdir or >3 files."""
    env = task_paths(root)["environment"]
    if not os.path.isdir(env):
        return False
    n_files = 0
    for entry in os.scandir(env):
        if entry.is_dir():
            return True
        if entry.name != "Dockerfile":
            n_files += 1
    return n_files > 3


def _has_dockerignore(root):
    return (os.path.isfile(os.path.join(root, ".dockerignore"))
            or os.path.isfile(os.path.join(root, "environment", ".dockerignore")))


def check_task(name, root):
    out = []
    df = task_paths(root)["Dockerfile"]
    text = read_text(df)
    if not text.strip():
        return [finding(name, "dockerfile", PASS, "dockerfile-repro-ok")]
    loc = "environment/Dockerfile"
    refs = _from_refs(text)
    reflection = is_reflection_schema(load_toml(task_paths(root)["task.toml"]))

    unpinned = [r for r in refs if _unpinned_base(r)]
    if unpinned:
        out.append(finding(name, "dockerfile", FAIL, "unpinned-base-image",
                           detail=f"base image(s) {unpinned} use :latest or no tag — the "
                                  "build drifts as upstream moves.",
                           location=loc,
                           fix="Pin to an explicit version AND an @sha256 digest."))
    # Harbor/Reflection base-image conventions (digest pinning + the approved set) are
    # delivery-specific, so apply them only to Reflection-schema tasks — legacy OTS
    # tasks aren't held to the ECR allowlist and would otherwise all warn.
    if reflection:
        undigested = [r for r in refs
                      if not r.startswith("$") and r.lower() != "scratch"
                      and "@sha256" not in r and not _unpinned_base(r)]
        if undigested:
            out.append(finding(name, "dockerfile", FAIL, "base-image-not-digest-pinned",
                               detail=f"base image(s) {undigested} are tagged but not pinned by "
                                      "@sha256 digest — Reflection requires digest pinning.",
                               location=loc,
                               fix="Append the @sha256:... digest to each FROM (see the approved "
                                   "base-image list)."))
        not_approved = [r for r in refs
                        if not r.startswith("$") and r.lower() != "scratch"
                        and not (APPROVED_REGISTRY in r and _image_name(r) in APPROVED_IMAGES)]
        if not_approved:
            out.append(finding(name, "dockerfile", FAIL, "base-image-not-approved",
                               detail=f"base image(s) {not_approved} are not in the pre-approved "
                                      "set (python/debian/ubuntu/node/rust/go/gcc/ruby/maven/"
                                      "eclipse-temurin on public.ecr.aws/docker/library).",
                               location=loc,
                               fix="Use a pre-approved base image, or justify the exception if "
                                   "none support the task's dependencies."))

    apt_runs = [b for b in _run_blocks(text) if APT_INSTALL.search(b)]
    if apt_runs and not APT_UPDATE.search(text):
        out.append(finding(name, "dockerfile", FAIL, "apt-no-update",
                           detail="`apt-get install` with no `apt-get update` in the "
                                  "Dockerfile — installs can fail on a stale cache.",
                           location=loc,
                           fix="Run `apt-get update` in the same RUN before install."))
    if len(apt_runs) > 1:
        out.append(finding(name, "dockerfile", FAIL, "apt-not-consolidated",
                           detail=f"apt installs are split across {len(apt_runs)} RUN layers — "
                                  "each is a wasted layer and a fresh metadata refresh.",
                           location=loc,
                           fix="Merge the apt-get installs into a single RUN block."))
    if APT_UPGRADE.search(text):
        out.append(finding(name, "dockerfile", FAIL, "apt-get-upgrade",
                           detail="`apt-get upgrade` silently pulls whatever the mirror has "
                                  "today — it defeats the base-image digest pinning.",
                           location=loc,
                           fix="Remove the upgrade; pin the packages you actually need."))

    pip = _unpinned_pip(text)
    if pip:
        shown = pip[:6]
        out.append(finding(name, "dockerfile", FAIL, "unpinned-pip",
                           detail=f"pip install without a version pin: {shown}"
                                  f"{' …' if len(pip) > 6 else ''} — non-reproducible.",
                           location=loc,
                           fix="Pin each package (`pkg==x.y.z`) or use a locked requirements file."))

    url = ADD_URL.search(text)
    if url:
        out.append(finding(name, "dockerfile", FAIL, "add-remote-url",
                           detail=f"`ADD {url.group(1)}` fetches over the network at build "
                                  "time — non-reproducible and a supply-chain risk.",
                           location=loc,
                           fix="Vendor the artifact, or download+verify a checksum in a RUN."))

    if CURL_PIPE.search(text):
        out.append(finding(name, "dockerfile", FAIL, "curl-pipe-sh",
                           detail="curl/wget piped into sh/bash — runs an unpinned remote "
                                  "script at build; not reproducible or auditable.",
                           location=loc,
                           fix="Download a pinned version, verify a checksum, then run it."))

    # compiled artifact + single stage => the build toolchain/cache ships to runtime
    if BUILD_CMD.search(text) and len(refs) <= 1:
        m = BUILD_CMD.search(text)
        out.append(finding(name, "dockerfile", FAIL, "missing-multistage-build",
                           detail=f"Dockerfile runs `{m.group(1)}` but has a single stage — "
                                  "the toolchain and build cache survive into the runtime image.",
                           location=loc,
                           fix="Use a multi-stage build: compile in a builder stage, COPY only "
                               "the artifact into the runtime stage (unless it's needed at runtime)."))

    if BROAD_CHMOD.search(text):
        out.append(finding(name, "dockerfile", FAIL, "broad-chmod",
                           detail="`chmod -R` rewrites the mode metadata of every file it "
                                  "touches and inflates the layer.",
                           location=loc,
                           fix="chmod only the specific files that need a mode change."))

    if HEREDOC_FILE.search(text):
        out.append(finding(name, "dockerfile", FAIL, "dockerfile-heredoc-source",
                           detail="source/data embedded via a heredoc (`RUN cat > f <<EOF`) — "
                                  "un-lintable, hard to diff, and impossible to test outside "
                                  "the container.",
                           location=loc,
                           fix="Put the file on disk and COPY it in."))

    for m in COPY_ADD.finditer(text):
        if ARCHIVE_EXT.search(m.group(1)):
            out.append(finding(name, "dockerfile", FAIL, "archive-fixture-not-extracted",
                               detail="a .tar.gz/.zip fixture is COPYed in as an opaque archive "
                                      "— individual files don't appear as layer entries.",
                               location=loc,
                               fix="Extract the archive at build time so files are reviewable, "
                                   "or COPY the extracted files directly."))
            break

    # MAI contractual rule: their infra overrides container startup (replaces it
    # with `sleep infinity`), so a task that relies on ENTRYPOINT to bring up a
    # service silently fails there. Use CMD only / start services in solve.sh.
    if re.search(r"^\s*ENTRYPOINT\b", text, re.M):
        out.append(finding(name, "dockerfile", FAIL, "dockerfile-entrypoint",
                           detail="Dockerfile sets ENTRYPOINT — client infra (e.g. MAI) "
                                  "overrides startup with `sleep infinity`, so anything "
                                  "ENTRYPOINT launches never comes up.",
                           location=loc,
                           fix="Use CMD instead, and start any service explicitly in solve.sh."))

    # test framework baked into the AGENT image (TB rubric: test deps belong in
    # the verifier / run-tests.sh, not the agent's build). This does NOT apply to
    # Reflection single-image Harbor tasks: there is no separate verifier image and
    # runtime installs in test.sh are forbidden, so the verifier's pytest MUST be
    # baked into the one image — flagging it there is a false positive.
    if (not reflection
            and re.search(r"(?:pip3?|uv\s+pip)\s+install\b[^\n]*\b(pytest|unittest2|nose2?)\b", text, re.I)):
        out.append(finding(name, "dockerfile", FAIL, "test-deps-in-image",
                           detail="the agent image installs a test framework (pytest/nose) — "
                                  "test-only deps should be installed by the verifier "
                                  "(tests/test.sh), not baked into the agent's image.",
                           location=loc,
                           fix="Move the test-dependency install into tests/test.sh."))

    # a non-trivial source tree should ship a .dockerignore to scope COPY narrowly
    if "COPY" in text.upper() and _nontrivial_env(root) and not _has_dockerignore(root):
        out.append(finding(name, "dockerfile", FAIL, "missing-dockerignore",
                           detail="non-trivial environment/ tree with no .dockerignore — "
                                  "broad COPYs risk pulling in .git, caches, or unused assets.",
                           location="environment/",
                           fix="Add a .dockerignore that excludes .git, caches, venvs, and "
                               "unused files; scope COPY narrowly."))

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
