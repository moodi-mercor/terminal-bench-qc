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

    def test_contract_and_determinism_are_required_enforced_dimensions(self):
        # the prompt asks for 9 dims; the enforced contract must match (else contract/
        # determinism findings are silently optional).
        for dim in ("contract", "determinism"):
            self.assertIn(dim, judge.REVIEWER_DIMS)
            self.assertIn(dim, shared_common.REVIEWER_DIMS)
            self.assertIn(dim, judge.DIM_AREA)
            self.assertIn(dim, shared_common.QC_DIMENSIONS)
        for name in ("tb-task-qc-reviewer-v1.md", "tb-task-qc-reviewer-v2.md"):
            self.assertIn("dimension: contract",
                          (PROMPT_DIR / name).read_text(encoding="utf-8"))

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
import check_test_hygiene  # noqa: E402
import check_portability  # noqa: E402
import check_metadata  # noqa: E402


def _make_task(tmp, files):
    root = Path(tmp)
    for rel, content in files.items():
        pth = root / rel
        pth.parent.mkdir(parents=True, exist_ok=True)
        pth.write_text(content, encoding="utf-8")
    return root


class Batch1FeedbackDetectorTests(unittest.TestCase):
    """Regression coverage for the concrete Batch-1 feedback dimensions."""

    def _hygiene(self, files):
        with tempfile.TemporaryDirectory() as tmp:
            return {f["title"] for f in check_test_hygiene.check_task("t", _make_task(tmp, files))}

    def _portability(self, files):
        with tempfile.TemporaryDirectory() as tmp:
            return {f["title"] for f in check_portability.check_task("t", _make_task(tmp, files))}

    def test_encoded_ground_truth_blob_flagged(self):
        b64 = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU2Nzg5"  # 48 base64 chars
        src = f'_TRUTH = "{b64}"\n\ndef test_check_1():\n    assert True\n'
        self.assertIn("encoded-ground-truth",
                      self._hygiene({"tests/test_outputs.py": src, "tests/test.sh": "pytest\n"}))

    def test_oracle_runtime_install_flagged(self):
        self.assertIn("oracle-runtime-install",
                      self._portability({"solution/solve.sh": "#!/bin/bash\napt-get install -y jq\n"}))

    def test_generic_db_bootstrap_flagged(self):
        ts = "pg_ctl start\nmysqld_safe &\nredis-server &\nmongod &\npytest\n"
        titles = self._hygiene({"tests/test_outputs.py": "def test_check_1():\n    assert True\n",
                                "tests/test.sh": ts,
                                "solution/solve.sh": "#!/bin/bash\ntrue\n",
                                "instruction.md": "do the thing"})
        self.assertIn("generic-bootstrap-blocks", titles)

    def test_dangling_truth_reference_flagged(self):
        src = 'def test_check_1():\n    open("/tests/.truth/expected.json").read()\n'
        self.assertIn("dangling-truth-reference",
                      self._hygiene({"tests/test_outputs.py": src, "tests/test.sh": "pytest\n"}))


class TaskNameConventionTests(unittest.TestCase):
    """The Reflection spec requires lowercase kebab-case; Batch-1 feedback flagged
    opaque `task_<hex>` names as a defect. They must be flagged for every task."""

    def _name_findings(self, task_name, toml_body):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "task.toml").write_text(toml_body, encoding="utf-8")
            (root / "instruction.md").write_text("do the thing", encoding="utf-8")
            return {f["title"] for f in check_structure.check_task(task_name, root)}

    def test_opaque_hash_name_flagged_even_for_reflection_task(self):
        toml = '[metadata]\ncategory = "x"\ntask_objective = ["implement_feature"]\n'
        self.assertIn("task-name-not-kebab",
                      self._name_findings("task_5daf0a23551d47fe8e344df4b2d11f71", toml))

    def test_kebab_name_passes(self):
        toml = '[metadata]\ncategory = "x"\ntask_objective = ["implement_feature"]\n'
        self.assertNotIn("task-name-not-kebab", self._name_findings("fix-nginx-tls-config", toml))


