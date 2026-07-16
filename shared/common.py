#!/usr/bin/env python3
"""Shared foundation for the terminal-bench-qc deterministic detectors.

Provides:
  - a minimal TB2-tuned TOML reader (stdlib-only; py3.9 has no tomllib)
  - task discovery (handles the `filesystem/` snapshot nesting from Studio)
  - the canonical findings schema + emit helpers
  - severity ranking shared by every gate and by aggregate.py

Findings schema (one dict per finding; a JSON array per gate):
  {
    "task":     "<task-name>",
    "area":     "structure|metadata|dockerfile|instructions|tests|solution|anti_cheat|behavioral|dataset",
    "severity": "PASS|WARN|FAIL",
    "title":    "<short stable label, used for distribution counts>",
    "location": "<file[:line] or ''>",
    "detail":   "<what is wrong>",
    "fix":      "<how to fix>",
    "layer":    "<optional: static|semantic|trajectory|behavioral — cross-layer provenance>"
  }

Keep `title` short and stable per defect class — the dataset defect-distribution
report counts (area, title) pairs, so drifting titles fragment the histogram.
"""
import json
import os

# ---------------------------------------------------------------- severity ---
PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
SEV_RANK = {PASS: 0, WARN: 1, FAIL: 2}
AREAS = ["structure", "metadata", "dockerfile", "instructions",
         "tests", "solution", "anti_cheat", "behavioral", "dataset"]


def worst(severities):
    """Return the highest-rank severity in an iterable (PASS if empty)."""
    out = PASS
    for s in severities:
        if SEV_RANK.get(s, 0) > SEV_RANK[out]:
            out = s
    return out


# ------------------------------------------------------ blocking calibration ---
# Reflection delivery calibration. `overall` in the CSV is the RAW worst-of verdict
# (what a client's un-calibrated LLM pass sees); `blocking` is the calibrated verdict
# (what actually fails delivery).
#
# DESIGN: block by default, downgrade only an explicit advisory DENYLIST — the inverse
# of an allowlist. This is deliberate and load-bearing:
#   * The semantic reviewer/adversary (judge.py) emits OPEN-VOCABULARY titles — the LLM
#     coins the defect name (untested-requirement, oracle-contract-violation,
#     contract-contradiction, brittle-string-match, ...). An allowlist can never
#     enumerate those, so an allowlist silently downgrades the ENTIRE semantic layer to
#     advisory. A denylist keeps every semantic FAIL blocking, as it must be.
#   * Any NEW static FAIL title a future check adds blocks by default (fail-safe), rather
#     than silently passing until someone remembers to allowlist it.
# So a FAIL blocks UNLESS its title is in ADVISORY_FAIL below. WARN/PASS never block.
#
# ADVISORY_FAIL = FAIL classes that do NOT block on their own. Two rationales, both
# aligned with how the pipeline actually runs — the runtime gates, not a static read, are
# the authority on build/portability:
#   (a) client-tolerated cosmetic authoring style (heredoc, solve length, bash-vs-native,
#       packaging residue, non-grading metadata gaps).
#   (b) BUILD / PORTABILITY hygiene. Whether the image actually builds and the oracle/no-op
#       actually run is PROVEN by check_behavioral (build-fails / oracle-fails /
#       no-op-passes — all blocking). The static apt/chmod/daemon/entrypoint-style
#       heuristics are advisory nudges, not delivery blockers; if they cause real breakage
#       the behavioral gate catches it. Genuine infra-CONFIG breakage a build can't reveal
#       stays blocking and is NOT listed here: cpus=0 (`cpus-nonpositive` / -zero-resource
#       -> Modal rejects the container), base image not digest-pinned/approved (spec-hard
#       FROM rule), ENTRYPOINT reliance (`dockerfile-entrypoint` / `cmd-entrypoint-reliance`
#       -> client infra overrides startup).
# Anything touching grading integrity, difficulty, determinism, infra-config, or a
# CONFIRMED leak is NOT here and blocks. split-apt (`apt-not-consolidated`), `unpinned-pip`
# and `oracle-runtime-install` are already WARN from their checks. Keep in exact sync with
# the delivery run so results are reproducible across whoever runs the skill.
ADVISORY_FAIL = {
    # (a) cosmetic authoring style
    "solve-embedded-heredoc", "dockerfile-heredoc-source",
    "solve-too-long", "mixed-bash-python-solve", "bash-op-doable-natively",
    "missing-dockerignore", "pycache-residue-after-script-removal",
    "missing-tags", "missing-junior-time",
    # (b) build hygiene (Dockerfile authoring; real breakage caught by the build gate)
    "apt-no-update", "broad-chmod", "archive-fixture-not-extracted",
    "test-deps-in-image", "add-remote-url", "curl-pipe-sh",
    # (b) environment hygiene (leftover/uncleaned files, bakeable installs, stray files) —
    # env authoring, not grading; a real answer-leak is caught by the leak checks/adversary,
    # a real offline-break by the behavioral gate.
    "leftover-generator", "uncleaned-setup-script", "bakeable-runtime-install",
    "unnecessary-files",
    # (b) portability hygiene (solve/runtime authoring; real breakage caught by oracle/no-op)
    "backgrounded-daemon-no-redirect", "pip-no-break-system-packages",
    "redis-no-daemonize", "broad-pkill", "config-edit-no-restart",
    "server-defined-not-started", "verifier-unbounded-call", "systemd-assumption",
    # (a) instruction authoring hygiene — real gaps surface in the semantic reviewer's
    # alignment/contract dimensions (which block); the static heuristics are nudges.
    "structured-output-undocumented", "instruction-relative-path", "prescriptive-instruction",
    # (a) test authoring hygiene — encoded/shelled-out test steps are readability nudges
    # (client "readable tests"), not grading defects; the check still surfaces them as WARN.
    "shell-wrapped-python", "base64-wrapped-command",
}

