#!/usr/bin/env python3
"""Auto-generate candidate CHEAT solutions from static findings (static->runtime loop).

Static flags are CANDIDATES; the only ground truth for "this verifier is gameable" is
a no-work cheat that the oracle scores reward=1. This closes the loop: for every
exploit-class finding (agent-writable grader / baked truth or grader the verifier
reads / literal-only verifier), it writes a candidate `solve.sh` that performs the
no-work exploit, plus a CHEAT.md describing it and how to confirm. Drop the solve.sh
into the task and run the behavioral oracle gate (Layer 3): reward=1 => CONFIRMED hack.

The generated cheats are best-effort probes — the scanner knows the leak path / grader
path / baked literals, but the exact deliverable wiring is per-task, so a cheat that
does not pass is INCONCLUSIVE (not "safe"); read the CHEAT.md and refine by hand.

Usage:
    python gen_cheat_harness.py <tasks-dir> --out-dir cheats
"""
import argparse
import ast
import os
import re
import shlex

import check_reward_hack
import check_leakage
import check_verifier_defenses
import assert_classify
from common import discover_tasks, read_text, task_paths

EXPLOIT_TITLES = {
    "agent-writable-verifier", "truth-baked-verifier-reads", "tests-bake-verifier-reads",
    "reference-solve-reads-truth", "literal-only-verifier", "unconditional-reward",
}


def _findings(name, root):
    out = []
    for mod in (check_reward_hack, check_leakage, check_verifier_defenses):
        try:
            out += mod.check_task(name, root)
        except Exception:
            pass
    return out


def _success_token(test_sh):
    """The string the verifier greps for to mean pass (agent-writable case)."""
    for m in re.finditer(r"grep\s+(?:-\w+\s+)*['\"]?([A-Za-z][\w ]{1,30})['\"]?", test_sh):
        tok = m.group(1).strip().strip('"\'')
        if tok and tok.lower() not in ("reward", "0", "1"):
            return tok
    return "PASS"


def _backticked(detail):
    return re.findall(r"`([^`]+)`", detail or "")


def _py_list(detail):
    m = re.search(r"\[([^\]]*)\]", detail or "")
    if not m:
        return []
    return [s.strip().strip("'\"") for s in m.group(1).split(",") if s.strip()]


def _cheat_for(name, root, findings):
    """Return (steps[], notes[]) — shell lines for solve.sh and human notes."""
    steps, notes = [], []
    p = task_paths(root)
    test_sh = read_text(p["test.sh"])
    seen = set()
    for f in findings:
        t = f.get("title")
        if t not in EXPLOIT_TITLES or t in seen:
            continue
        seen.add(t)
        detail = f.get("detail", "")
        if t == "agent-writable-verifier":
            bt = _backticked(detail)
            inimg = next((x for x in bt if x.startswith("/")), None)
            tok = _success_token(test_sh)
            if inimg:
                ext = os.path.splitext(inimg)[1]
                body = (f'print("{tok}")' if ext == ".py" else f'echo "{tok}"')
                steps.append(f"# EXPLOIT: overwrite the agent-writable grader to emit the pass token")
                steps.append(f"mkdir -p {shlex.quote(os.path.dirname(inimg) or '/')}")
                steps.append(f"cat > {shlex.quote(inimg)} <<'CHEAT_EOF'\n"
                             f"{'#!/usr/bin/env python3' if ext=='.py' else '#!/bin/sh'}\n{body}\nCHEAT_EOF")
                steps.append(f"chmod +x {shlex.quote(inimg)}")
                notes.append(f"agent-writable-verifier: rewrote {inimg} to print '{tok}'.")
        elif t in ("truth-baked-verifier-reads", "tests-bake-verifier-reads"):
            for path in _py_list(detail):
                if not path.startswith("/"):
                    continue
                steps.append(f"# EXPLOIT: the verifier reads baked truth the agent can also read")
                steps.append(f"echo '>> leaked truth at {path} (copy it onto the deliverable):'")
                steps.append(f"cat {shlex.quote(path)} 2>/dev/null || true")
                notes.append(f"{t}: leaked truth at {path} — wire it onto the graded output path "
                             "(per-task) to confirm.")
        elif t == "reference-solve-reads-truth":
            bt = [x for x in _backticked(detail) if x.startswith("/")]
            for path in bt:
                steps.append(f"cat {shlex.quote(path)} 2>/dev/null || true   # leaked truth")
                notes.append(f"reference-solve-reads-truth: the reference itself reads {path}.")
        elif t == "literal-only-verifier":
            pairs = assert_classify.literal_io_pairs(read_text(p["test_outputs.py"]))
            if pairs:
                steps.append("# EXPLOIT: verifier compares files to baked literals — write them directly")
            for path, val in pairs:
                if not isinstance(path, str) or not path:
                    continue
                steps.append(f"mkdir -p {shlex.quote(os.path.dirname(path) or '/')}")
                steps.append(f"printf '%s\\n' {shlex.quote(str(val))} > {shlex.quote(path)}")
                notes.append(f"literal-only-verifier: wrote baked literal {val!r} to {path}.")
        elif t == "unconditional-reward":
            notes.append("unconditional-reward: test.sh writes a passing reward with no success "
                         "check — a no-op (empty solve.sh) should already score reward=1.")
    return steps, notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks")
    ap.add_argument("--out-dir", default="cheats")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    index = []
    for name, root in discover_tasks(args.tasks):
        findings = _findings(name, root)
        if not any(f.get("title") in EXPLOIT_TITLES for f in findings):
            continue
        steps, notes = _cheat_for(name, root, findings)
        if not steps and not any(f.get("title") == "unconditional-reward" for f in findings):
            continue
        tdir = os.path.join(args.out_dir, name)
        os.makedirs(tdir, exist_ok=True)
        solve = "#!/bin/bash\n# AUTO-GENERATED CANDIDATE CHEAT — confirm by running the oracle.\n" \
                "# reward=1 after this no-work solve => the verifier is gameable (CONFIRMED).\n" \
                "set -uo pipefail\n\n" + "\n".join(steps) + "\n"
        with open(os.path.join(tdir, "solve.sh"), "w") as fh:
            fh.write(solve)
        os.chmod(os.path.join(tdir, "solve.sh"), 0o755)
        classes = sorted({f["title"] for f in findings if f.get("title") in EXPLOIT_TITLES})
        with open(os.path.join(tdir, "CHEAT.md"), "w") as fh:
            fh.write(f"# Candidate cheat — {name}\n\n**Exploit classes:** {', '.join(classes)}\n\n"
                     "## How to confirm\n"
                     "Drop `solve.sh` in as the task's solution and run the Layer-3 behavioral "
                     "oracle gate. **reward=1 => CONFIRMED reward hack.** A failing/!=1 reward is "
                     "INCONCLUSIVE — refine the cheat from the notes below.\n\n## Notes\n"
                     + "\n".join(f"- {n}" for n in notes) + "\n")
        index.append({"task": name, "classes": classes, "cheat_steps": len(steps)})
    with open(os.path.join(args.out_dir, "index.json"), "w") as fh:
        import json
        json.dump(index, fh, indent=2)
    print(f"[cheat_harness] generated {len(index)} candidate cheat(s) -> {args.out_dir}/")
    for e in index:
        print(f"  {e['task']:34} {e['classes']}")


if __name__ == "__main__":
    main()
