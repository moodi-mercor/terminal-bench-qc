#!/usr/bin/env python3
"""AST classification of a pytest verifier's assertions (deterministic, read-only).

Other gates need to tell a BRITTLE hardcoded-literal verifier from a legitimate
FUNCTIONAL one without the regex false positives that sank the earlier
`weak-verifier-no-value-compare` attempt (it counted `==` in shell redirects /
`[[ x == y ]]` / base64, and flagged every functional verifier that had no `==`).
Parsing the Python with `ast` makes the distinction precise.

For each test function we record:
  functional      runs code / queries a service or DB / parses a produced artifact
                  (subprocess, os.system/popen, requests/httpx/urlopen, socket,
                  sqlite3/psycopg2/redis ..., json.load(s), yaml.load, pd.read_*,
                  csv.reader, importlib/runpy/exec_module/__import__, Image.open)
  recompute       the expected value is DERIVED here (hashlib/hmac/blake2, sum/len/
                  sorted/max/min/Counter/any/all, arithmetic, a comprehension)
  literal_cmp     an (in)equality compares a value against a bare literal constant
  literal_values  the literal constants compared against (for the cheat harness)
  existence_only  the only failure-capable check is a path-existence call
  reads_agent     opens a path under agent-writable space (/app,/workspace,/data,cwd)

`classify_file(src)` returns {"funcs": {name: {...}}, "file": {rollup booleans}}.
`classify_path(path)` reads + classifies, returning {} on unreadable/!parse.
"""
import ast
import os

AGENT_ROOTS = ("/app", "/workspace", "/data", "/srv", "/home", "/root", "./", "out/")

# dotted call names that mean the test EXECUTES / queries / parses produced output
FUNCTIONAL_CALLS = (
    "subprocess.run", "subprocess.Popen", "subprocess.check_output",
    "subprocess.check_call", "subprocess.call", "os.system", "os.popen",
    "requests.get", "requests.post", "requests.put", "requests.delete",
    "requests.head", "requests.patch", "requests.request",
    "httpx.get", "httpx.post", "httpx.request", "urlopen", "urllib.request.urlopen",
    "socket.create_connection", "socket.socket",
    "sqlite3.connect", "psycopg2.connect", "psycopg.connect", "pymysql.connect",
    "duckdb.connect", "redis.Redis", "redis.StrictRedis",
    "json.load", "json.loads", "yaml.load", "yaml.safe_load",
    "csv.reader", "csv.DictReader", "Image.open",
    "importlib.import_module", "importlib.util.spec_from_file_location",
    "runpy.run_path", "runpy.run_module", "__import__",
)
FUNCTIONAL_METHODS = ("execute", "cursor", "exec_module", "read_csv", "read_json",
                      "read_parquet", "read_excel", "read_table", "communicate")
RECOMPUTE_CALLS = ("hashlib.sha256", "hashlib.sha1", "hashlib.md5", "hashlib.sha512",
                   "hashlib.new", "hmac.new", "hashlib.blake2b", "hashlib.blake2s")
RECOMPUTE_NAMES = ("sum", "len", "sorted", "max", "min", "Counter", "any", "all",
                   "round", "abs", "set")
EXISTENCE = {"exists", "isfile", "isdir", "is_file", "is_dir", "lexists"}
LITERAL_OPS = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)
# Modules a pure literal-checker may import without "doing work". Importing anything
# ELSE (the agent's own module, numpy/pandas, a task lib) means the verifier executes
# or computes — so it is NOT a bare-literal checker. Erring large here is safe: it can
# only SUPPRESS the literal-only flag (fewer FPs), never create one.
STDLIB_SAFE = {"os", "sys", "re", "json", "pathlib", "subprocess", "math", "string",
               "collections", "base64", "hashlib", "hmac", "csv", "io", "time",
               "datetime", "glob", "shutil", "typing", "itertools", "functools",
               "pytest", "unittest", "textwrap", "decimal", "fractions", "statistics",
               "struct", "binascii", "difflib", "filecmp", "stat", "tempfile",
               "warnings", "contextlib", "pprint", "operator"}