# ADVERSARY_GATED = FP-prone static LEAK + WEAK-VERIFIER heuristics — a static read cannot
# tell a real defect from a benign pattern here, so these are CANDIDATES, not verdicts.
# They block ONLY when the adversary/skeptic CONFIRMS them (`verify-confirm`); unconfirmed,
# aggregate.reconcile down-grades FAIL->WARN — the same candidate→confirm pattern already
# used for `agent-writable-verifier` / `semantic-cheat-vector`. The authoritative signals
# for what these approximate are the SEMANTIC reviewer (its own FAILs block) and the
# BEHAVIORAL gate (mutation / no-op-passes / --determinism-trials -> nondeterministic-oracle,
# all blocking) — so a REAL defect still blocks; a static-only false alarm does not.
#   * leak reads/bakes: a comment-mentioned `.truth`, a build-baked fixture regenerated at
#     runtime, a verifier reading an INPUT it is allowed to.
#   * weak verifier: existence-only / no-assertion / vacuous readings over-fire; power is
#     confirmed by mutation testing at runtime.
#   * verifier determinism: static wall-clock/RNG heuristics; real drift is proven by
#     --determinism-trials (nondeterministic-oracle, which stays blocking).
# These block outright (NOT gated) — deterministic and not FP-prone: reward-gaming
# (unconditional-reward, reward-pre-created, test-sh-swallows-failure, test-sh-set-e-reward-
# abort, reward-path-nonstandard, conftest-plant-vulnerable, test-runtime-install),
# llm-judge-in-verifier (subjective grading), dangling-truth-reference (broken verifier),
# plus all infra-config, behavioral-runtime, and semantic FAILs.
# NOTE: dockerfile-copies-* / secret-baked-in-image / obfuscated-payload / hidden-unicode /
# prompt-injection ARE gated — the delivery triage found them all FP (agent-facing smoke
# fixtures, test crypto keys, build-time fixture-data encoding, Unicode-analysis test data,
# security-task fixture content), so a static hit is a candidate the adversary confirms.
ADVERSARY_GATED = {
    # leak reads/bakes
    "truth-baked-verifier-reads", "truth-named-baked",
    "tests-bake-verifier-reads", "tests-bake-unread", "tests-bake-removed",
    "verifier-reads-instruction-input", "verifier-reads-config-spec",
    "reference-solve-reads-truth", "reference-reads-instruction-input",
    "delegates-to-truth-verifier", "verifier-helper-in-environment",
    # image/file leak heuristics — triage: agent-facing smoke fixtures, not answer leaks
    "dockerfile-copies-solution", "dockerfile-copies-tests",
    "dockerfile-copies-env-tests", "dockerfile-copies-hint-file",
    "test-imports-solution", "secret-baked-in-image",
    # security heuristics — triage: fixture/test data for security-analysis tasks
    "obfuscated-payload", "hidden-unicode", "prompt-injection",
    # weak / low-power verifier heuristics (power confirmed by mutation testing)
    "existence-only-check", "no-assertion-test", "vacuous-test", "swallowed-assertion",
    "empty-parametrize", "skipped-scored-test", "source-match-verification",
    "literal-only-verifier", "verifier-self-consistent", "degenerate-integrity-guard",
    "filename-encodes-answer", "verifier-undefended", "fragmented-test-helpers",
    # agent-writable (candidate; confirmed by adversary / behavioral reward-signal-gameable)
    "agent-writable-verifier", "agent-writable-reward-signal",
    # verifier-determinism heuristics (real drift proven by behavioral --determinism-trials)
    "unseeded-randomness-in-verifier", "wall-clock-in-verifier", "wall-clock-dependent-verifier",
}


