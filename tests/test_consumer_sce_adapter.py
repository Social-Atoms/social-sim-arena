import json
import sys
import zipfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ssa.adapters.consumer import SCE_FIELDS, sce_extensions
from tests.test_sce import workbook

for sid, value in (("sce_spending_growth", 2.5),
                   ("sce_stock_prices_rise", 48.0),
                   ("sce_unemployment_rise", 39.0)):
    sheet, header, unit = SCE_FIELDS[sid]
    body = workbook([(202610, {"B": 999, "C": value, "D": 888})],
                    headers=(("B", "decoy"), ("C", header), ("D", "other")),
                    sheet_name=sheet)
    parsed = sce_extensions(body, {sid: (sheet, header, unit)})
    assert parsed["series"][sid] == [{"date": "2026-10-01", "value": value}]
    try:
        sce_extensions(body, {sid: (sheet, "Changed header", unit)})
    except ValueError:
        pass
    else:
        raise AssertionError("Renamed SCE field did not fail")

fixture = json.dumps({"not": "an xlsx"}).encode()
try:
    sce_extensions(fixture)
except zipfile.BadZipFile:
    pass
else:
    raise AssertionError("non-XLSX SCE input must fail loudly")

print("SCE consumer adapter contract passed")
