"""Regression tests for the blocking calibration + gate coherence.

These lock in the three inconsistencies caught in review (branch @4ac42b1):
  1. gate.py made its decision on the raw `overall` verdict, ignoring `blocking`.
  2. Open-vocabulary semantic reviewer titles (untested-requirement,
     oracle-contract-violation, contract-contradiction, ...) were absent from the
     blocking allowlist, so the ENTIRE semantic layer silently downgraded to WARN.
  3. cpus=0 emits `cpus-nonpositive`, but the allowlist expected
     `placeholder-zero-resource`, so cpus=0 never blocked.

The fix inverts the calibration: block by default, downgrade only ADVISORY_FAIL.
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "shared"))

import common  # noqa: E402
import aggregate  # noqa: E402
import gate  # noqa: E402

FAIL, WARN, PASS = common.FAIL, common.WARN, common.PASS


def _fail(title):
    return {"severity": FAIL, "title": title}


class IsBlockingTests(unittest.TestCase):
    # Every defect the reviewer questioned must BLOCK. Semantic titles are
    # open-vocabulary (LLM-coined) — the whole reason an allowlist fails.
    MUST_BLOCK = [
        "cpus-nonpositive",              # (3) cpus=0 real emitted title
        "placeholder-zero-resource",     # sibling zero-resource title
        "untested-requirement",          # (2) semantic, open-vocab
        "oracle-contract-violation",     # (2) semantic
        "contract-contradiction",        # (2) semantic
        "category-mismatch",             # (2) semantic — wrong category label
        "no-assertion-test",             # weak assertion
        "vacuous-test",                  # phantom test
        "nondeterministic-oracle",       # nondeterministic grading
        "agent-writable-verifier",       # candidate-derived / writable truth
        "leftover-generator",            # kept blocking (can leak/regenerate truth)
        "unpinned-base-image",           # spec hard rule: FROM pinned by digest
        "some-brand-new-check-title",    # fail-safe: unknown FAIL blocks by default
    ]
    # Client-tolerated hygiene must NOT block.
    MUST_NOT_BLOCK_FAIL = [
        "solve-embedded-heredoc", "dockerfile-heredoc-source",
        "solve-too-long", "mixed-bash-python-solve",
        "bash-op-doable-natively",
        "missing-dockerignore", "pycache-residue-after-script-removal",
        "missing-tags", "missing-junior-time",
    ]

    def test_defects_block(self):
        for t in self.MUST_BLOCK:
            self.assertTrue(common.is_blocking(_fail(t)), f"{t} should block")

    def test_advisory_fails_do_not_block(self):
        for t in self.MUST_NOT_BLOCK_FAIL:
            self.assertFalse(common.is_blocking(_fail(t)), f"{t} should be advisory")

    def test_warn_and_pass_never_block(self):
        self.assertFalse(common.is_blocking({"severity": WARN, "title": "apt-not-consolidated"}))
        self.assertFalse(common.is_blocking({"severity": WARN, "title": "oracle-runtime-install"}))
        self.assertFalse(common.is_blocking({"severity": WARN, "title": "unpinned-pip"}))
        self.assertFalse(common.is_blocking({"severity": PASS, "title": "metadata-ok"}))


class VerdictTests(unittest.TestCase):
    def _rows(self, findings):
        return aggregate.verdicts(aggregate.per_task(findings))

    def test_semantic_fail_is_blocking_not_downgraded(self):
        rows = self._rows([{"task": "t", "area": "tests", "severity": FAIL,
                            "title": "untested-requirement", "layer": "semantic"}])
        self.assertEqual(rows["t"]["overall"], FAIL)
        self.assertEqual(rows["t"]["blocking"], FAIL)

    def test_advisory_only_fail_downgrades_to_warn(self):
        rows = self._rows([{"task": "t", "area": "solution", "severity": FAIL,
                            "title": "solve-embedded-heredoc"}])
        self.assertEqual(rows["t"]["overall"], FAIL)
        self.assertEqual(rows["t"]["blocking"], WARN)
        self.assertIn("solve-embedded-heredoc", rows["t"]["advisory_issues"])


class GateVerdictTests(unittest.TestCase):
    """(1) gate must decide on `blocking`, not `overall`."""
    FINDINGS = [
        {"task": "t_block", "area": "metadata", "severity": FAIL, "title": "cpus-nonpositive"},
        {"task": "t_semantic", "area": "tests", "severity": FAIL,
         "title": "oracle-contract-violation", "layer": "semantic"},
        {"task": "t_advisory", "area": "solution", "severity": FAIL, "title": "solve-embedded-heredoc"},
        {"task": "t_clean", "area": "tests", "severity": PASS, "title": "reward-hack-static-clean"},
    ]

    def _dir(self):
        d = tempfile.mkdtemp()
        json.dump(self.FINDINGS, open(os.path.join(d, "findings.json"), "w"))
        return d

    def test_calibrated_gate_promotes_advisory(self):
        q, p, _ = gate.partition(self._dir())
        self.assertEqual({t for t, _, _ in q}, {"t_block", "t_semantic"})
        self.assertEqual(set(p), {"t_advisory", "t_clean"})

    def test_raw_verdict_quarantines_advisory(self):
        q, p, _ = gate.partition(self._dir(), raw_verdict=True)
        self.assertEqual({t for t, _, _ in q}, {"t_block", "t_semantic", "t_advisory"})
        self.assertEqual(set(p), {"t_clean"})


if __name__ == "__main__":
    unittest.main()
