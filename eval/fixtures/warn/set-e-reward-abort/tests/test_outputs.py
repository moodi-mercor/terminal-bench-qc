def test_count():
    assert open("/app/out/count.txt").read().strip() == "4"
