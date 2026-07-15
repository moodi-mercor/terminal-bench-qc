import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "skills" / "static-semantic-qc" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_instructions  # noqa: E402
import judge  # noqa: E402

PROMPT_DIR = REPO_ROOT / "skills" / "static-semantic-qc" / "prompts"


def _load(mod_name, path):
    """Load a module by explicit path (shared/common.py collides with the scripts'
    own common.py on module name, so import it under a distinct name)."""
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


shared_common = _load("shared_common", REPO_ROOT / "shared" / "common.py")


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


class CategoryDimensionTests(unittest.TestCase):
    """Semantic category correctness is a first-class, required reviewer dimension
    (was: audit_category.py existed but was absent from the pipeline + v2 prompt)."""

    def test_category_is_a_required_reviewer_dimension(self):
        self.assertIn("category", shared_common.REVIEWER_DIMS)
        self.assertIn("category", judge.REVIEWER_DIMS)
        self.assertEqual(judge.DIM_AREA["category"], "metadata")
        self.assertEqual(shared_common.dimension_area("category"), "metadata")

    def test_category_dimension_present_in_both_reviewer_prompts(self):
        for name in ("tb-task-qc-reviewer-v1.md", "tb-task-qc-reviewer-v2.md"):
            text = (PROMPT_DIR / name).read_text(encoding="utf-8")
            self.assertIn("dimension: category", text, f"{name} omits category validation")

    def test_judge_output_contract_allows_category_dimension(self):
        self.assertIn("category", judge.REVIEWER_OUT)

    def test_missing_category_finding_is_flagged_incomplete(self):
        # six dims covered with evidence, category absent -> category is a gap
        findings = [{"dimension": d, "detail": "x"} for d in
                    ["alignment", "coverage", "hygiene", "golden-patch", "realism", "constraints"]]
        gaps = dict(shared_common.coverage_gaps(findings, require_adversary=False,
                                                require_behavioral=False))
        self.assertIn("category", gaps)


class VerifierSoundnessDimensionTests(unittest.TestCase):
    """Mutation testing is required evidence, not an assumption (was: mutation_test.py
    ran but nothing required it; the v2 prompt assumed soundness without evidence)."""

    def test_verifier_sound_dimension_registered(self):
        self.assertIn("verifier-sound", shared_common.QC_DIMENSIONS)
        self.assertEqual(shared_common.QC_DIMENSIONS["verifier-sound"]["layer"], "behavioral")

    def test_missing_mutation_signal_is_a_completeness_gap(self):
        # oracle + no-op present, mutation signal absent -> verifier-sound is unassessed
        gaps = dict(shared_common.coverage_gaps(
            [], behavioral={"oracle": 1, "noop": 0}, require_adversary=False))
        self.assertIn("verifier-sound", gaps)

    def test_present_mutation_signal_closes_the_gap(self):
        gaps = dict(shared_common.coverage_gaps(
            [], behavioral={"oracle": 1, "noop": 0, "mutation": 1}, require_adversary=False))
        self.assertNotIn("verifier-sound", gaps)

    def test_v2_prompt_conditions_soundness_on_evidence(self):
        text = (PROMPT_DIR / "tb-task-qc-reviewer-v2.md").read_text(encoding="utf-8")
        self.assertIn("Conditional on evidence", text)
        self.assertIn("verifier-sound", text)


class DocumentedWorkflowTests(unittest.TestCase):
    """The documented end-to-end workflow actually invokes mutation testing."""

    def test_mutation_test_is_in_documented_workflows(self):
        for doc in ("README.md", "QC_CHECKLIST.md"):
            text = (REPO_ROOT / doc).read_text(encoding="utf-8")
            self.assertIn("mutation_test.py", text, f"{doc} never invokes mutation_test.py")

    def test_checklist_lists_verifier_soundness_dimension(self):
        text = (REPO_ROOT / "QC_CHECKLIST.md").read_text(encoding="utf-8")
        self.assertIn("verifier-sound", text)


import check_structure  # noqa: E402


class ReflectionNameExemptionTests(unittest.TestCase):
    """Reflection deliveries use opaque `task_<hex>` names by design — the kebab-case
    convention must not false-flag them (matches the Reflection spec)."""

    def _name_findings(self, task_name, toml_body):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "task.toml").write_text(toml_body, encoding="utf-8")
            (root / "instruction.md").write_text("do the thing", encoding="utf-8")
            return {f["title"] for f in check_structure.check_task(task_name, root)}

    def test_opaque_name_exempt_for_reflection_task(self):
        toml = '[metadata]\ncategory = "x"\ntask_objective = ["implement_feature"]\n'
        self.assertNotIn("task-name-not-kebab", self._name_findings("task_a1b2c3d4", toml))

    def test_non_kebab_name_still_flagged_for_tb2_task(self):
        toml = '[metadata]\ndifficulty = "medium"\ncategory = "x"\n'
        self.assertIn("task-name-not-kebab", self._name_findings("Task_NotKebab", toml))


if __name__ == "__main__":
    unittest.main()
