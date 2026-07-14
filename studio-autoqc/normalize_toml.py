#!/usr/bin/env python3
"""Normalize task.toml to the Reflection spec schema: keep the 8 spec [metadata] keys +
the functional Harbor sections (agent/verifier/environment/solution incl .env, artifacts,
schema_version); drop redundant metadata dupes + top-level cruft; ensure avg_at_8 present.

Run with the modalenv python (tomllib + tomli_w). --apply to write; default dry-run.
"""
import os, re, sys, json, tomllib, tomli_w

BASE = "/Users/mahmoodmapara/Desktop/terminal-bench-qc/_local/refl_eval_pool/delivery_0708"
SCORES = json.load(open("/Users/mahmoodmapara/Desktop/terminal-bench-qc/_local/qc_out_eval_pool/selected2500.json"))
META_ORDER = ["category","subcategory","task_objective","artifact_type",
              "expert_time_estimate_hours","model_tested","agent_tested","avg_at_8"]
ALLOWED_TOP = ["schema_version","artifacts","metadata","agent","verifier","environment","solution"]
PROMOTE = {"category":"diversity_category","subcategory":"diversity_subcategory",
           "task_objective":"task_objectives","artifact_type":"artifact_types"}
# redundant metadata lines to strip from malformed files so they parse
REDUNDANT_LINE = re.compile(r'^\s*(task_objectives|artifact_types|diversity_category|diversity_subcategory|difficulty|operation_type|expert_time_estimate_min|junior_time_estimate_min|verifier_timeout_sec|agent_timeout_sec)\s*=')

def load(fp):
    raw = open(fp, errors="replace").read()
    try:
        return tomllib.loads(raw)
    except Exception:
        # duplicate-key defect: keep the LAST occurrence of each key within its section.
        # (only used on the handful of malformed files; their values are single-line.)
        out = []; section = ""; seen = {}
        # walk bottom-up so "last wins", then reverse
        lines = raw.splitlines()
        # first pass: record section per line
        sec_of = []; cur = ""
        for ln in lines:
            h = re.match(r'\s*\[([^\]]+)\]', ln)
            if h: cur = h.group(1)
            sec_of.append(cur)
        keep = [True]*len(lines)
        seenset = set()
        for i in range(len(lines)-1, -1, -1):
            m = re.match(r'\s*([A-Za-z0-9_]+)\s*=', lines[i])
            if m:
                key = (sec_of[i], m.group(1))
                if key in seenset: keep[i] = False
                else: seenset.add(key)
        deduped = "\n".join(l for i, l in enumerate(lines) if keep[i])
        return tomllib.loads(deduped)

def normalize(task, d):
    m = dict(d.get("metadata", {}))
    for canon, dup in PROMOTE.items():
        if canon not in m and dup in m:
            m[canon] = m[dup]
    # avg_at_8: batch_2 (kebab) -> measured score; batch_1 keep existing
    if not task.startswith("task_") and task in SCORES:
        m["avg_at_8"] = SCORES[task]
    # snap avg_at_8 to the nearest 1/8 (a task run over 7 or 9 attempts yields a
    # non-eighth value like 0.111; Reflection expects k/8). Out-of-range left as-is.
    a = m.get("avg_at_8")
    if isinstance(a, (int, float)) and 0 <= a <= 1:
        m["avg_at_8"] = round(a * 8) / 8
    newmeta = {k: m[k] for k in META_ORDER if k in m}
    out = {}
    for k in ALLOWED_TOP:
        if k == "metadata":
            out["metadata"] = newmeta
        elif k in d:
            out[k] = d[k]
    return out

def main():
    apply = "--apply" in sys.argv
    tasks = sorted(t for t in os.listdir(BASE) if os.path.isdir(f"{BASE}/{t}"))
    changed = 0; added_avg8 = 0; err = 0; sample=None
    for t in tasks:
        fp = f"{BASE}/{t}/task.toml"
        if not os.path.isfile(fp): continue
        try:
            d = load(fp)
        except Exception as e:
            err += 1; print(f"  PARSE-FAIL {t}: {str(e)[:60]}"); continue
        had_avg8 = "avg_at_8" in d.get("metadata", {})
        out = normalize(t, d)
        if not had_avg8 and "avg_at_8" in out["metadata"]: added_avg8 += 1
        new_text = tomli_w.dumps(out)
        if new_text != open(fp, errors="replace").read():
            changed += 1
            if sample is None: sample=(t,new_text)
            if apply: open(fp, "w").write(new_text)
    print(f"tasks: {len(tasks)} | would-change: {changed} | avg_at_8 added: {added_avg8} | parse-fail: {err} | apply={apply}")
    if sample and not apply:
        print(f"\n--- sample normalized ({sample[0]}) [metadata] ---")
        print("\n".join(l for l in sample[1].splitlines() if not l.startswith("[") or "metadata" in l)[:1])
        import re as _re
        m=_re.search(r'\[metadata\].*?(?=\n\[|\Z)', sample[1], _re.S)
        print(m.group(0) if m else "")

if __name__ == "__main__":
    main()
