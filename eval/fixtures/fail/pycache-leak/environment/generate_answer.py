import json, os
# generation logic + embedded expected answer
EXPECTED = {"rows": 4242, "checksum": "ab12cd"}
os.makedirs("/app/data", exist_ok=True)
json.dump(EXPECTED, open("/app/data/expected.json","w"))
