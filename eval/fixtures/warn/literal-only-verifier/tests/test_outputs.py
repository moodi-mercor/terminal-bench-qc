def test_total():
    got = open("/app/out/answer.txt").read().strip()
    assert got == "42"

def test_status():
    status = open("/app/out/status.txt").read().strip()
    assert status == "done"
