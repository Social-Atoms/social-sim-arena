"""FRED's keyless CSV endpoint, for series whose primary source FRED *is*.

  GET https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES_ID>

No API key needed, which is the whole reason to reach for it.

**Never use this as a fallback for a series that has a timelier primary
source.** It used to back Michigan sentiment that way, and FRED republishes
that series a month late at Michigan's request. On the one run where the
university's own table was briefly unreachable, the fallback answered with a
history ending a month early -- and nothing downstream could tell the
difference between "a month behind" and "up to date".

That run wrote the lock snapshot for `umich-2026-08-prelim`. Its baselines were
anchored a month stale, and because a resolution is "the first release after the
frozen history", the answer silently became July's final (55.2, published in
July, public well before the round's lock) instead of August's preliminary
(51.0). The arena's one claim -- that at lock time the answer does not exist --
failed on its first round, and it failed quietly.

The Michigan helpers that made that possible are gone from this module. A
silently stale source is worse than no source: see ssa/series.michigan_history,
which now raises instead.
"""
import csv
import io

import requests

BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv"


def fetch_text(series_id="UMCSENT", timeout=30):
    """The CSV exactly as served, for ssa/provenance.py to archive."""
    r = requests.get(BASE, params={"id": series_id}, timeout=timeout)
    r.raise_for_status()
    return r.text


def parse(text, series_id="UMCSENT"):
    rows = []
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if header is None:
        raise RuntimeError("FRED returned an empty body for series " + series_id)
    for row in reader:
        if len(row) < 2 or row[1] in (".", ""):
            continue
        rows.append({"date": row[0], "value": float(row[1])})
    return rows


def series(series_id, timeout=30):
    """Full monthly history: [{date: 'YYYY-MM-DD', value: float}], oldest first."""
    return parse(fetch_text(series_id, timeout), series_id)


# `umich_sentiment` and `umich_inflation_expectations` used to live here. They
# are deliberately not replaced: their only caller used them as a fallback, and
# a convenience function is how that becomes easy to do again by accident.