def is_blocking(finding):
    """A FAIL blocks acceptance UNLESS its title is an explicitly-tolerated advisory
    hygiene class (ADVISORY_FAIL). WARN/PASS never block. Blocking-by-default is required
    because the semantic reviewer emits open-vocabulary titles no allowlist could cover.
    (FP-prone static leak heuristics are down-graded FAIL->WARN in aggregate.reconcile
    unless the adversary confirms them, so they reach here as WARN when unconfirmed.)"""
    return (finding.get("severity") == FAIL
            and finding.get("title") not in ADVISORY_FAIL)


# ------------------------------------------------------------- dimensions ---
# The QC dimensions every task must be assessed on before it can be called clean.
# This is the master checklist (see QC_CHECKLIST.md): each dimension names the tool
# that answers it, the QC layer, and the SSOT `area` its finding rolls into. A task
# is INCOMPLETE — not clean — until every dimension carries a finding *with evidence*.
# Enforced by aggregate.py --require-complete (which quarantines incomplete tasks via
# the gate); the reviewer/adversary producers (judge.py) also tag each finding with
# its `dimension` so the aggregator can tell "assessed clean" from "never looked".
#
#   key -> {area, layer, tool}
QC_DIMENSIONS = {
    # semantic reviewer — judge.py --role reviewer (one finding per dimension)
    "alignment":     {"area": "instructions", "layer": "semantic",   "tool": "judge.py --role reviewer"},
    "coverage":      {"area": "tests",         "layer": "semantic",   "tool": "judge.py --role reviewer"},
    "hygiene":       {"area": "instructions",  "layer": "semantic",   "tool": "judge.py --role reviewer"},
    "golden-patch":  {"area": "solution",      "layer": "semantic",   "tool": "judge.py --role reviewer"},
    "realism":       {"area": "instructions",  "layer": "semantic",   "tool": "judge.py --role reviewer"},
    "constraints":   {"area": "tests",         "layer": "semantic",   "tool": "judge.py --role reviewer"},
    "category":      {"area": "metadata",      "layer": "semantic",   "tool": "judge.py --role reviewer"},
    "determinism":   {"area": "tests",         "layer": "semantic",   "tool": "judge.py --role reviewer"},
    "contract":      {"area": "instructions",  "layer": "semantic",   "tool": "judge.py --role reviewer"},
    # adversarial reward-hack red-team — judge.py --role adversary
    "cheat-vector":  {"area": "tests",         "layer": "semantic",   "tool": "judge.py --role adversary"},
    # behavioral — the ones a read cannot decide; only Modal/Docker execution answers them
    "oracle-passes": {"area": "behavioral",    "layer": "behavioral", "tool": "modal_gate.py (oracle -> reward 1)"},
    "noop-fails":    {"area": "behavioral",    "layer": "behavioral", "tool": "modal_gate.py (no-op -> reward 0)"},
    "verifier-sound":{"area": "tests",         "layer": "behavioral", "tool": "mutation_test.py (mutants -> reward 0)"},
}
# The read-only dimensions the reviewer sub-agent / judge.py reviewer must emit.
# `category` = the assigned task.toml category/subcategory matches the dominant work.
# `contract` = instruction+spec define a coherent, complete, non-contradictory contract.
REVIEWER_DIMS = ["alignment", "coverage", "hygiene", "golden-patch", "realism",
                 "constraints", "category", "determinism", "contract"]


def dimension_area(dim):
    """SSOT area a dimension's coverage finding rolls into (default 'tests')."""
    return QC_DIMENSIONS.get(dim, {}).get("area", "tests")


