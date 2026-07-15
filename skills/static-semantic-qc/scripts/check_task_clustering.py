#!/usr/bin/env python3
"""Dataset-level SKILL-CLUSTERING check (deterministic, read-only).

The pairwise cosine<0.90 diversity check misses tight *clusters*: many tasks that reuse
one rich environment/seed and test the SAME underlying skill+tech+setup, even when the
surface wording differs enough to stay under 0.90. This check builds a per-task SKILL
FINGERPRINT (tech + operation + artifact + environment scaffolding + solve.sh imports),
clusters tasks by fingerprint overlap, and flags dense clusters + per-seed over-use.

Emits area="dataset" findings:
  - skill-cluster-too-large    a group of >=MIN_CLUSTER tasks sharing a skill fingerprint
  - seed-env-over-used         one scenario/seed signature > SEED_CAP% of the dataset
Writes a cluster map (cluster_map.json) alongside the findings.

Usage: python check_task_clustering.py <tasks-dir> [--out findings_clustering.json]
       [--jaccard 0.6] [--min-cluster 8] [--seed-cap 0.05]
"""
import argparse, json, os, re
from collections import Counter, defaultdict
from common import FAIL, WARN, PASS, finding, emit, read_text, discover_tasks, task_paths

DATASET = "__dataset__"

# distinctive technical vocabulary (tools / formats / mechanisms / setup)
TECH = ["sqlite","parquet","arrow","hmac","sha256","sha1","md5","crc32c","crc32","crc","jq",
    "locale","tar","gzip","zstd","zlib","lz4","protobuf","msgpack","base64","hex","fsync",
    "wal","journal","manifest","quarantine","replay","offset","binary","daemon","queue",
    "append-only","atomic","idempoten","reconcil","rename","checkpoint","ledger","spool",
    "read-only","air-gap","airgap","mmap","struct","varint","little-endian","big-endian",
    "watermark","dedup","backpressure","rotation","sidecar","envelope","segment"]
SETUP = ["air-gap","airgap","read-only","no network","pre-installed","offline","ephemeral"]
OPS = ["parse","verify","reconcile","validate","recover","replay","dedup","compact","migrate",
       "rotate","repair","audit","export","ingest","serialize","checksum","rebuild"]

def fingerprint(txt):
    t = txt.lower()
    return frozenset([k for k in TECH if k in t] + ["op:"+o for o in OPS if o in t])

def seed_sig(txt):
    t = txt.lower()
    # dominant scenario noun + setup flags = the "seed environment" signature
    scen = [w for w in ("kiosk","microgrid","maritime","vessel","port","exhibit","telemetry",
            "spool","biotelemetry","coldchain","broadcast","calibration","clinical","hpc")
            if w in t]
    setup = [s for s in SETUP if s in t]
    return (scen[0] if scen else "?", tuple(sorted(setup)))

def jac(a, b):
    return len(a & b) / len(a | b) if (a | b) else 0.0

def check_dataset(tasks_dir, jaccard, min_cluster, seed_cap):
    tasks = list(discover_tasks(tasks_dir))
    raw = {}; seeds = {}
    for name, root in tasks:
        p = task_paths(root)
        txt = (read_text(p["instruction.md"]) or "") + "\n" + (read_text(p["solve.sh"]) or "")
        raw[name] = fingerprint(txt)
        seeds[name] = seed_sig(txt)
    names = list(raw)
    total = len(names)
    # DOCUMENT-FREQUENCY FILTER: ubiquitous tokens (tar/hex/sha256 appear in most tasks)
    # carry no diversity signal and lump everything into one mega-cluster. Keep only
    # DISTINCTIVE tokens (present in < DF_MAX of the corpus) as the skill fingerprint.
    DF_MAX = 0.35
    df = Counter(k for n in names for k in raw[n])
    fps = {n: frozenset(k for k in raw[n] if df[k] / total < DF_MAX) for n in names}
    # inverted index over distinctive tokens -> candidate pairs only (efficient, global)
    parent = {n: n for n in names}
    def find(x):
        while parent[x] != x: parent[x] = parent[parent[x]]; x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb: parent[ra] = rb
    postings = defaultdict(list)
    for n in names:
        for k in fps[n]:
            postings[k].append(n)
    seen = set()
    for k, group in postings.items():
        if len(group) > 400:   # a still-common token: skip to bound cost
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if (a, b) in seen: continue
                seen.add((a, b))
                if len(fps[a]) >= 2 and len(fps[b]) >= 2 and jac(fps[a], fps[b]) >= jaccard:
                    union(a, b)
    clusters = defaultdict(list)
    for n in names:
        if fps[n]: clusters[find(n)].append(n)
    big = sorted([c for c in clusters.values() if len(c) >= min_cluster], key=len, reverse=True)
    out = []
    total = len(names)
    for c in big:
        # describe the shared skill = tokens common to >=70% of the cluster
        common = [k for k, v in Counter(k for n in c for k in fps[n]).items() if v / len(c) >= 0.7]
        out.append(finding(DATASET, "dataset", FAIL, "skill-cluster-too-large",
            detail=f"{len(c)} tasks ({len(c)/total*100:.1f}%) share one skill fingerprint "
                   f"[{', '.join(sorted(common)[:8])}] — same underlying skill/tech/setup, "
                   "not meaningfully different. e.g. " + ", ".join(sorted(c)[:3]),
            location="dataset",
            fix="Cap tasks per seed environment; diversify the skill (tech+operation+artifact), "
                "not just the wording. Thin this cluster or replace with distinct-skill tasks."))
    # per-seed over-use
    seed_counts = Counter(seeds[n][0] for n in names if seeds[n][0] != "?")
    for seed, cnt in seed_counts.most_common():
        if cnt / total > seed_cap:
            out.append(finding(DATASET, "dataset", FAIL, "seed-env-over-used",
                detail=f"scenario seed '{seed}' appears in {cnt} tasks ({cnt/total*100:.1f}% > "
                       f"{seed_cap*100:.0f}%) — one environment over-seeded.",
                location="dataset",
                fix=f"Reduce '{seed}'-seeded tasks below {seed_cap*100:.0f}% of the dataset."))
    if not out:
        out.append(finding(DATASET, "dataset", PASS, "skill-diversity-ok",
            detail=f"{total} tasks; no skill cluster >= {min_cluster} and no seed > {seed_cap*100:.0f}%."))
    cmap = {f"cluster_{i}": sorted(c) for i, c in enumerate(big)}
    return out, cmap

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks"); ap.add_argument("--out", default="findings_clustering.json")
    ap.add_argument("--jaccard", type=float, default=0.6)
    ap.add_argument("--min-cluster", type=int, default=8)
    ap.add_argument("--seed-cap", type=float, default=0.05)
    ap.add_argument("--map-out", default="")
    a = ap.parse_args()
    findings, cmap = check_dataset(a.tasks, a.jaccard, a.min_cluster, a.seed_cap)
    emit(findings, a.out)
    if a.map_out:
        json.dump(cmap, open(a.map_out, "w"), indent=1)
    nf = sum(1 for f in findings if f["severity"] == FAIL)
    print(f"{len(findings)} findings, {nf} FAIL | clusters: {len(cmap)} -> {a.out}")

if __name__ == "__main__":
    main()
