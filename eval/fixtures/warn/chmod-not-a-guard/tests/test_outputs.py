def test_recompute():
    expected = sum(int(x) for x in open("/app/data/nums.txt"))
    got = int(open("/app/out/total.txt").read().strip())
    assert got == expected
