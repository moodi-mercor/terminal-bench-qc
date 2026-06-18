import csv, os
def test_clean_csv_drops_void():
    assert os.path.exists("/app/out/clean.csv"), "output missing"
    rows = list(csv.DictReader(open("/app/out/clean.csv")))
    assert len(rows) == 2, f"expected 2 surviving rows, got {len(rows)}"
    assert all(r["status"] != "void" for r in rows)
