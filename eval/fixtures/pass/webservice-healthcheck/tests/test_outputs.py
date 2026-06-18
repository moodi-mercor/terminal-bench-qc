import json
import urllib.request


def test_health_endpoint_ok():
    url = "http://127.0.0.1:8080/health"
    with urllib.request.urlopen(url, timeout=5) as resp:
        assert resp.status == 200, f"expected 200, got {resp.status}"
        body = json.loads(resp.read())
    assert body.get("status") == "ok", f"unexpected body: {body}"
