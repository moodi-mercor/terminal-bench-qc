def test_matches_expected():
    exp = open("/opt/expected_output.csv").read()
    got = open("/app/out/clean.csv").read()
    assert got == exp
