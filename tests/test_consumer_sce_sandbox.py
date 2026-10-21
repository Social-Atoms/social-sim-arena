"""SCE release timing and offline settlement example."""
import sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ssa import scoring
from ssa.adapters.consumer import SCE_FIELDS, sce_extensions
from tests.test_sce import workbook

lock = datetime.fromisoformat("2026-11-07T11:00:00-05:00")
release = datetime.fromisoformat("2026-11-09T11:00:00-05:00")
assert (release - lock).total_seconds() >= 48 * 3600
sid = "sce_spending_growth"
sheet, header, unit = SCE_FIELDS[sid]
body = workbook([(202610, {"B": 999, "C": 2.5, "D": 888})],
                headers=(("B", "decoy"), ("C", header), ("D", "other")),
                sheet_name=sheet)
point = sce_extensions(body, {sid: (sheet, header, unit)})["series"][sid][0]
assert point == {"date": "2026-10-01", "value": 2.5}
assert scoring.crps_normal(2.4, 1.0, point["value"]) >= 0
print("SCE sandbox settlement passed")
