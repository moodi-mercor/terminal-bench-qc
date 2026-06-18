# Semantic Review Dispatch (Layer 3)

The static layers (0-1) are deterministic and catch structural/leak/metadata
defects. They cannot judge instruction↔test alignment, brittleness, phantom
tests, or over-specification — those need a reader. Layer 3 dispatches one
sub-agent per task (parallelize across tasks) that applies the imported
`terminal-bench-review` checklist and returns findings in the shared schema so
they aggregate with the static layers.

## How to run

For each task (or a sample), dispatch a sub-agent with the prompt below. Collect
each agent's JSON array and write them into the same `qc_out/` findings dir as
the static layers, then re-run `aggregate.py` so the SSOT and defect
distribution include semantic findings.

Run static QC FIRST and hand the sub-agent the static findings for that task —
it should confirm/extend, not re-derive, and should down-rank static flags it
can refute (e.g. a "leak" that is an instruction-promised sample).

## Sub-agent prompt template

> You are a Terminal-Bench task QC reviewer. Review the single task at
> `<TASK_DIR>` for SEMANTIC defects only (structure/metadata/leak statics already
> ran; their findings for this task: `<STATIC_FINDINGS_JSON>` — confirm or refute,
> don't repeat).
>
> Authoritative rubric: read
> `references/terminal-bench-review-CHECKLIST.md` and the FP/DT rules in
> `references/terminal-bench-review-SKILL.md`. Apply especially:
> - **Instruction↔test alignment (bidirectional):** every hard requirement in
>   `instruction.md` has ≥1 test; every test maps to a stated requirement OR a
>   value discoverable in the agent-visible environment. Grep the environment for
>   a value BEFORE calling a test "phantom".
> - **Brittleness (false-reject):** source-code greps, exact-string/whitespace
>   matches, file-count/layout guards that reject a correct solution. For each,
>   construct a concrete correct solution that would wrongly fail.
> - **Weakness (false-accept):** substring checks that ignore return codes;
>   format-only checks; bare existence checks.
> - **Over-specification:** enumerated fix lists, step-by-step recipes, exact
>   bug-location references, answer-key tables that hand over the solution.
> - **Hygiene:** spelling/grammar/markdown/LaTeX; clarity.
> - **Realism:** the task resembles a real developer workflow.
>
> Apply the FP rules before flagging (anti-shortcut guards alongside runtime
> tests are PASS; discoverable values are not phantom; constraint files need BOTH
> instruction-immutability AND a harbor integrity guard to be excused).
>
> Return ONLY a JSON array of findings, each:
> `{"task":"<name>","area":"instructions|tests|solution|anti_cheat",
>   "severity":"PASS|WARN|FAIL","title":"<short stable class>",
>   "location":"<file:line>","detail":"...","fix":"..."}`
> Use a short, reused `title` per defect class (e.g. "untested-requirement",
> "phantom-test", "brittle-string-match", "over-specified-instruction") so the
> distribution report groups them. If an area is clean, emit one PASS finding for
> it.

## Verdict areas the sub-agent owns

- `instructions` — alignment, over-specification, clarity, hygiene
- `tests` — coverage, phantom tests, brittleness, weak assertions, flakiness
- `solution` — correctness vs spec, hardcoding, reasonable approach
- `anti_cheat` — semantic cheat vectors the static scan can't see (the
  cheat-trace in the review skill, DT-9/10/11)