def coverage_gaps(task_findings, behavioral=None, require_adversary=True,
                  require_behavioral=True):
    """Which QC dimensions were skipped or asserted without evidence for one task.

    A dimension is *covered* when some finding carries `dimension == <key>` and that
    finding has non-empty evidence (`detail` or `location`) — a PASS with no evidence
    means "didn't actually look", so it does NOT count as covered. The two behavioral
    dimensions are covered by a runtime signal (oracle/noop in `behavioral`), not by a
    read. Returns [(dim, reason)] for every gap; empty list == fully assessed.

    `behavioral` is the per-task runtime dict {oracle,noop,...} (see aggregate.py);
    None means behavioral never ran. Set require_* False to exempt a layer you did
    not intend to run (e.g. reviewer-only pass exempts adversary + behavioral).
    """
    def has_evidence(f):
        return bool((f.get("detail") or "").strip() or (f.get("location") or "").strip())

    covered, no_evidence = set(), set()
    for f in task_findings:
        dim = f.get("dimension")
        if not dim:
            continue
        if has_evidence(f):
            covered.add(dim)
        else:
            no_evidence.add(dim)

    gaps = []
    need = list(REVIEWER_DIMS)
    if require_adversary:
        need.append("cheat-vector")
    for dim in need:
        if dim in covered:
            continue
        gaps.append((dim, "asserted-without-evidence" if dim in no_evidence
                     else "not-assessed"))
    if require_behavioral:
        b = behavioral or {}
        if b.get("oracle") is None:
            gaps.append(("oracle-passes", "not-assessed"))
        if b.get("noop") is None:
            gaps.append(("noop-fails", "not-assessed"))
        # verifier soundness is established by mutation testing (mutants -> reward 0),
        # NOT by assumption: a task is incomplete until a mutation-testing signal exists.
        if b.get("mutation") is None:
            gaps.append(("verifier-sound", "not-assessed"))
    return gaps


def finding(task, area, severity, title, detail="", location="", fix="", layer=""):
    f = {
        "task": task, "area": area, "severity": severity, "title": title,
        "location": location, "detail": detail, "fix": fix,
    }
    # Optional cross-layer provenance: which QC layer caught this. Omitted when not
    # set so existing findings stay unchanged; the gate falls back to AREA_LAYER.
    if layer:
        f["layer"] = layer
    return f


# Which QC layer an area belongs to, for cross-layer provenance + the defect gate
# (shared/gate.py). A finding may override this with an explicit "layer" — e.g.
# trajectory (Layer 2) findings use area="tests" but layer="trajectory".
AREA_LAYER = {
    "structure": "static", "metadata": "static", "dockerfile": "static",
    "instructions": "static", "anti_cheat": "static", "dataset": "static",
    "tests": "semantic", "solution": "semantic",
    "behavioral": "behavioral",
}


def layer_of(f):
    """The QC layer that produced a finding: explicit `layer`, else mapped from area."""
    return f.get("layer") or AREA_LAYER.get(f.get("area", ""), "unknown")


def emit(findings, out_path):
    """Write a findings list to out_path as a JSON array; return the count."""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(findings, f, indent=2)
    return len(findings)


# -------------------------------------------------------------------- io -----
def read_text(path):
    try:
        with open(path, errors="replace") as f:
            return f.read()
    except Exception:
        return ""


# ----------------------------------------------------- task discovery --------
def task_root(dir_with_toml):
    """Given a directory that contains task.toml, return (task_name, root).

    Studio snapshots nest the tree under `filesystem/`, so a task may live at
    `<name>/task.toml` or `<name>/filesystem/task.toml`. The task *name* is the
    nearest enclosing directory that is not literally `filesystem`.
    """
    root = os.path.abspath(dir_with_toml)
    name = os.path.basename(root)
    if name == "filesystem":
        name = os.path.basename(os.path.dirname(root))
    return name, root


def discover_tasks(path):
    """Yield (task_name, task_root) for every task under `path`.

    A task root is any directory directly containing `task.toml`. Works whether
    `path` is a single task or a folder of many tasks (optionally `filesystem/`
    nested). Deduped by task name (first one wins).
    """
    path = os.path.abspath(path)
    seen = set()
    out = []
    if os.path.isfile(os.path.join(path, "task.toml")):
        name, root = task_root(path)
        out.append((name, root))
        return out
    for dirpath, dirnames, filenames in os.walk(path):
        # don't descend into heavy/noise dirs
        dirnames[:] = [d for d in dirnames
                       if d not in (".git", "node_modules", "__pycache__", "tasks_cache")]
        if "task.toml" in filenames:
            name, root = task_root(dirpath)
            if name not in seen:
                seen.add(name)
                out.append((name, root))
            # do not descend below a task root
            dirnames[:] = []
    return sorted(out)


def task_paths(root):
    """Standard TB2 paths relative to a task root (whether or not they exist)."""
    return {
        "task.toml":          os.path.join(root, "task.toml"),
        "instruction.md":     os.path.join(root, "instruction.md"),
        "Dockerfile":         os.path.join(root, "environment", "Dockerfile"),
        "environment":        os.path.join(root, "environment"),
        "test.sh":            os.path.join(root, "tests", "test.sh"),
        "test_outputs.py":    os.path.join(root, "tests", "test_outputs.py"),
        "tests":              os.path.join(root, "tests"),
        "solve.sh":           os.path.join(root, "solution", "solve.sh"),
        "solution":           os.path.join(root, "solution"),
    }


