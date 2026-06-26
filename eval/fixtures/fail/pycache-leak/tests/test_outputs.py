import json
def test_rows():
    exp=json.load(open("/app/data/expected.json"))
    got=json.load(open("/app/out/result.json"))
    assert got["rows"]==exp["rows"]
