import subprocess
def test_via_tests_copy():
    # the real grader is the verify-time copy invoked by path; environment/checker.py
    # is a redundant baked copy of verifier logic in the agent image.
    out = subprocess.run(["python3", "/tests/checker.py"], capture_output=True, text=True).stdout
    assert "ok" in out
