#!/usr/bin/env python3
"""Build the decontamination corpus from the public benchmarks clients name.

NVIDIA's acceptance criteria require no contamination with **Terminal-Bench,
SWE-Bench, LiveCodeBench, and Aider**. This script assembles one JSONL corpus
(`{name, source, instruction}` per line, the schema `decontaminate.py` reads) from
all four so the contamination check covers every named benchmark, not just TB.

Sources & how each is obtained:
  - terminal-bench  : kept from the existing corpus (the 244 TB instructions).
  - swe-bench       : princeton-nlp/SWE-bench_Verified `problem_statement`, fetched
                      live from the HF datasets-server REST API (no HF lib needed).
  - livecodebench   : `question_content` from the LCB code_generation_lite test
                      split. That file is a ~400 MB git-LFS blob, so pass a local
                      copy via --lcb-file instead of re-downloading each run:
                        curl -sL https://huggingface.co/datasets/livecodebench/\\
                          code_generation_lite/resolve/main/test.jsonl -o /tmp/lcb_test.jsonl
  - aider           : Exercism `.docs/instructions.md` from the Aider polyglot
                      benchmark repo (git clone --depth 1
                      https://github.com/Aider-AI/polyglot-benchmark <dir>).

Re-runnable: TB rows in the existing corpus are preserved; any prior swe/lcb/aider
rows are replaced. Writes data/decontam_corpus.jsonl and mirrors it to
references/golden/decontam_corpus.jsonl.

Usage:
    python build_decontam_corpus.py \\
        [--lcb-file /tmp/lcb_test.jsonl] \\
        [--aider-dir references/aider-polyglot-src] \\
        [--swe-limit 500]
"""
import argparse
import json
import os
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data", "decontam_corpus.jsonl")
MIRROR = os.path.join(ROOT, "references", "golden", "decontam_corpus.jsonl")
REBUILT_SOURCES = {"swe-bench", "livecodebench", "aider-polyglot"}


def _norm(s):
    return " ".join((s or "").split())


def load_jsonl(path):
    out = []
    if path and os.path.isfile(path):
        for line in open(path, errors="replace"):
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    return out


def fetch_swe(limit):
    """SWE-bench_Verified problem_statements via the HF datasets-server."""
    rows, offset = [], 0
    base = ("https://datasets-server.huggingface.co/rows?dataset="
            "princeton-nlp/SWE-bench_Verified&config=default&split=test")
    while offset < limit:
        n = min(100, limit - offset)
        url = f"{base}&offset={offset}&length={n}"
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                data = json.load(r)
        except Exception as e:
            print(f"  ! swe-bench fetch failed at offset {offset}: {e}")
            break
        batch = data.get("rows", [])
        if not batch:
            break
        for item in batch:
            row = item.get("row", {})
            ps = _norm(row.get("problem_statement"))
            if ps:
                rows.append({"name": row.get("instance_id", f"swe_{offset}"),
                             "source": "swe-bench", "instruction": ps})
        offset += len(batch)
    return rows


def fetch_lcb(path):
    """LiveCodeBench question_content from a local test.jsonl (LFS blob)."""
    rows = []
    for r in load_jsonl(path):
        q = _norm(r.get("question_content"))
        if q:
            rows.append({"name": r.get("question_title") or r.get("question_id", "lcb"),
                         "source": "livecodebench", "instruction": q})
    return rows


def fetch_aider(root):
    """Exercism instruction.md (+ introduction.md) from the polyglot repo."""
    rows = []
    if not os.path.isdir(root):
        return rows
    for dirpath, _, files in os.walk(root):
        if os.path.basename(dirpath) != ".docs" or "instructions.md" not in files:
            continue
        text = open(os.path.join(dirpath, "instructions.md"), errors="replace").read()
        intro = os.path.join(dirpath, "introduction.md")
        if os.path.isfile(intro):
            text = open(intro, errors="replace").read() + "\n" + text
        # name: <lang>/<exercise>
        parts = dirpath.replace(root, "").strip("/").split("/")
        lang = parts[0] if parts else "?"
        ex = parts[-2] if len(parts) >= 2 else os.path.basename(dirpath)
        q = _norm(text)
        if q:
            rows.append({"name": f"{lang}/{ex}", "source": "aider-polyglot",
                         "instruction": q})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lcb-file", default="/tmp/lcb_test.jsonl")
    ap.add_argument("--aider-dir",
                    default=os.path.join(ROOT, "references", "aider-polyglot-src"))
    ap.add_argument("--swe-limit", type=int, default=500)
    args = ap.parse_args()

    existing = load_jsonl(DATA)
    kept = [r for r in existing if r.get("source") not in REBUILT_SOURCES]
    print(f"kept {len(kept)} existing rows (TB etc.)")

    swe = fetch_swe(args.swe_limit)
    print(f"swe-bench:     {len(swe)}")
    lcb = fetch_lcb(args.lcb_file)
    print(f"livecodebench: {len(lcb)}" + ("" if lcb else f"  (no rows — pass --lcb-file)"))
    aider = fetch_aider(args.aider_dir)
    print(f"aider-polyglot:{len(aider)}" + ("" if aider else "  (no rows — clone the repo)"))

    # dedup by (source, name)
    merged, seen = [], set()
    for r in kept + swe + lcb + aider:
        key = (r.get("source"), r.get("name"))
        if key in seen:
            continue
        seen.add(key)
        merged.append(r)

    with open(DATA, "w") as f:
        for r in merged:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.makedirs(os.path.dirname(MIRROR), exist_ok=True)
    with open(MIRROR, "w") as f:
        for r in merged:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    from collections import Counter
    dist = Counter(r.get("source") for r in merged)
    print(f"\ncorpus: {len(merged)} rows -> {DATA} (+ mirror)")
    for s, n in dist.most_common():
        print(f"  {s}: {n}")


if __name__ == "__main__":
    main()
