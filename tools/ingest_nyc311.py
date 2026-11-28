"""File a manually downloaded NYC 311 body: the card's fallback route.

Card nyc-311-requests@v2, archive_policy, manual-download fallback. Used only
if maintainers rule that automated retrieval is not allowed. Once a week, on
Sunday between 08:00 and 20:00 New York time, the named person opens the URL
this tool prints, saves the JSON exactly as the browser shows it, and runs:

    python tools/ingest_nyc311.py --url                 # today's URL (NY date)
    python tools/ingest_nyc311.py ~/Downloads/erm2-nwe9.json

The body is checked before anything is written (ssa.adapters.nyc311.
ingest_check): it must be the card's request for the capture day (oldest day
= capture day - 280), carry every day through capture day - 2, and have that
day -- Friday, for a Sunday download -- as its newest day posted in full,
which is how a stale replica shows itself without response headers. Outage
failures in the weeks it serves are printed, not refused: the round rules
handle them on the same body. Then ssa/capture.py files it write-once as
sources/nyc311/<capture day>.json with its sha256, refusing a capture day
older than the newest one held and a different file for a day already held.

A Sunday file sets that night's lock reference and resolves the round whose
week ended six days earlier. A late download until Wednesday 12:00 ET serves
resolution only. Missing Sunday 20:00 ET means that night's round is not
posted; two missed Sundays in a row pause the family.
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ssa import capture                                      # noqa: E402
from ssa.adapters import nyc311                              # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", type=Path, nargs="?",
                        help="the JSON saved from the browser")
    parser.add_argument("--capture-day",
                        help="New York date of the download (default: today in New York)")
    parser.add_argument("--url", action="store_true",
                        help="print the exact URL to open for the capture day, and exit")
    args = parser.parse_args(argv)
    day = (date.fromisoformat(args.capture_day) if args.capture_day
           else capture.utcnow().astimezone(nyc311.TZ).date())
    if args.url:
        print(nyc311.request_url(day))
        return 0
    if args.file is None:
        parser.error("a file is required unless --url is given")
    if day.weekday() != 6:
        print(f"note: {day} is not a Sunday; a late file serves resolution "
              "only (card v2, MISSED DOWNLOAD)", file=sys.stderr)
    out = nyc311.ingest_manual(args.file, capture_day=day.isoformat())
    for week, days in ((out.get("row") or {}).get("outage_days") or {}).items():
        print(f"warning: {week} fails the outage check on {', '.join(days)}",
              file=sys.stderr)
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
