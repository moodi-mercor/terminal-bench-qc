import os
def test_quarantine():
    for path in os.listdir("/app/out"):
        if "malicious" in os.path.basename(path):
            assert path.endswith(".quarantine")
