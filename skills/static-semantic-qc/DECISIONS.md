# Decision log

## 2026-07-15 — Deterministic instruction token limit

- Count the full `instruction.md` with tiktoken's `o200k_base` encoding instead of
  estimating tokens as `len(text) // 4`.
- Treat the Reflection requirement as strictly fewer than 1,500 tokens, so an
  instruction with exactly 1,500 tokens fails the gate.
- Document `o200k_base` as a deterministic proxy rather than claiming it is the
  exact tokenizer used by Qwen.

## 2026-07-15 — Fail closed when a mandatory reviewer check is skipped

- Require explicit `Q1:` through `Q4:` evidence in both reviewer prompt versions
  and in the programmatic reviewer output contract.
- Emit `mandatory-check-not-assessed` as a FAIL when any label is absent, including
  Q4 (protected ground truth), so a complete set of dimension findings cannot hide
  a skipped mandatory check.
