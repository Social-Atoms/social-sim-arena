"""Preliminary/final separation and offline settlement example."""
import sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ssa import scoring
from ssa.adapters.consumer import michigan_current, michigan_finals

lock = datetime.fromisoformat("2026-10-21T10:00:00-04:00")
release = datetime.fromisoformat("2026-10-23T10:00:00-04:00")
assert (release - lock).total_seconds() >= 48 * 3600
prelim = (b",,,EXPECTED CHANGE IN PRICES\nDATE OF SURVEY,,,NEXT YEAR,,NEXT 5 YEARS\n"
          b"October (P),2026,,4.7,,3.5\n")
final = b"Month,YYYY,PX_MD,PX5_MD\nOctober,2026,4.8,3.6\n"
answer = michigan_current(prelim)["series"]["umich_inflation_1y"][0]
later = michigan_finals(final)["series"]["umich_inflation_1y"][0]
assert answer == {"date": "2026-10-01", "value": 4.7}
assert later["value"] == 4.8 and later != answer
assert scoring.crps_normal(4.5, 1.0, answer["value"]) >= 0
print("Michigan preliminary sandbox settlement passed")
