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
