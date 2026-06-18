def test_error_count_matches_truth():
    # The expected answer lives under tests/.truth/, mounted only at verify time
    # (never baked into the agent image), so the agent cannot read it while solving.
    expected = int(open("/tests/.truth/expected_errors.txt").read().strip())
    got = int(open("/app/out/error_count.txt").read().strip())
    assert got == expected, f"expected {expected} ERROR lines, got {got}"