def _dotted(node):
    """Best-effort dotted name for a Call's func (e.g. subprocess.run, json.loads)."""
    parts = []
    n = node
    while isinstance(n, ast.Attribute):
        parts.append(n.attr)
        n = n.value
    if isinstance(n, ast.Name):
        parts.append(n.id)
    return ".".join(reversed(parts))


def _is_literal(node):
    """A bare constant (or container of constants) — int/str/float/bytes/bool/None."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_is_literal(e) for e in node.elts)
    if isinstance(node, ast.Dict):
        return all(_is_literal(k) and _is_literal(v)
                   for k, v in zip(node.keys, node.values))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):  # -5
        return _is_literal(node.operand)
    return False


def _literal_value(node):
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def _classify_func(fn):
    info = {"functional": False, "recompute": False, "literal_cmp": False,
            "literal_values": [], "existence_only": False, "reads_agent": False,
            "has_assert": False}
    asserts = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Assert):
            info["has_assert"] = True
            asserts.append(n)
        if isinstance(n, ast.Call):
            dotted = _dotted(n.func)
            attr = getattr(n.func, "attr", "")
            if dotted in FUNCTIONAL_CALLS or attr in FUNCTIONAL_METHODS:
                info["functional"] = True
            if dotted in RECOMPUTE_CALLS or _dotted(n.func).split(".")[0] in ("hashlib", "hmac"):
                info["recompute"] = True
            base = getattr(n.func, "id", attr)
            if base in RECOMPUTE_NAMES:
                info["recompute"] = True
            # open() of an agent-writable path
            if base == "open" and n.args and isinstance(n.args[0], ast.Constant) \
                    and isinstance(n.args[0].value, str):
                p = n.args[0].value
                if any(p.startswith(r) for r in AGENT_ROOTS):
                    info["reads_agent"] = True
        if isinstance(n, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            info["recompute"] = True  # derives a value rather than baking it
    # literal comparisons inside asserts
    n_existence = 0
    for a in asserts:
        for cmp in ast.walk(a):
            if isinstance(cmp, ast.Compare) and any(isinstance(o, LITERAL_OPS)
                                                    for o in cmp.ops):
                operands = [cmp.left] + list(cmp.comparators)
                lits = [o for o in operands if _is_literal(o)]
                non_trivial = [o for o in operands if not _is_literal(o)]
                # a comparison of a value against a bare literal, where the value is
                # NOT itself derived by a recompute call on this line
                if lits and non_trivial:
                    info["literal_cmp"] = True
                    for o in lits:
                        v = _literal_value(o)
                        if v is not None and not (isinstance(v, bool)):
                            info["literal_values"].append(v)
            if isinstance(cmp, ast.Call):
                name = getattr(cmp.func, "attr", getattr(cmp.func, "id", ""))
                if name in EXISTENCE:
                    n_existence += 1
    # existence-only: every assert is just an existence call, nothing else
    if asserts and n_existence and not info["literal_cmp"] and not info["functional"]:
        info["existence_only"] = True
    return info


def _scan_calls(tree):
    """File-wide functional/recompute scan — catches helpers (e.g. a `_run(cmd)`
    wrapper around subprocess.run that the test functions call indirectly), which
    per-function attribution misses. This indirection is the norm in these verifiers,
    so the literal-only gate keys on the FILE-level result, not per-test."""
    functional = recompute = False
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            dotted = _dotted(n.func)
            attr = getattr(n.func, "attr", "")
            base = getattr(n.func, "id", attr)
            if dotted in FUNCTIONAL_CALLS or attr in FUNCTIONAL_METHODS:
                functional = True
            # sys.path.append/insert -> the verifier loads & runs agent code (it then
            # imports the agent's module and calls its methods, e.g. trainer.run()).
            if dotted in ("sys.path.append", "sys.path.insert"):
                functional = True
            if (dotted in RECOMPUTE_CALLS or dotted.split(".")[0] in ("hashlib", "hmac")
                    or base in RECOMPUTE_NAMES):
                recompute = True
        if isinstance(n, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            recompute = True
        # importing anything outside the stdlib-safe set means the verifier pulls in
        # the agent's module or a compute lib (numpy/pandas/...) -> it executes/derives,
        # so it is not a bare-literal checker.
        if isinstance(n, ast.Import):
            if any(a.name.split(".")[0] not in STDLIB_SAFE for a in n.names):
                functional = True
        if isinstance(n, ast.ImportFrom):
            if (n.module or "").split(".")[0] not in STDLIB_SAFE and n.level == 0:
                functional = True
    return functional, recompute


def classify_file(src):
    out = {"funcs": {}, "file": {}}
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name.startswith("test"):
            out["funcs"][node.name] = _classify_func(node)
    f = out["funcs"].values()
    file_functional, file_recompute = _scan_calls(tree)
    scored = [v for v in f if v["has_assert"]]
    out["file"] = {
        "n_tests": len(out["funcs"]),
        "any_functional": file_functional,
        "any_recompute": file_recompute,
        "any_literal_cmp": any(v["literal_cmp"] for v in f),
        "any_reads_agent": any(v["reads_agent"] for v in f),
        # brittle hardcoded-literal verifier: nothing executes / queries / parses,
        # nothing recomputes, and every scored test only compares against bare
        # literals. The whole-file functional/recompute scan keeps functional
        # verifiers (the dropped check's 34 FPs) out.
        "all_literal_only": bool(scored) and not file_functional and not file_recompute
        and all(v["literal_cmp"] for v in scored),
        "literal_values": sorted(
            {repr(x) for v in f for x in v["literal_values"]})[:25],
    }
    return out


def _open_path(node):
    """If node is `open('PATH'...)` (optionally .read()/.read().strip()), return PATH."""
    n = node
    # peel .strip()/.read()/.readlines()/int(...)/float(...) wrappers
    for _ in range(4):
        if isinstance(n, ast.Call):
            base = getattr(n.func, "attr", getattr(n.func, "id", ""))
            if base == "open" and n.args and isinstance(n.args[0], ast.Constant) \
                    and isinstance(n.args[0].value, str):
                return n.args[0].value
            if n.args:
                n = n.args[0]
                continue
            if isinstance(n.func, ast.Attribute):
                n = n.func.value
                continue
        break
    return None


def literal_io_pairs(src):
    """Best-effort (output_path, literal_value) pairs from asserts that compare a
    file read to a baked literal — `assert open(P).read().strip() == "v"` and the
    one-hop variable form `g = open(P).read(); assert g == "v"`. Feeds the cheat
    harness: a no-op that writes `v` to `P` passes such a verifier."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    # var -> path, from `var = open('PATH')...`
    var_path = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and len(n.targets) == 1 \
                and isinstance(n.targets[0], ast.Name):
            p = _open_path(n.value)
            if p:
                var_path[n.targets[0].id] = p
    pairs = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Compare) or not any(isinstance(o, ast.Eq) for o in n.ops):
            continue
        operands = [n.left] + list(n.comparators)
        lits = [o for o in operands if isinstance(o, ast.Constant)
                and not isinstance(o.value, bool)]
        if not lits:
            continue
        path = None
        for o in operands:
            path = _open_path(o)
            if path:
                break
            if isinstance(o, ast.Name) and o.id in var_path:
                path = var_path[o.id]
                break
        if path:
            pairs.append((path, lits[0].value))
    return pairs


def classify_path(path):
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return classify_file(fh.read())
    except OSError:
        return {}


if __name__ == "__main__":
    import json
    import sys
    print(json.dumps(classify_path(sys.argv[1]), indent=2, default=str))