# ------------------------------------------------- minimal TOML reader -------
def _strip_comment(line):
    out, q = [], None
    for ch in line:
        if q:
            out.append(ch)
            if ch == q:
                q = None
        elif ch in ("'", '"'):
            q = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out)


def _split_commas(s):
    parts, buf, q, depth = [], [], None, 0
    for ch in s:
        if q:
            buf.append(ch)
            if ch == q:
                q = None
        elif ch in ("'", '"'):
            q = ch
            buf.append(ch)
        elif ch in "[{":
            depth += 1
            buf.append(ch)
        elif ch in "]}":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if "".join(buf).strip():
        parts.append("".join(buf))
    return parts


def _scalar(s):
    s = s.strip()
    if not s:
        return ""
    if (s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'"):
        return s[1:-1]
    low = s.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _value(s):
    s = s.strip()
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [_scalar(x) for x in _split_commas(inner)]
    return _scalar(s)


def _set_nested(root, dotted, value):
    keys = dotted.split(".")
    d = root
    for k in keys[:-1]:
        k = k.strip().strip('"').strip("'")
        d = d.setdefault(k, {})
    d[keys[-1].strip().strip('"').strip("'")] = value


def parse_toml(text):
    """Parse the subset of TOML used by TB2 task.toml.

    Handles dotted section headers, quoted/numeric/bool scalars, single- and
    multi-line arrays, and comments. Inline tables are not expected and are
    stored as raw strings. Best-effort: never raises on malformed input.
    """
    data, cur, pending = {}, {}, None
    data["_root"] = cur  # top-level keys before any section
    section = data["_root"]

    def flush(line):
        nonlocal section
        line = _strip_comment(line).rstrip()
        if not line.strip():
            return
        st = line.strip()
        if st.startswith("[") and st.endswith("]"):
            name = st[1:-1].strip()
            section = {}
            _set_nested(data, name, section)
            return
        if "=" in st:
            k, _, v = st.partition("=")
            section[k.strip().strip('"').strip("'")] = _value(v)

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        st = _strip_comment(line)
        # multi-line array: join until brackets balance
        if "=" in st and st.count("[") > st.count("]"):
            buf = st
            while i + 1 < len(lines) and buf.count("[") > buf.count("]"):
                i += 1
                buf += " " + _strip_comment(lines[i])
            flush(buf)
        else:
            flush(line)
        i += 1

    # promote top-level keys (schema_version, artifacts) to the data dict
    for k, v in data.pop("_root").items():
        data.setdefault(k, v)
    return data


def load_toml(path):
    return parse_toml(read_text(path))


def get(d, dotted, default=None):
    """Dotted-path getter over the parsed-toml dict."""
    cur = d
    for k in dotted.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


# Reflection-only task.toml keys — their presence marks a Harbor/Reflection-schema
# task (vs the older TB2/OTS shape). Detectors use this to apply Harbor-specific
# conventions (base-image allowlist, digest pinning, no-runtime-install-in-tests,
# the /logs/verifier/reward.txt reward path) ONLY to Reflection deliveries, so legacy
# OTS tasks aren't blanketed with WARNs for rules they aren't held to.
def is_reflection_schema(d):
    md = get(d, "metadata") or {}
    if not isinstance(md, dict):
        md = {}
    refl_keys = ("task_objective", "artifact_type", "model_tested",
                 "agent_tested", "avg_at_8", "expert_time_estimate_hours")
    return (any(k in md for k in refl_keys)
            or get(d, "environment.build_timeout_sec") is not None)


if __name__ == "__main__":
    # tiny self-test
    sample = '''
schema_version = "1.1"
artifacts = []

[metadata]
difficulty = "hard"
category = "networking"
tags = ["nginx", "tls", "reverse-proxy"]
expert_time_estimate_min = 90
junior_time_estimate_min = 240

[verifier]
timeout_sec = 300

[agent]
timeout_sec = 1800

[environment]
cpus = 1
memory_mb = 4096
allow_internet = false
'''
    d = parse_toml(sample)
    assert get(d, "schema_version") == "1.1"
    assert get(d, "metadata.difficulty") == "hard"
    assert get(d, "metadata.tags") == ["nginx", "tls", "reverse-proxy"]
    assert get(d, "metadata.expert_time_estimate_min") == 90
    assert get(d, "verifier.timeout_sec") == 300
    assert get(d, "environment.allow_internet") is False
    print("common.py self-test OK")
