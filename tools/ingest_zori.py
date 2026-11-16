"""File a manually downloaded Zillow ZORI metro CSV (card zillow-zori-metro@v3). Never fetches.

Once a month, from the 16th, a named person opens https://www.zillow.com/research/data/
in an ordinary browser, section Rentals, "ZORI (Smoothed, Seasonally Adjusted):
All Homes Plus Multifamily Time Series ($)", geography Metro & U.S., Download.
Zillow's terms bar automated queries, so no script, workflow or test may do
this step. Then, from the repository:

  python tools/sync_consumer_archive.py pull            # restore the private archive
  PYTHONPATH=. python tools/ingest_zori.py ~/Downloads/Metro_zori_uc_sfrcondomfr_sm_sa_month.csv \\
      --expected-month 2026-12
  python tools/sync_consumer_archive.py push            # raw file -> age ciphertext on `sealed`
  git add sources/zillow_zori && git commit             # public: sha256 + derived changes only

Checks, each a hard stop with nothing written (card source.access): (a) the
file name is exactly Metro_zori_uc_sfrcondomfr_sm_sa_month.csv; (b) the header
and consecutive month-end columns from 2015-01-31; (c) the newest column is
--expected-month; (d) a United States row and every frozen profile metro;
(e) positive values for the two newest months; (f) each profile metro's
previous-month change within 0.05 points of the previous file's; (g) sha256,
write-once per capture day, no capture day older than the newest held.

The raw bytes go only to `<private archive>/raw/zillow_zori/<sha256>.csv`
(default `.local/consumer`, ignored by git and Vercel). The public record is
`sources/zillow_zori/<capture day>.json` plus `captures.json` and, on the first
ingest, `universe.json`. Every page that shows a derived number carries "Data
Provided by Zillow Group" (ssa/source_notices.py).
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ssa import consumer                                     # noqa: E402
from ssa.adapters import zillow_zori as z                    # noqa: E402


def _dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def ingest(path, expected_month, capture_day=None, archive=None, public=None, now=None):
    path = Path(path)
    if path.name != z.FILE_NAME:
        raise ValueError(f"check (a): file name is {path.name!r}, not {z.FILE_NAME!r}: "
                         "the SA and not-SA files share a layout, so the name is the "
                         "only proof of the variant; archive unchanged")
    now = now or datetime.now(timezone.utc)
    capture_day = capture_day or now.date().isoformat()
    datetime.strptime(capture_day, "%Y-%m-%d")
    datetime.strptime(expected_month, "%Y-%m")
    public = Path(public or z.PUBLIC)
    archive = consumer.private_archive(archive or consumer.DEFAULT_ARCHIVE)
    body = path.read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    captures_path = public / "captures.json"
    captures = json.loads(captures_path.read_text()) if captures_path.exists() else {}
    if capture_day in captures:
        if captures[capture_day]["sha256"] == digest:
            return dict(captures[capture_day], created=False)
        raise ValueError(f"check (g): {capture_day} already holds a different file; "
                         "archive unchanged")
    if captures and capture_day < max(captures):
        raise ValueError(f"check (g): capture day {capture_day} is older than the newest "
                         f"held, {max(captures)}; archive unchanged")
    parsed = z.parse(body)
    universe_path = public / "universe.json"
    first = not universe_path.exists()
    universe = z.freeze_universe(parsed) if first else json.loads(universe_path.read_text())
    previous = None
    if captures:
        previous = json.loads((public / f"{max(captures)}.json").read_text())
    record = z.check(parsed, expected_month, universe, previous)
    # Everything checked: the raw bytes go to the private archive only.
    raw_rel = f"raw/{z.NAME}/{digest}.csv"
    consumer.immutable(consumer.archive_path(archive, raw_rel), body)
    record.update({"capture_day": capture_day, "ingested_at":
                   now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "file_name": z.FILE_NAME, "sha256": digest, "bytes": len(body),
                   "private_raw": raw_rel, "manual": True})
    if first:
        _dump(universe_path, universe)
    _dump(public / f"{capture_day}.json", record)
    captures[capture_day] = {"sha256": digest, "bytes": len(body),
                             "newest_month": record["newest_month"],
                             "file": f"sources/{z.NAME}/{capture_day}.json"}
    _dump(captures_path, captures)
    return dict(captures[capture_day], created=True, universe_frozen=first)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--expected-month", required=True)
    ap.add_argument("--capture-day")
    ap.add_argument("--archive", type=Path, default=None)
    args = ap.parse_args(argv)
    try:
        out = ingest(args.file, args.expected_month, args.capture_day, args.archive)
    except ValueError as e:
        sys.exit(f"ingest_zori: {e}")
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    main()
