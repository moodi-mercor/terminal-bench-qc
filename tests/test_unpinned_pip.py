"""unpinned-pip is now blocking (Reflection: all deps pinned to exact versions), so its
scoping must not false-positive on legitimately-pinned / not-a-package installs."""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "skills" / "static-semantic-qc" / "scripts"))

import check_dockerfile  # noqa: E402


class UnpinnedPipScopeTests(unittest.TestCase):
    def flagged(self, cmd):
        return check_dockerfile._unpinned_pip(cmd)

    def test_genuine_unpinned_is_flagged(self):
        self.assertEqual(self.flagged("RUN pip install requests flask"), ["requests", "flask"])
        self.assertEqual(self.flagged("RUN uv pip install numpy"), ["numpy"])

    def test_pinned_not_flagged(self):
        self.assertEqual(self.flagged("RUN pip install requests==2.31.0 flask==3.0.0"), [])
        self.assertEqual(self.flagged("RUN pip install 'numpy>=1.26,<2'"), [])

    def test_requirements_and_constraints_files_not_flagged(self):
        self.assertEqual(self.flagged("RUN pip install -r requirements.txt"), [])
        self.assertEqual(self.flagged("RUN pip install --requirement=reqs.txt"), [])
        self.assertEqual(self.flagged("RUN pip install -c constraints.txt foo==1.0"), [])

    def test_local_editable_url_offline_not_flagged(self):
        self.assertEqual(self.flagged("RUN pip install -e ."), [])
        self.assertEqual(self.flagged("RUN pip install ."), [])
        self.assertEqual(self.flagged("RUN pip install /app/pkg"), [])
        self.assertEqual(self.flagged("RUN pip install https://x/y.whl"), [])
        self.assertEqual(self.flagged("RUN pip install --no-index --find-links=/wheels foo"), [])

    def test_bootstrap_trio_not_flagged(self):
        self.assertEqual(self.flagged("RUN pip install -U pip setuptools wheel"), [])

    def test_comment_is_not_scanned(self):
        # a `pip install` mentioned in a comment/heredoc must not be flagged
        self.assertEqual(self.flagged("# pip install -e . editable means the oracle later\n"
                                      "RUN pip install -e /app"), [])

    def test_severity_is_advisory_warn(self):
        # spec-hygiene, not task-breaking: WARN (advisory), so it never blocks
        import tempfile, os
        d = tempfile.mkdtemp()
        env = os.path.join(d, "environment"); os.makedirs(env)
        open(os.path.join(env, "Dockerfile"), "w").write(
            "FROM python:3.11.7\nRUN pip install requests\n")
        out = check_dockerfile.check_task("t", d)
        hits = [f for f in out if f["title"] == "unpinned-pip"]
        self.assertTrue(hits and hits[0]["severity"] == "WARN")


if __name__ == "__main__":
    unittest.main()
