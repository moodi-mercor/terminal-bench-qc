#!/usr/bin/env python3
"""Local-only: render _smoke_results.json into a readable Markdown report. No API calls."""
import json

SRC = "_local/tb_modules/_smoke_results.json"
OUT = "_local/tb_modules/SMOKE_REPORT.md"
ICON = {"pass": "✅", "fail": "❌", "neutral": "⚪"}


def dims(outcome):
    rows = []
    if isinstance(outcome, dict):
        for s in outcome.get("sections", []):
            for d in s.get("dimensions", []):
                rows.append((d.get("dimension") or d.get("name") or "?",
                             (d.get("status") or "?").lower(),
                             " ".join((d.get("analysis") or d.get("text") or "").split())))
    return rows


d = json.load(open(SRC))
L = ["# Terminal-Bench AutoQC — Smoke Test Results",
     "",
     "Campaign `[OTS] Terminal Bench` · 3 tasks × 3 modules · model `claude-opus-4-7` · `source: automatic`.",
     "✅ pass  ❌ fail  ⚪ neutral (candidate — never auto-fails, surfaced for human/behavioral confirm)",
     "",
     "## Summary",
     "",
     "| Task | Module | global_pass | ❌ fail | ⚪ neutral |",
     "|---|---|---|---|---|"]

for task in sorted(d):
    for mod in d[task]:
        rec = d[task][mod]
        oc = rec.get("outcome") if isinstance(rec, dict) else None
        gp = oc.get("global_pass") if isinstance(oc, dict) else "?"
        rows = dims(oc)
        nf = sum(1 for _, s, _ in rows if s == "fail")
        nn = sum(1 for _, s, _ in rows if s == "neutral")
        L.append(f"| {task} | {mod} | {'PASS' if gp else 'FAIL'} | {nf} | {nn} |")

for task in sorted(d):
    L += ["", f"## {task}", ""]
    for mod in d[task]:
        rec = d[task][mod]
        oc = rec.get("outcome") if isinstance(rec, dict) else None
        gp = oc.get("global_pass") if isinstance(oc, dict) else "?"
        L += [f"### {mod} — global_pass: {'PASS ✅' if gp else 'FAIL ❌'}", "",
              "| | Dimension | Finding |", "|---|---|---|"]
        for name, status, an in dims(oc):
            an = an.replace("|", "\\|")
            if len(an) > 300:
                an = an[:300] + "…"
            L.append(f"| {ICON.get(status,'?')} | {name} | {an} |")
        L.append("")

open(OUT, "w").write("\n".join(L))
print(f"wrote {OUT} ({len(L)} lines)")
print("\n".join(L[5:5 + 4 + 9]))  # echo the summary table
