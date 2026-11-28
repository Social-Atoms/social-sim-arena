"""Fail closed when a named Civiqs archive is stale for a resolution date."""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ssa import health


def evaluate(key, expected_date, archive=None):
    expected = date.fromisoformat(expected_date).isoformat()
    rows = {row["key"]: row for row in health.civiqs_rows(archive=archive)}
    row = rows.get(key)
    if row is None:
        return {"key": key, "expected_date": expected, "state": "missing"}
    snapshot = row.get("newest_snapshot")
    reading = row.get("newest_reading")
    state = "fresh" if snapshot and reading and snapshot >= expected and reading >= expected else "stale"
    return {"key": key, "expected_date": expected, "state": state,
            "newest_snapshot": snapshot, "newest_reading": reading}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive_key")
    parser.add_argument("expected_date")
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args(argv)
    result = evaluate(args.archive_key, args.expected_date, args.archive)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["state"] == "fresh" else 1


if __name__ == "__main__":
    sys.exit(main())
