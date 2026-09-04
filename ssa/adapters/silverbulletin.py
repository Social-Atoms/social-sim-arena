"""Silver Bulletin poll databases.

Two published-to-web Google Sheets, free and keyless, linked from the free
portion of natesilver.net. They carry the poll-level records behind Silver
Bulletin's approval and generic-ballot averages, refreshed daily -- on
2026-08-08 the newest field date was three days old, against forty-one days for
the aggregation API this replaces.

  approval        every Trump approval poll, 5,875 rows, incl. issue subgroups
  generic ballot  every 2026 generic congressional ballot poll, 540 rows

Both files publish, per poll: pollster, sponsor, field dates, sample size,
population, the raw marginals, and Silver Bulletin's own house-effect-adjusted
marginals. **We read the raw columns, never the adjusted ones.** Adjustment is
the arena's own step (ssa/average.py); taking theirs would outsource the
resolution value to a third party's model and move our numbers whenever they
retune it.

Two fields decide which number you get, and both are load-bearing:

- `subgroup` splits overall approval from issue-specific approval. A row is not
  "the" approval reading unless you say which subgroup you mean; 'All polls'
  is overall, while 'Economy'/'Immigration'/'Trade'/'Cost' are separate
  trackers that happen to share a file.
- `population` splits adults (A) from registered (RV) and likely (LV) voters.
  The same Morning Consult wave in June 2025 read 49.8 among adults and 46.2
  among registered voters -- a 3.6-point gap that is not noise. A question that
  asks about registered voters must be resolved against registered voters.

Nothing here defaults those filters. Callers name them, because a silent
default is how a series ends up answering a different question than its round.
"""
import csv
import datetime
import io
import time

import requests

APPROVAL_URL = (
    "https://docs.google.com/spreadsheets/d/e/2PACX-1vS-FKWVTTFtJT6u56e0bqdfoM"
    "cXvDO1DUChsJ3jQAMB2lZk2SMqVfmg7dGjclTYkYWz-Pm5lfcLPjp4/pub?output=csv")
GENERIC_URL = (
    "https://docs.google.com/spreadsheets/d/e/2PACX-1vRsvXNCZ0ubJr8D_yNcU5q6C0"
    "_HBa35K7oDK03KpO7Ca43UwdXaIdvVLWoXEmHHph0EREz5430Hm5yZ/pub?output=csv")

TIMEOUT = 60


def _date(s):
    """M/D/YYYY as published. Returns None for blanks and malformed cells."""
    parts = (s or "").strip().split("/")
    if len(parts) != 3:
        return None
    try:
        m, d, y = (int(p) for p in parts)
        return datetime.date(y, m, d)
    except ValueError:
        return None


def _float(s):
    try:
        return float((s or "").strip())
    except ValueError:
        return None


RETRIES = 4
BACKOFF = 3.0  # seconds, multiplied by attempt number


def fetch_text(url, timeout=TIMEOUT, retries=RETRIES):
    """Fetch one sheet as text, retrying transient network failures.

    Google's endpoint drops connections intermittently -- two SSLEOFError
    handshake failures in one afternoon here -- and the refresh runs unattended
    every six hours with a hard deadline at each round's lock. One flake must
    not cost a run, so transport errors are retried with a linear backoff.
    An HTTP error or an empty body is not retried: those mean the sheet moved
    or was unpublished, and repeating the request will not change it.

    Returns the body rather than parsed rows, because `ssa/provenance.py`
    archives exactly what arrived: a vintage reconstructed from parsed rows is
    our reading of the file, not the file.
    """
    last = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, timeout=timeout)
        except requests.RequestException as e:
            last = e
            if attempt < retries:
                time.sleep(BACKOFF * attempt)
                continue
            raise RuntimeError(
                f"Silver Bulletin sheet unreachable after {retries} attempts "
                f"({type(e).__name__}: {e}): {url}") from e
        r.raise_for_status()
        if not r.text.strip():
            raise RuntimeError(f"Silver Bulletin sheet returned nothing: {url}")
        return r.text
    raise RuntimeError(f"Silver Bulletin sheet unreachable: {url} ({last})")


