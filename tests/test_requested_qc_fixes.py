import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "skills" / "static-semantic-qc" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_instructions  # noqa: E402
import judge  # noqa: E402


class InstructionTokenLimitTests(unittest.TestCase):
    def _instruction_with_tokens(self, count):
        text = " token" * count
        self.assertEqual(check_instructions._count_tokens(text), count)
        return text

    def _titles_for(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "instruction.md").write_text(text, encoding="utf-8")
            return {finding["title"] for finding in check_instructions.check_task("task", root)}

    def test_1499_tokens_passes_length_gate(self):
        self.assertNotIn(
            "instruction-too-long",
            self._titles_for(self._instruction_with_tokens(1499)),
        )

    def test_1500_tokens_fails_length_gate(self):
        self.assertIn(
            "instruction-too-long",
            self._titles_for(self._instruction_with_tokens(1500)),
        )


class MandatoryReviewerCheckTests(unittest.TestCase):
    def test_all_four_checks_are_required_by_both_prompts_and_api_contract(self):
        prompt_dir = REPO_ROOT / "skills" / "static-semantic-qc" / "prompts"
        contracts = [
            (prompt_dir / "tb-task-qc-reviewer-v1.md").read_text(encoding="utf-8"),
            (prompt_dir / "tb-task-qc-reviewer-v2.md").read_text(encoding="utf-8"),
            judge.REVIEWER_OUT,
        ]
        for contract in contracts:
            for check in judge.MANDATORY_CHECKS:
                self.assertIn(f"{check}:", contract)

    def test_missing_mandatory_checks_are_detected(self):
        findings = [{"detail": "Q1: checked\nQ2: checked\nQ3: checked"}]
        self.assertEqual(judge.mandatory_check_gaps(findings), ["Q4"])

    def test_all_mandatory_checks_satisfy_contract(self):
        findings = [{"detail": "Q1: checked\nQ2: checked\nQ3: checked\nQ4: checked"}]
        self.assertEqual(judge.mandatory_check_gaps(findings), [])


if __name__ == "__main__":
    unittest.main()
