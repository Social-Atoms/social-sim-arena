"""File a manually downloaded MTA daily ridership CSV (card mta-subway-weekly@v2, route B).

Once a week, between Saturday 00:00 and Sunday 20:00 New York time, a person
opens https://data.ny.gov/d/sayj-mze2, clicks Export > CSV (or opens
https://data.ny.gov/api/v3/views/sayj-mze2/export.csv in a
browser), notes the time, and runs:

  PYTHONPATH=. python tools/ingest_mta_daily.py ~/Downloads/MTA_Daily_Ridership_and_Traffic__Beginning_2020.csv \\
      --downloaded-at 2026-11-28T15:04:00Z
  git add sources/mta_daily sources/manifest.json && git commit

Before anything is written it checks (ssa/adapters/mta_daily.check_download):
the header is exactly Date,Mode,Count; the download time falls inside the
Saturday-Sunday window (or, for a missed or incomplete Saturday, the
resolution-only grace up to Tuesday 12:00 ET); the newest posted Subway day
reaches the Sunday six days before that Saturday ("Friday posting missing"
otherwise). The file is then archived as served under
sources/mta_daily/<download day, UTC>.csv: write-once, never older than the
newest one held, the same bytes again a no-op. A Mode code never seen before
is printed and recorded, and does not stop the ingest (card policy (4)).
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ssa.adapters import mta_daily                           # noqa: E402


def parse_instant(s):
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.strptime(s, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"{s!r}: give the download time in UTC, e.g. 2026-11-28T15:04:00Z") from None


def main(argv=None, now=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file", type=Path)
    ap.add_argument("--downloaded-at", required=True, type=parse_instant,
                    help="when the file was downloaded, UTC (YYYY-MM-DDTHH:MM[:SS]Z)")
    ap.add_argument("--url", default=mta_daily.URL,
                    help="the URL the file came from, if the portal's export "
                         "link has changed (recorded with the capture)")
    args = ap.parse_args(argv)
    now = now or datetime.now(timezone.utc)
    if args.downloaded_at > now:
        sys.exit(f"--downloaded-at {args.downloaded_at:%Y-%m-%dT%H:%M:%SZ} is in "
                 "the future; archive unchanged")
    try:
        out = mta_daily.ingest(args.file, args.downloaded_at, url=args.url)
    except ValueError as e:
        sys.exit(f"REFUSED: {e}")
    row = out["row"] or {}
    if row.get("unknown_modes"):
        print(f"NOTE: Mode code(s) not seen before: {', '.join(row['unknown_modes'])} "
              "(recorded; Subway rounds are unaffected)", file=sys.stderr)
    print(json.dumps({"archive": out["archive"], "sha256": out["sha256"],
                      "created": out["created"], "window": row.get("window"),
                      "lock_of": row.get("lock_of"), "resolves": row.get("resolves"),
                      "newest_subway": (row.get("newest_posted") or {}).get("Subway")},
                     sort_keys=True))
    return out


if __name__ == "__main__":
    main()