def _records(rows, value_cols, subgroup=None, pollster=None, population=None,
             sponsor=None):
    """Poll-level records, newest last.

    subgroup/pollster/population/sponsor are matched case-insensitively;
    pollster and sponsor are substring matches, so 'YouGov' catches the
    sponsor-suffixed variants and 'Economist' catches 'The Economist'.
    """
    out = []
    for r in rows:
        if subgroup and (r.get("subgroup") or "").strip().lower() != subgroup.lower():
            continue
        if pollster and pollster.lower() not in (r.get("pollster") or "").lower():
            continue
        if sponsor and sponsor.lower() not in (r.get("sponsors") or "").lower():
            continue
        if population and (r.get("population") or "").strip().upper() != population.upper():
            continue
        start, end = _date(r.get("startdate")), _date(r.get("enddate"))
        if not start or not end or end < start:
            continue
        values = {c: _float(r.get(c)) for c in value_cols}
        if any(v is None for v in values.values()):
            continue
        rec = {
            # Field midpoint, matching how the rest of the pipeline dates a wave.
            "date": (start + (end - start) / 2),
            "start_date": start, "end_date": end,
            "pollster": (r.get("pollster") or "").strip(),
            "sponsors": (r.get("sponsors") or "").strip(),
            "n": int(_float(r.get("samplesize")) or 0),
            "population": (r.get("population") or "").strip().upper(),
            "url": (r.get("url") or "").strip(),
            # The day the poll entered the sheet: the arena's publication date.
            "created": _date(r.get("createddate")),
        }
        rec.update(values)
        out.append(rec)
    out.sort(key=lambda x: (x["date"], x["pollster"]))
    return out


def approval_polls(subgroup="All polls", pollster=None, population=None, rows=None,
                   sponsor=None):
    """Trump approval. Same record shape as votehub.approval_polls, so this is a
    drop-in replacement wherever poll records are averaged.

    subgroup='All polls' is overall approval; 'Economy', 'Immigration', 'Trade'
    and 'Cost' are issue-specific trackers in the same file.
    """
    rows = rows if rows is not None else fetch(APPROVAL_URL)
    recs = _records(rows, ("approve", "disapprove"), subgroup, pollster, population,
                    sponsor)
    for r in recs:
        r["net"] = round(r["approve"] - r["disapprove"], 2)
        # `value` is the quantity the averaging code scores by default, and
        # `margin` the approve-minus-disapprove spread -- the same two keys
        # votehub emits, so ssa/average.py needs no branch on the source.
        r["value"] = r["approve"]
        r["margin"] = r["net"]
    return recs


def generic_ballot_polls(subgroup="All polls", pollster=None, population=None, rows=None,
                         sponsor=None):
    """2026 generic congressional ballot. Same record shape as
    votehub.generic_ballot_polls; `value` and `margin` are both D minus R."""
    rows = rows if rows is not None else fetch(GENERIC_URL)
    recs = _records(rows, ("dem", "rep", "net"), subgroup, pollster, population,
                    sponsor)
    for r in recs:
        r["value"] = r["net"]
        r["margin"] = r["net"]
    return recs


def subgroups(rows):
    """Distinct subgroup labels in a fetched sheet, for discovery."""
    return sorted({(r.get("subgroup") or "").strip() for r in rows} - {""})


def to_series(records, key):
    """Poll records -> [{date, value}] oldest first, one point per field date.

    Several pollsters publish more than one wave ending on the same midpoint;
    the later field window wins, so the series always carries the freshest
    reading for a date rather than whichever row happened to sort last.
    """
    by_date = {}
    for r in records:
        d = r["date"].isoformat()
        prev = by_date.get(d)
        if prev is None or r["end_date"] >= prev["end_date"]:
            by_date[d] = r
    # Rounded to two places. `net` is a difference of two published percents
    # and arrives with float noise -- 4.100002 rather than 4.1 -- which would
    # be shown on the site and written into every resolution as if the extra
    # digits meant something.
    return [{"date": d, "value": round(float(by_date[d][key]), 2)}
            for d in sorted(by_date)]


def parse(text):
    """CSV text -> the poll rows every filter below reads."""
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise RuntimeError("Silver Bulletin sheet parsed to zero rows")
    return rows


def fetch(url, timeout=TIMEOUT, retries=RETRIES):
    """Fetch and parse in one step, for callers with nothing to archive."""
    return parse(fetch_text(url, timeout, retries))
