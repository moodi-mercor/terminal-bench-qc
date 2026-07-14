#!/usr/bin/env python3
"""Layer-1 static check — contract path/reference existence (Reflection group 1).

The client's group-1 defect: "instruction points somewhere the file/dir is not, or it is
never shipped." This extracts every concrete file/dir path the instruction.md references
and confirms it is either shipped in the agent-visible environment, created at build time
(Dockerfile), or produced by the task itself. A referenced input path that exists nowhere
is a broken-contract FAIL.

Conservative by design (the goal is precision, not recall): only flags paths that look like
a specific shipped artifact (have an extension or sit under a non-system dir) and that appear
NOWHERE in the environment tree, Dockerfile, setup scripts, or solve.sh.

Usage: python check_contract_paths.py <tasks-dir> [--out findings_contract_paths.json]
"""
import argparse, os, re
from common import FAIL, PASS, finding, emit, read_text, discover_tasks, task_paths

# system/framework paths that always exist or are runtime-managed — never flag
SYSTEM_PREFIXES = ("/proc", "/sys", "/dev", "/etc", "/usr", "/bin", "/sbin", "/lib",
                   "/var/log", "/var/run", "/run", "/tmp", "/root", "/home", "/opt",
                   "/logs", "/mnt", "/media")
# an instruction path is an "input the agent must find" if it has a file extension
# or is clearly a data/config artifact; we only check those (dirs to be created by the
# agent are outputs, not missing inputs)
# real shipped-artifact file extensions (data/config/code/binary) — NOT python module refs
FILE_EXTS = (".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml", ".xml", ".txt", ".md",
             ".ini", ".cfg", ".conf", ".toml", ".log", ".dat", ".bin", ".db", ".sqlite",
             ".sql", ".pkl", ".npy", ".npz", ".parquet", ".pcap", ".gz", ".zip", ".tar",
             ".png", ".jpg", ".jpeg", ".wav", ".pdf", ".proto", ".pb", ".h5", ".nc",
             ".env", ".key", ".pem", ".crt", ".lock", ".idx", ".sh")
PATHY = re.compile(r"""(?:^|[\s`'"(])(/[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)+)""")
BACKTICK_FILE = re.compile(r"[`'\"]([A-Za-z0-9_\-]+(?:/[A-Za-z0-9_\-]+)*\.[A-Za-z0-9]{1,6})[`'\"]")
# a NNN/xxxx/### placeholder or a 2+ digit run means it's a pattern / generated-series
# instance, not a single literal shipped file — don't treat as a concrete missing input
PLACEHOLDER = re.compile(r"(N{3,}|X{3,}|\{|\}|<|>|\*|#{2,}|\.\.\.|%[0-9]*d)")
DIGIT_RUN = re.compile(r"\d{2,}")
# words that mark the path as something the agent PRODUCES (an output, not a missing input)
OUTPUT_VERB = re.compile(r"\b(write|writes|writing|create|creates|creating|produce|produces|"
                         r"producing|generate|generates|generating|save|saves|saving|output|"
                         r"outputs|emit|emits|dump|dumps|pack|packs|store|stores|append|appends|"
                         r"populate|render|export)\b", re.I)
# output-ish basenames that are almost never a shipped input
OUTPUT_NAME = re.compile(r"(^out|_out\.|^output|^result|\.lock$|^packed|^report\.)", re.I)


# all-caps alpha run (NN, XYZ, YYYY, MMDD, HH, YYYYMMDD) in the basename, or a literal
# placeholder word — marks a template pattern, not a concrete shipped filename
CAPS_RUN = re.compile(r"[A-Z]{2,}")
PLACEHOLDER_WORD = re.compile(r"(filename|timestamp|datetime|hostname|username|placeholder|yourfile)", re.I)


def _is_file_artifact(pth):
    low = pth.lower()
    base = os.path.basename(pth)
    if PLACEHOLDER.search(pth):
        return False
    if CAPS_RUN.search(os.path.splitext(base)[0]):   # NN / XYZ / YYYYMMDD template token
        return False
    if PLACEHOLDER_WORD.search(base):
        return False
    return low.endswith(FILE_EXTS)


def _is_series_instance(pth):
    """A basename with a 2+ digit run (foo_000.json, segment_00001.bin) is one instance of a
    generated series, not a single shipped file — verify by prefix, don't flag the literal."""
    return bool(DIGIT_RUN.search(os.path.basename(pth)))


# marks an illustrative example filename (not a shipped input): "e.g. X", "such as X",
# "for example X", "named ... X", "like X"
EXAMPLE_MARK = re.compile(r"(e\.?g\.?|i\.?e\.?|such as|for example|example|named|like|format)",
                          re.I)


