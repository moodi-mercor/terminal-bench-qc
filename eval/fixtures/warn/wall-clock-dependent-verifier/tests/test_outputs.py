import subprocess, time
def test_runs_within_budget():
    t0 = time.monotonic()
    subprocess.run(["python3", "/app/pipeline.py"])
    elapsed = time.monotonic() - t0
    assert elapsed < 2.0, f"too slow: {elapsed}s"
