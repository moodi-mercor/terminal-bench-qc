#!/usr/bin/env python3
"""Dataset-level decontamination + near-duplicate detection (deterministic).

Two checks the clients explicitly require (NVIDIA/Reflection decontamination;
GDM cross-delivery overlap):

  1. Contamination vs public benchmarks — each OTS task instruction is compared
     to the public Terminal-Bench corpus (references/golden/decontam_corpus.jsonl)
     by TF-IDF cosine similarity. High similarity ⇒ the task may be a public
     benchmark task (contamination / trivially searchable).
  2. Near-duplicate / template reuse — OTS tasks are compared to each OTHER; high
     pairwise similarity ⇒ low diversity from template reuse.

This is a stdlib lexical baseline (TF-IDF cosine over word tokens). It is
deterministic and needs no model. For the embedding-based methodology the clients
ask for, swap the vectorizer for sentence embeddings and keep the same cosine
thresholds — the report format is identical.

Usage:
    python decontaminate.py <tasks-dir> \
        [--corpus references/golden/decontam_corpus.jsonl] \
        [--contam-threshold 0.5] [--dup-threshold 0.6] \
        [--out findings_dataset.json]

Emits findings with area="dataset".
"""
import argparse
import json
import math
import os
import re
from collections import Counter

from common import FAIL, WARN, PASS, finding, emit, read_text, discover_tasks, task_paths

WORD = re.compile(r"[a-z0-9]+")
STOP = set("the a an and or of to in is are be for on with that this you your it "
           "as at by from must should will can not no if then else when each all "
           "any into out has have was were which who what where how".split())


def tokens(text):
    return [w for w in WORD.findall(text.lower()) if w not in STOP and len(w) > 1]


def tf(toks):
    c = Counter(toks)
    n = len(toks) or 1
    return {w: cnt / n for w, cnt in c.items()}


def build_idf(docs):
    df = Counter()
    for toks in docs:
        for w in set(toks):
            df[w] += 1
    n = len(docs) or 1
    return {w: math.log((1 + n) / (1 + d)) + 1 for w, d in df.items()}


def vec(toks, idf):
    t = tf(toks)
    return {w: t[w] * idf.get(w, 0.0) for w in t}


def cosine(a, b):
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    num = sum(a[w] * b[w] for w in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return num / (na * nb) if na and nb else 0.0


def load_corpus(path):
    rows = []
    if path and os.path.isfile(path):
        for line in open(path):
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks")
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--corpus",
                    default=os.path.join(here, "..", "references", "golden",
                                         "decontam_corpus.jsonl"))
    ap.add_argument("--contam-threshold", type=float, default=0.5)
    ap.add_argument("--dup-threshold", type=float, default=0.6)
    ap.add_argument("--out", default="findings_dataset.json")
    args = ap.parse_args()

    tasks = discover_tasks(args.tasks)
    q_names, q_toks = [], []
    for name, root in tasks:
        instr = read_text(task_paths(root)["instruction.md"])
        q_names.append(name)
        q_toks.append(tokens(instr))

    corpus = load_corpus(args.corpus)
    c_names = [r.get("name", f"corpus_{i}") for i, r in enumerate(corpus)]
    c_toks = [tokens(r.get("instruction", "")) for r in corpus]

    idf = build_idf(q_toks + c_toks)
    q_vecs = [vec(t, idf) for t in q_toks]
    c_vecs = [vec(t, idf) for t in c_toks]

    findings = []

    # 1. contamination vs public corpus
    if c_vecs:
        for i, name in enumerate(q_names):
            best, best_j = 0.0, -1
            for j, cv in enumerate(c_vecs):
                s = cosine(q_vecs[i], cv)
                if s > best:
                    best, best_j = s, j
            if best >= args.contam_threshold:
                sev = FAIL if best >= args.contam_threshold + 0.2 else WARN
                findings.append(finding(
                    name, "dataset", sev, "public-benchmark-contamination",
                    detail=f"Instruction is {best:.2f} cosine-similar to public TB "
                           f"task '{c_names[best_j]}'. Possible contamination / "
                           "trivially searchable if internet is allowed.",
                    location="instruction.md",
                    fix="Confirm the task is not a public benchmark task; replace or "
                        "differentiate. Re-check with embedding similarity."))
            else:
                findings.append(finding(name, "dataset", PASS,
                                        "no-public-contamination",
                                        detail=f"max public similarity {best:.2f} "
                                               f"(< {args.contam_threshold})."))
    else:
        print("! no corpus loaded — skipping contamination check")

    # 2. intra-set near-duplicates
    n = len(q_names)
    for i in range(n):
        for k in range(i + 1, n):
            s = cosine(q_vecs[i], q_vecs[k])
            if s >= args.dup_threshold:
                findings.append(finding(
                    q_names[i], "dataset", WARN, "near-duplicate-in-set",
                    detail=f"{s:.2f} cosine-similar to '{q_names[k]}' in the same "
                           "set — possible template reuse / low diversity.",
                    location="instruction.md",
                    fix="Confirm the two tasks are meaningfully distinct; dedupe if not."))

    n_out = emit(findings, args.out)
    fails = sum(1 for f in findings if f["severity"] == FAIL)
    warns = sum(1 for f in findings if f["severity"] == WARN)
    print(f"[decontaminate] {len(tasks)} tasks vs {len(corpus)} corpus tasks: "
          f"{n_out} findings, {fails} FAIL, {warns} WARN -> {args.out}")


if __name__ == "__main__":
    main()
