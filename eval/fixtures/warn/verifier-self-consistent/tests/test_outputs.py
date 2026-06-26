def test_matches_baseline():
    expected = int(open("/app/out/baseline.txt").read().strip())
    got = int(open("/app/out/result.txt").read().strip())
    assert got == expected
