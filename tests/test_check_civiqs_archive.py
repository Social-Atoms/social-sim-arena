"""Offline Civiqs resolution freshness checks."""
import json
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.check_civiqs_archive import evaluate

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    tracker = root / "tracker"
    tracker.mkdir()
    (tracker / "2026-10-21.json").write_text(json.dumps({"end_date": "2026-10-20"}))
    assert evaluate("tracker", "2026-10-21", root)["state"] == "stale"
    assert evaluate("missing", "2026-10-21", root)["state"] == "missing"
    (tracker / "2026-10-22.json").write_text(json.dumps({"end_date": "2026-10-21"}))
    assert evaluate("tracker", "2026-10-21", root)["state"] == "fresh"
    assert evaluate("tracker", "2026-10-22", root)["state"] == "stale"

print("Civiqs archive freshness checks passed")
