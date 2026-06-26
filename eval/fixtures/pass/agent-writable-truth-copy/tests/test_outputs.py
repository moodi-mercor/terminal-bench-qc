import subprocess

def test_via_verify_time_grader():
    # The grader the verifier runs lives under the verify-time mount, which the
    # agent cannot write — any in-image copy is an unused decoy.
    out = subprocess.run(["python3", "/tests/.truth/verify.py"], capture_output=True, text=True).stdout
    assert "PASS" in out
