#!/usr/bin/env python3
"""Dataset-level decontamination + near-duplicate detection (deterministic).

Two checks the clients explicitly require (NVIDIA/Reflection decontamination;
GDM cross-delivery overlap):

  1. Contamination vs public benchmarks — each OTS task instruction is compared
     to the public Terminal-Bench corpus (references/golden/decontam_corpus.jsonl)
     by TF-IDF cosine similarity. High similarity ⇒ the task may be a public
     benchmark task (contamination / trivially searchable).
  2. Near-duplicate / template reuse — OTS tasks are compared to each OTHER; high
     pairwise similarity ⇒ low diversity from template reuse. Reflection requires
     pairwise cosine < 0.90 (all-MiniLM-L6-v2) across THREE artifacts — instruction.md,
     solve.sh, and test_outputs.py — so all three are checked (instructions also at the
     tuned 0.6 sensitivity for contamination-style overlap; solve/test at the 0.90 bar).

Two similarity backends, same thresholds and same report:
  - `--method tfidf` (default): stdlib TF-IDF cosine over word tokens. Deterministic,
    no model, no install.
  - `--method embed`: sentence-embedding cosine — the embedding methodology NVIDIA /
    Reflection ask for. Needs `pip install sentence-transformers numpy`; pick the
    model with `--model` (default all-MiniLM-L6-v2).

Usage:
    python decontaminate.py <tasks-dir> \
        [--corpus data/decontam_corpus.jsonl] \
        [--method tfidf|embed] [--model all-MiniLM-L6-v2] \
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


def pairwise_dups(texts, names, threshold, method, model, title, artifact):
    """Flag pairs of tasks whose `artifact` text is >= threshold cosine-similar.

    Used for the solve.sh / test_outputs.py near-duplicate bar (Reflection's 0.90).
    Self-contained so it works for both the tfidf (default) and embed backends.
    """
    idxs = [i for i, t in enumerate(texts) if t and t.strip()]
    out = []
    if len(idxs) < 2:
        return out
    if method == "embed":
        import numpy as np
        from sentence_transformers import SentenceTransformer
        m = SentenceTransformer(model)
        E = np.asarray(m.encode([texts[i] for i in idxs],
                                normalize_embeddings=True, show_progress_bar=False))

        def sim(a, b):
            return float(E[a] @ E[b])
    else:
        toks = [tokens(texts[i]) for i in idxs]
        idf = build_idf(toks)
        vecs = [vec(t, idf) for t in toks]

        def sim(a, b):
            return cosine(vecs[a], vecs[b])

    for a in range(len(idxs)):
        for b in range(a + 1, len(idxs)):
            s = sim(a, b)
            if s >= threshold:
                ia, ib = idxs[a], idxs[b]
                out.append(finding(
                    names[ia], "dataset", WARN, title,
                    detail=f"{artifact} is {s:.2f} cosine-similar to '{names[ib]}' "
                           f"(≥ {threshold}) — template reuse / low diversity.",
                    location=artifact,
                    fix=f"Make the two tasks' {artifact} meaningfully distinct, or dedupe."))
    return out


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
    # prefer the committed data/ copy; fall back to the local references/ copy
    default_corpus = os.path.join(here, "..", "data", "decontam_corpus.jsonl")
    if not os.path.isfile(default_corpus):
        default_corpus = os.path.join(here, "..", "references", "golden",
                                      "decontam_corpus.jsonl")
    ap.add_argument("--corpus", default=default_corpus)
    ap.add_argument("--contam-threshold", type=float, default=0.5)
    ap.add_argument("--dup-threshold", type=float, default=0.6)
    ap.add_argument("--strict-dup-threshold", type=float, default=0.90,
                    help="Reflection's pairwise-similarity bar for solve.sh/test_outputs.py")
    ap.add_argument("--method", choices=["tfidf", "embed"], default="tfidf",
                    help="tfidf (stdlib default) or embed (sentence-transformers cosine)")
    ap.add_argument("--model", default="all-MiniLM-L6-v2",
                    help="sentence-transformers model id for --method embed")
    ap.add_argument("--out", default="findings_dataset.json")
    args = ap.parse_args()

    tasks = discover_tasks(args.tasks)
    q_names, q_toks, q_text = [], [], []
    solve_text, test_text = [], []
    for name, root in tasks:
        p = task_paths(root)
        instr = read_text(p["instruction.md"])
        q_names.append(name)
        q_toks.append(tokens(instr))
        q_text.append(instr)
        solve_text.append(read_text(p["solve.sh"]))
        test_text.append(read_text(p["test_outputs.py"]))

    corpus = load_corpus(args.corpus)
    c_names = [r.get("name", f"corpus_{i}") for i, r in enumerate(corpus)]
    c_src = [r.get("source", "public") for r in corpus]
    c_toks = [tokens(r.get("instruction", "")) for r in corpus]
    c_text = [r.get("instruction", "") for r in corpus]

    # similarity backend: TF-IDF cosine (stdlib default) or sentence-embedding
    # cosine (the methodology NVIDIA/Reflection ask for; needs sentence-transformers).
    if args.method == "embed":
        try:
            import numpy as np
            from sentence_transformers import SentenceTransformer
        except ImportError:
            import sys
            sys.exit("--method embed needs: pip install sentence-transformers numpy")
        model = SentenceTransformer(args.model)
        qE = np.asarray(model.encode(q_text, normalize_embeddings=True,
                                     show_progress_bar=False))
        cE = (np.asarray(model.encode(c_text, normalize_embeddings=True,
                                      show_progress_bar=False))
              if corpus else np.zeros((0, qE.shape[1] if len(qE) else 1)))

        def contam(i):
            if len(cE) == 0:
                return 0.0, -1
            s = cE @ qE[i]
            j = int(s.argmax())
            return float(s[j]), j

        def dup(i, k):
            return float(qE[i] @ qE[k])
        print(f"[decontaminate] embedding backend: {args.model}")
    else:
        idf = build_idf(q_toks + c_toks)
        q_vecs = [vec(t, idf) for t in q_toks]
        c_vecs = [vec(t, idf) for t in c_toks]

        def contam(i):
            best, best_j = 0.0, -1
            for j, cv in enumerate(c_vecs):
                s = cosine(q_vecs[i], cv)
                if s > best:
                    best, best_j = s, j
            return best, best_j

        def dup(i, k):
            return cosine(q_vecs[i], q_vecs[k])

    findings = []

    # 1. contamination vs public corpus
    if corpus:
        for i, name in enumerate(q_names):
            best, best_j = contam(i)
            if best >= args.contam_threshold:
                sev = FAIL if best >= args.contam_threshold + 0.2 else WARN
                findings.append(finding(
                    name, "dataset", sev, "public-benchmark-contamination",
                    detail=f"Instruction is {best:.2f} cosine-similar to "
                           f"{c_src[best_j]} task '{c_names[best_j]}'. Possible "
                           "contamination / trivially searchable if internet is allowed.",
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

    # 2. intra-set near-duplicates — instruction.md (tuned 0.6 sensitivity)
    n = len(q_names)
    for i in range(n):
        for k in range(i + 1, n):
            s = dup(i, k)
            if s >= args.dup_threshold:
                findings.append(finding(
                    q_names[i], "dataset", WARN, "near-duplicate-in-set",
                    detail=f"{s:.2f} cosine-similar to '{q_names[k]}' in the same "
                           "set — possible template reuse / low diversity.",
                    location="instruction.md",
                    fix="Confirm the two tasks are meaningfully distinct; dedupe if not."))

    # 2b. solve.sh + test_outputs.py near-duplicates at Reflection's 0.90 bar
    findings += pairwise_dups(solve_text, q_names, args.strict_dup_threshold,
                              args.method, args.model, "near-duplicate-solve", "solution/solve.sh")
    findings += pairwise_dups(test_text, q_names, args.strict_dup_threshold,
                              args.method, args.model, "near-duplicate-test", "tests/test_outputs.py")

    n_out = emit(findings, args.out)
    fails = sum(1 for f in findings if f["severity"] == FAIL)
    warns = sum(1 for f in findings if f["severity"] == WARN)
    print(f"[decontaminate] {len(tasks)} tasks vs {len(corpus)} corpus tasks: "
          f"{n_out} findings, {fails} FAIL, {warns} WARN -> {args.out}")


if __name__ == "__main__":
    main()
