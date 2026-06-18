def test_count_matches_truth():
    expected = int(open("/opt/answer_truth.txt").read().strip())
    got = sum(1 for _ in open("/app/out/clean.csv")) - 1
    assert got == expected