class EncodedContentHardeningTests(unittest.TestCase):
    """Encoded ground truth must be caught outside test_outputs.py, and even when
    test_outputs.py is absent (the early-return used to skip solve.sh)."""

    def _hygiene(self, files):
        with tempfile.TemporaryDirectory() as tmp:
            return {f["title"] for f in check_test_hygiene.check_task("t", _make_task(tmp, files))}

    def test_base64_truth_in_solve_without_test_outputs(self):
        b64 = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU2Nzg5"
        titles = self._hygiene({"solution/solve.sh": f'#!/bin/bash\n_TRUTH="{b64}"\n'})
        self.assertIn("encoded-ground-truth", titles)
        self.assertNotIn("test-hygiene-unknown", titles)  # did not bail early

    def test_base64_truth_in_truth_fixture_file(self):
        b64 = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU2Nzg5"
        titles = self._hygiene({"tests/test_outputs.py": "def test_check_1():\n    assert True\n",
                                "tests/.truth/expected.py": f'GOLDEN = "{b64}"\n'})
        self.assertIn("encoded-ground-truth", titles)


class ObjectiveCoverageTests(unittest.TestCase):
    """Objective/artifact coverage is a FAIL and accounts for required labels that
    never appear in the delivery (full-taxonomy, not just present labels)."""

    def test_missing_required_objective_is_fail(self):
        import json as _json
        import check_diversity
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            toml = ('[metadata]\ncategory = "software-engineering"\nsubcategory = "x"\n'
                    'task_objective = ["implement_feature"]\nartifact_type = ["patch"]\n'
                    'avg_at_8 = 0.25\nmodel_tested = "GPT-5.4"\n')
            for i in range(20):
                d = root / f"task-{i:02d}"
                (d).mkdir(parents=True)
                (d / "task.toml").write_text(toml, encoding="utf-8")
                (d / "instruction.md").write_text("do the thing", encoding="utf-8")
            out = root / "div.json"
            argv = ["check_diversity.py", str(root), "--out", str(out), "--min-tasks", "20"]
            old = sys.argv
            sys.argv = argv
            try:
                check_diversity.main()
            finally:
                sys.argv = old
            findings = _json.load(open(out))
        by_title = {f["title"]: f["severity"] for f in findings}
        self.assertEqual(by_title.get("task-objective-missing"), check_diversity.FAIL)
        self.assertEqual(by_title.get("artifact-type-missing"), check_diversity.FAIL)


class ScenarioKeywordTests(unittest.TestCase):
    """The keyword-concentration check detects a dominant task-name token."""

    def test_dominant_name_token_detected(self):
        import check_scenario_diversity
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i, suffix in enumerate(["alpha", "beta", "gamma", "delta", "epsilon"]):
                d = root / f"widget-{suffix}-{i:02d}"
                d.mkdir(parents=True)
                (d / "instruction.md").write_text(f"process the {suffix} record", encoding="utf-8")
                (d / "task.toml").write_text('[metadata]\ncategory = "x"\n', encoding="utf-8")
            n, over, per, over_names, per_name = check_scenario_diversity.check(str(root), 0.05)
        self.assertIn("widget", {w for w, c, f in over_names})


class MetadataFailSeverityTests(unittest.TestCase):
    """Batch-1 feedback: invalid avg_at_8 fractions and non-GPT-5.4 grading are hard
    FAILs, not warnings."""

    _BASE = '[metadata]\ncategory = "x"\ntask_objective = ["implement_feature"]\n'

    def _meta(self, extra):
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_task(tmp, {"task.toml": self._BASE + extra,
                                    "instruction.md": "do the thing"})
            return {f["title"]: f["severity"] for f in check_metadata.check_task("t", root)}

    def test_invalid_avg_at_8_fraction_is_fail(self):
        res = self._meta('avg_at_8 = 0.1111\nmodel_tested = "GPT-5.4"\n')
        self.assertEqual(res.get("avg-at-8-invalid-fraction"), check_metadata.FAIL)

    def test_valid_eighth_avg_at_8_passes(self):
        self.assertNotIn("avg-at-8-invalid-fraction",
                         self._meta('avg_at_8 = 0.25\nmodel_tested = "GPT-5.4"\n'))

    def test_opus_model_is_fail(self):
        res = self._meta('avg_at_8 = 0.25\nmodel_tested = "Opus 4.8"\n')
        self.assertEqual(res.get("model-not-gpt-5.4"), check_metadata.FAIL)

    def test_gpt_5_4_model_passes(self):
        self.assertNotIn("model-not-gpt-5.4",
                         self._meta('avg_at_8 = 0.25\nmodel_tested = "GPT-5.4"\n'))


if __name__ == "__main__":
    unittest.main()
