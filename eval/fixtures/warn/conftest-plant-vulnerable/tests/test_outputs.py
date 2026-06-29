def test_total():
    assert open("/app/out/total.txt").read().strip()=="42"
