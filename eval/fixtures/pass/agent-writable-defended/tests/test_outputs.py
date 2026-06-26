import subprocess

def test_via_in_image_grader():
    # Gameable on its own: the agent could overwrite /app/verify.py to print PASS.
    out = subprocess.run(["python3", "/app/verify.py"], capture_output=True, text=True).stdout
    assert "PASS" in out

def test_recompute_independent():
    # HARD DEFENSE: recompute the expected sum here, independent of the copied
    # grader — the agent cannot forge this by overwriting /app/verify.py.
    expected = sum(int(x) for x in open("/app/data/nums.txt"))
    got = int(open("/app/out/total.txt").read().strip())
    assert got == expected, f"expected {expected}, got {got}"