def _framed_as_output(instr, pth):
    """The path is an output the agent produces (skip) if an output verb sits within ~90 chars
    before a mention, or the basename looks output-y."""
    if OUTPUT_NAME.search(os.path.basename(pth)):
        return True
    for m in re.finditer(re.escape(pth), instr):
        window = instr[max(0, m.start() - 90):m.start()]
        if OUTPUT_VERB.search(window):
            return True
    return False


NEG_MARK = re.compile(r"(reject|rejected|deny|denied|denies|disallow|forbidden|invalid|illegal|"
                      r"must not|should not|confusab|bypass|traversal|malicious|attack|adversar|"
                      r"blocked?|refuse[ds]?)", re.I)


def _framed_as_example(instr, pth):
    """The path is only an illustrative example or a NEGATIVE example to reject (skip): an
    example/reject marker sits within ~40 chars before OR after the mention."""
    for m in re.finditer(re.escape(pth), instr):
        before = instr[max(0, m.start() - 40):m.start()]
        after = instr[m.end():m.end() + 40]
        if EXAMPLE_MARK.search(before):
            return True
        if NEG_MARK.search(before) or NEG_MARK.search(after):
            return True
    return False


def _corpus(root, p):
    """All text an existence check may match against: env tree names, Dockerfile,
    setup/solve scripts, and the literal environment file paths."""
    parts = []
    env = p["environment"]
    env_files = []
    if os.path.isdir(env):
        for dp, _dirs, files in os.walk(env):
            for fn in files:
                rel = os.path.relpath(os.path.join(dp, fn), env)
                env_files.append(rel)
                if fn.endswith((".py", ".sh", ".txt", ".md", ".json", ".yaml", ".yml", ".cfg",
                                ".ini", ".c", ".h", ".go", ".rs", ".js", ".ts", ".cpp", ".cc",
                                ".java", ".rb", ".pl", ".tpl", ".j2", ".template")):
                    parts.append(read_text(os.path.join(dp, fn)))
    for key in ("Dockerfile", "solve.sh", "test.sh", "test_outputs.py"):
        if os.path.isfile(p[key]):
            parts.append(read_text(p[key]))
    return "\n".join(parts), env_files


def _shipped(path, corpus, env_files):
    base = os.path.basename(path)
    if base and base in corpus:
        return True
    # path (or its tail) appears verbatim in build/setup/verifier text
    if path in corpus:
        return True
    tail = "/".join(path.strip("/").split("/")[-2:])
    if tail and tail in corpus:
        return True
    # matches a shipped environment file by basename
    for ef in env_files:
        if os.path.basename(ef) == base:
            return True
    # generated-series instance (foo_000.json): match the non-digit prefix against the
    # corpus / a generator script, since the literal numbered file is produced at build/run
    stem = os.path.splitext(base)[0]
    prefix = DIGIT_RUN.split(stem)[0].rstrip("_-")
    if len(prefix) >= 4 and prefix in corpus:
        return True
    return False


def check_task(name, root):
    p = task_paths(root)
    instr = read_text(p["instruction.md"])
    if not instr.strip():
        return [finding(name, "contract", PASS, "contract-paths-ok")]
    corpus, env_files = _corpus(root, p)
    cands = set()
    for m in PATHY.finditer(instr):
        pth = m.group(1)
        if pth.startswith(SYSTEM_PREFIXES):
            continue
        if _is_file_artifact(pth):
            cands.add(pth)
    for m in BACKTICK_FILE.finditer(instr):
        if _is_file_artifact(m.group(1)):
            cands.add(m.group(1))
    # the agent's own solution script is never a shipped input
    cands = {c for c in cands if os.path.basename(c) not in ("solve.sh", "solution.sh")}
    # drop outputs the agent produces and illustrative example filenames — only missing
    # shipped INPUTS are contract breaks
    cands = {c for c in cands if not _framed_as_output(instr, c) and not _framed_as_example(instr, c)}
    missing = sorted(c for c in cands if not _shipped(c, corpus, env_files))
    out = []
    if missing:
        out.append(finding(
            name, "contract", FAIL, "instruction-path-not-shipped",
            detail=f"instruction references path(s) not found in the environment, Dockerfile, "
                   f"setup, or oracle: {missing[:6]}"
                   f"{' …' if len(missing) > 6 else ''}.",
            location="instruction.md",
            fix="Ship the referenced file/dir in environment/, create it in the Dockerfile, "
                "or correct the path in the instruction."))
    else:
        out.append(finding(name, "contract", PASS, "contract-paths-ok"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks")
    ap.add_argument("--out", default="findings_contract_paths.json")
    args = ap.parse_args()
    findings = []
    tasks = discover_tasks(args.tasks)
    for name, root in tasks:
        findings.extend(check_task(name, root))
    n = emit(findings, args.out)
    fails = sum(1 for f in findings if f["severity"] == FAIL)
    print(f"[contract-paths] {len(tasks)} tasks, {n} findings, {fails} FAIL -> {args.out}")


if __name__ == "__main__":
    main()
