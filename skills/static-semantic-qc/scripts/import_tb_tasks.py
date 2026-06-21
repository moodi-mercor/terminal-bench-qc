#!/usr/bin/env python3
"""Normalize public Terminal-Bench (TB v1) tasks into the TB2/harbor layout.

The public corpus (github.com/laude-institute/terminal-bench, `original-tasks/`)
uses TB v1: `task.yaml`, a root `Dockerfile`, `run-tests.sh`, `solution.sh`,
`tests/`. Our detectors expect TB2: `task.toml`, `environment/Dockerfile`,
`tests/test.sh`, `solution/solve.sh`. This script produces a faithful TB2 *view*
of each TB v1 task so the whole pipeline runs on them unchanged.

Why a normalizer (not dual-format detectors): TB v1 has a genuinely different
*metadata* schema (no [agent]/[verifier] timeouts, etc.). Running TB2-tuned
metadata/structure checks on it directly would flag dozens of format differences
as "defects" and corrupt the precision measurement. Normalizing once, here, keeps
the detectors single-path and the metadata clean *by construction* — so the real
TB tasks measure CONTENT-level false positives (leakage / reward-hack / brittle /
portability), which is the point of a clean baseline.

What is preserved verbatim (content checks stay faithful):
  - the Dockerfile, run-tests.sh, solution.sh, and tests/ file *contents*.
What is synthesized (format scaffolding only):
  - task.toml from task.yaml metadata, with timeouts/resources filled to valid
    TB2 values and time estimates kept if sane else set within the difficulty band.

Build-context note: in TB v1 the Docker build context is the task root, so a
`COPY . /app` would pull in solution.sh/tests/. In the normalized layout the build
context is `environment/`, which we populate with ONLY the build inputs (not
solution/tests) — i.e. exactly what the agent is meant to see. This models intent
correctly and avoids manufacturing false `COPY`-leak FAILs.

Usage:
    python import_tb_tasks.py --src references/tb-public-src/original-tasks \\
        --out tasks_cache_tb --labels eval/tb_clean_labels.csv [--limit N]
"""
import argparse
import csv
import os
import re
import shutil

# files that are NOT part of the agent build context (live elsewhere in TB2)
_NON_CONTEXT = {"task.yaml", "run-tests.sh", "solution.sh", "solution.yaml",
                "docker-compose.yaml", "docker-compose.yml", "Dockerfile",
                ".gitignore", ".dockerignore"}
_NON_CONTEXT_DIRS = {"tests", "solution", ".git", "__pycache__"}

VALID_DIFFICULTY = {"easy", "medium", "hard"}
# keep in sync with check_metadata.TIME_RANGES — pick a value inside the band
DEFAULT_TIME = {  # difficulty -> (expert_min, junior_min)
    "easy":   (30, 60),
    "medium": (60, 240),
    "hard":   (360, 1200),
}


# ------------------------------------------------- minimal task.yaml reader ---
def parse_task_yaml(text):
    """Parse the small fixed schema of a TB v1 task.yaml (no PyYAML needed).

    Handles: `key: value` scalars, a `|`/`|-` block scalar (used for
    `instruction`), inline `[]`/`[a, b]` lists, and `- item` block lists.
    Comment lines (`#`) and blanks are ignored. Best-effort; never raises.
    """
    data = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip("\n")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        m = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", line)
        if not m:
            i += 1
            continue
        key, val = m.group(1), m.group(2).strip()
        # block scalar: `key: |` / `|-` / `>`  -> gather indented lines
        if val in ("|", "|-", "|+", ">", ">-", ">+"):
            block, i = [], i + 1
            while i < len(lines):
                bl = lines[i]
                if bl.strip() == "":
                    block.append("")
                    i += 1
                    continue
                if bl[:1] in (" ", "\t"):
                    block.append(bl.lstrip())
                    i += 1
                else:
                    break
            data[key] = "\n".join(block).strip("\n")
            continue
        # block list: `key:` followed by `  - item` lines
        if val == "":
            items, j = [], i + 1
            saw = False
            while j < len(lines):
                lj = lines[j]
                if lj.strip().startswith("- "):
                    items.append(_scalar(lj.strip()[2:]))
                    saw = True
                    j += 1
                elif lj.strip() == "":
                    j += 1
                else:
                    break
            if saw:
                data[key] = items
                i = j
                continue
            # plain indented block (no `|`/`>` indicator) — e.g. a multi-line
            # `instruction:` whose text is just indented beneath it. Gather every
            # indented line until a column-0 key/line.
            block, j = [], i + 1
            while j < len(lines):
                lj = lines[j]
                if lj.strip() == "":
                    block.append("")
                    j += 1
                elif lj[:1] in (" ", "\t"):
                    block.append(lj.strip())
                    j += 1
                else:
                    break
            if any(b.strip() for b in block):
                data[key] = "\n".join(block).strip("\n")
                i = j
                continue
            data[key] = ""
            i += 1
            continue
        # inline list
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            data[key] = [_scalar(x) for x in inner.split(",")] if inner else []
            i += 1
            continue
        data[key] = _scalar(val)
        i += 1
    return data


