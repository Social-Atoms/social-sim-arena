"""Pinned two-report weekly-change settlement example."""
import sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ssa import scoring
from ssa.adapters.consumer import mint_series

lock = datetime.fromisoformat("2026-10-11T23:00:00-04:00")
week_start = datetime.fromisoformat("2026-10-12T00:00:00-04:00")
assert lock < week_start
parsed = mint_series({"EXAMPLE": [
    {"date": "2026-10-11", "value": 100},
    {"date": "2026-10-18", "value": 90}]})
answer = parsed["series"]["mint_EXAMPLE_weekly_net_sales"]
assert answer == [{"date": "2026-10-18", "value": -10}]
assert scoring.crps_normal(0, 15, answer[0]["value"]) >= 0
print("Mint weekly increment sandbox settlement passed")