def _scalar(s):
    s = s.strip()
    if (len(s) >= 2) and ((s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
        return s[1:-1]
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _toml_str(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def synth_task_toml(meta):
    """Build a clean TB2 task.toml string from parsed task.yaml metadata."""
    diff = str(meta.get("difficulty", "medium")).lower()
    if diff not in VALID_DIFFICULTY:
        diff = "medium"
    category = meta.get("category") or "software-engineering"
    tags = meta.get("tags") or []
    if not isinstance(tags, list):
        tags = [tags]
    tags = [str(t) for t in tags if str(t).strip()]
    if not tags:  # TB v1 tasks sometimes ship tags: [] — fill from category
        tags = [str(category)]

    de, dj = DEFAULT_TIME[diff]
    elo, ehi = {"easy": (5, 60), "medium": (5, 180), "hard": (300, 480)}[diff]
    jlo, jhi = {"easy": (20, 120), "medium": (10, 480), "hard": (600, 19200)}[diff]
    expert = meta.get("expert_time_estimate_min")
    junior = meta.get("junior_time_estimate_min")
    expert = int(expert) if isinstance(expert, (int, float)) and elo <= expert <= ehi else de
    junior = int(junior) if isinstance(junior, (int, float)) and jlo <= junior <= jhi else dj
    if junior < expert:
        junior = max(junior, expert)

    vto = meta.get("max_test_timeout_sec") or 180
    ato = meta.get("max_agent_timeout_sec") or 900
    vto, ato = int(float(vto)), int(float(ato))
    if ato < vto:
        ato = vto

    tags_toml = ", ".join(_toml_str(t) for t in tags)
    return (
        'schema_version = "1.1"\n\n'
        "[metadata]\n"
        f"difficulty = {_toml_str(diff)}\n"
        f"category = {_toml_str(category)}\n"
        f"tags = [{tags_toml}]\n"
        f"expert_time_estimate_min = {expert}\n"
        f"junior_time_estimate_min = {junior}\n\n"
        "[verifier]\n"
        f"timeout_sec = {vto}\n\n"
        "[agent]\n"
        f"timeout_sec = {ato}\n\n"
        "[environment]\n"
        "cpus = 1\n"
        "memory_mb = 4096\n"
        # TB v1 run-tests.sh fetches uv over the network at verify time; declare
        # internet so the (WARN-level) internet-flag-contradiction check doesn't
        # fire on the whole corpus.
        "allow_internet = true\n"
    )


def importable(src_task):
    """A TB v1 task we can faithfully normalize: needs the four core files."""
    need = [os.path.join(src_task, "task.yaml"),
            os.path.join(src_task, "Dockerfile"),
            os.path.join(src_task, "run-tests.sh"),
            os.path.join(src_task, "solution.sh"),
            os.path.join(src_task, "tests")]
    return all(os.path.exists(p) for p in need)


# the verifier itself — copying these into the agent image IS a real leak and must
# stay flagged even for a real TB task; everything else under tests/ is a fixture.
_VERIFIER_FILE = re.compile(r"(^|/)(test_outputs\.py|conftest\.py|test\.sh|test_[^/]*\.py)$")


def _relocate_tests_copies(src_task, env):
    """Rewrite TB v1 `COPY tests/<x>` build inputs into the environment/ context.

    TB v1's build context is the task root, so tasks `COPY tests/fixture.c` to
    hand the agent an input that happens to be stored under tests/. In TB2 the
    context is environment/, so we move those files there and strip the `tests/`
    prefix from the COPY. Verifier files (test_outputs.py/test.sh/conftest/
    test_*.py) are left pointing at tests/ on purpose — copying the verifier into
    the agent image is a genuine leak and should still flag.
    """
    dpath = os.path.join(env, "Dockerfile")
    text = open(dpath, errors="replace").read()
    out_lines = []
    for line in text.splitlines():
        m = re.match(r"^(\s*(?:COPY|ADD)\s+(?:--\S+\s+)*)(\S+)(\s+.*)$", line)
        if m:
            pre, src, rest = m.group(1), m.group(2), m.group(3)
            norm = src.strip().strip('"').strip("'").lstrip("./").lstrip("/")
            if norm.startswith("tests/"):
                relsub = norm[len("tests/"):].rstrip("/")
                srcfs = os.path.join(src_task, "tests", relsub)
                is_verifier = bool(_VERIFIER_FILE.search(norm))
                if os.path.exists(srcfs) and not is_verifier:
                    dst = os.path.join(env, relsub)
                    os.makedirs(os.path.dirname(dst) or env, exist_ok=True)
                    (shutil.copytree(srcfs, dst, dirs_exist_ok=True)
                     if os.path.isdir(srcfs) else shutil.copy2(srcfs, dst))
                    line = pre + relsub + rest  # drop the tests/ prefix
                elif os.path.exists(srcfs):  # verifier copy: keep flagged, keep buildable
                    dst = os.path.join(env, "tests", relsub)
                    os.makedirs(os.path.dirname(dst) or env, exist_ok=True)
                    (shutil.copytree(srcfs, dst, dirs_exist_ok=True)
                     if os.path.isdir(srcfs) else shutil.copy2(srcfs, dst))
        out_lines.append(line)
    with open(dpath, "w") as f:
        f.write("\n".join(out_lines) + "\n")


def normalize(src_task, out_task):
    os.makedirs(out_task, exist_ok=True)
    meta = parse_task_yaml(open(os.path.join(src_task, "task.yaml"),
                                errors="replace").read())
    # instruction.md
    with open(os.path.join(out_task, "instruction.md"), "w") as f:
        f.write((meta.get("instruction") or "").strip() + "\n")
    # task.toml
    with open(os.path.join(out_task, "task.toml"), "w") as f:
        f.write(synth_task_toml(meta))
    # environment/ = Dockerfile + build-context inputs (NOT solution/tests)
    env = os.path.join(out_task, "environment")
    os.makedirs(env, exist_ok=True)
    shutil.copy2(os.path.join(src_task, "Dockerfile"),
                 os.path.join(env, "Dockerfile"))
    for entry in sorted(os.listdir(src_task)):
        sp = os.path.join(src_task, entry)
        if os.path.isdir(sp):
            if entry in _NON_CONTEXT_DIRS:
                continue
            shutil.copytree(sp, os.path.join(env, entry), dirs_exist_ok=True)
        elif entry not in _NON_CONTEXT:
            shutil.copy2(sp, os.path.join(env, entry))
    # tests/  (test.sh <- run-tests.sh, plus the tests/ tree verbatim)
    tdir = os.path.join(out_task, "tests")
    os.makedirs(tdir, exist_ok=True)
    shutil.copy2(os.path.join(src_task, "run-tests.sh"),
                 os.path.join(tdir, "test.sh"))
    for entry in sorted(os.listdir(os.path.join(src_task, "tests"))):
        sp = os.path.join(src_task, "tests", entry)
        dp = os.path.join(tdir, entry)
        if os.path.isdir(sp):
            shutil.copytree(sp, dp, dirs_exist_ok=True)
        else:
            shutil.copy2(sp, dp)
    # solution/solve.sh <- solution.sh
    sdir = os.path.join(out_task, "solution")
    os.makedirs(sdir, exist_ok=True)
    shutil.copy2(os.path.join(src_task, "solution.sh"),
                 os.path.join(sdir, "solve.sh"))
    # rewrite TB v1 `COPY tests/<input>` build inputs into the environment/ context
    _relocate_tests_copies(src_task, env)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="references/tb-public-src/original-tasks",
                    help="dir of TB v1 task folders (each with task.yaml)")
    ap.add_argument("--out", default="tasks_cache_tb",
                    help="output dir for normalized TB2 tasks")
    ap.add_argument("--labels", default="eval/tb_clean_labels.csv",
                    help="write is_defect=0 label rows for the imported tasks")
    ap.add_argument("--limit", type=int, default=0, help="cap number imported (0=all)")
    args = ap.parse_args()

    src = os.path.abspath(args.src)
    names = sorted(d for d in os.listdir(src) if os.path.isdir(os.path.join(src, d)))
    os.makedirs(args.out, exist_ok=True)

    done, skipped = [], []
    for name in names:
        st = os.path.join(src, name)
        if not importable(st):
            skipped.append(name)
            continue
        try:
            normalize(st, os.path.join(args.out, name))
            done.append(name)
        except Exception as e:  # never let one bad task abort the import
            print(f"  ! skip {name}: {e}")
            skipped.append(name)
        if args.limit and len(done) >= args.limit:
            break

    if args.labels:
        os.makedirs(os.path.dirname(os.path.abspath(args.labels)), exist_ok=True)
        with open(args.labels, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["task", "is_defect", "expected_verdict", "expected_title",
                        "kind", "source", "notes"])
            for n in done:
                w.writerow([n, 0, "PASS", "", "public-tb", "terminal-bench-core",
                            "real public TB task (TB v1, normalized to TB2); "
                            "presumed clean — measures content-level precision"])
    print(f"[import_tb] normalized {len(done)} tasks -> {args.out} "
          f"({len(skipped)} skipped for missing core files)")
    print(f"[import_tb] labels -> {args.labels}")


if __name__ == "__main__":
    main()
