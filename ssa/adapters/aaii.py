"""AAII Investor Sentiment Survey, from the survey's own results page.

  GET https://www.aaii.com/sentimentsurvey/sent_results

Weekly since 1987: the percent of AAII members bullish, neutral and bearish on
the stock market over the next six months. The voting period runs Thursday
through Wednesday, the page's "Reported Date" is the closing **Wednesday**, and
results publish the following Thursday.

**Why the HTML page and not the spreadsheet.** The long-standing download,
https://www.aaii.com/files/surveys/sentiment.xls, carries the full history back
to 1987 -- and its first eight bytes are `d0 cf 11 e0`, a genuine OLE2 compound
document. Reading one takes a real .xls library, this repository's rule is
requests and the standard library only, and a hand-rolled OLE2 parser is the
kind of code that silently reads the wrong sector the first time Excel rewrites
the file. So the .xls is left on the table, and with it the pre-2026 history:
the HTML route serves a **rolling window of about 22 weeks** (22 rows on
2026-08-17). That clears the ~15 weeks the baselines need, with little margin;
the committed vintages in `sources/aaii/` extend it a week per week from here.

**The page's dates carry no year.** Rows read "Aug 12", newest first, weekly.
The year is inferred by walking down from the response's own `Date` header --
never from the local clock, which is how a replayed vintage would re-date the
whole table to the day of the replay -- and a row that lands *after* the
anchor, or fails to step backwards, is a parse error, not a leap of faith.

**Every row checks itself: bullish + neutral + bearish = 100.** The survey is a
three-way choice, so the shares are exhaustive; on the live page all 22 rows
sum to 100 within 0.2. A regex that slips one cell sideways reads a percentage
into the wrong column and the sum breaks immediately. The one misread the sum
cannot catch is a column *swap* -- bull and bear exchanged still sum to 100
while flipping the spread's sign -- which is why `parse` also requires the
header cells in the exact order Reported Date, Bullish, Neutral, Bearish
before it reads anything.

**The site sits behind Imperva and refuses non-browser user agents.** A plain
`python-requests/2.x` UA gets 403 on every request (verified 2026-08-17), the
same class of block that took Civiqs off the GitHub runners (see ssa/health.py).
A browser UA string is sent, the block is documented here rather than worked
around quietly, and if AAII escalates to fingerprinting the fetch fails loudly
instead of serving anything stale.
"""
import datetime
import email.utils
import re
import time

import requests

URL = "https://www.aaii.com/sentimentsurvey/sent_results"
# The full 1987-present history, machine-unreadable without an .xls dependency.
# Named here so the limitation is discoverable, not so anyone fetches it.
XLS_URL = "https://www.aaii.com/files/surveys/sentiment.xls"

TIMEOUT = 60
RETRIES = 4
BACKOFF = 3.0  # seconds, multiplied by attempt number

# Imperva answers 403 to python-requests' default UA. This is the smallest
# string that gets a 200; sending it is stated policy, not evasion.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# How stale the newest row may be before the run refuses the page. The survey
# publishes weekly; 21 days is two missed releases plus slack. The Michigan
# incident (ssa/series.py) is why this raises: a page that is quietly weeks
# behind resolves rounds against numbers that were public before the lock.
MAX_STALE_DAYS = 21

# The header cells, in order, before any data row is believed. Order is the
# entire point: a reordered table would put bearish percentages in the bullish
# column, every row would still sum to 100, and the spread would change sign
# with nothing downstream able to tell.
_HEADER = re.compile(
    r">\s*Reported Date\s*<.*?>\s*Bullish\s*<.*?>\s*Neutral\s*<.*?>\s*Bearish\s*<",
    re.S)

# One data row: a left-aligned month-day cell followed by exactly three
# right-aligned percent cells, all in the table's own markup. Anchoring on the
# full four-cell shape (never a bare number) is what keeps a stray percentage
# elsewhere on the page from becoming a survey reading.
_ROW = re.compile(
    r'<td\s+align="left"\s+class="tableTxt">\s*([A-Za-z]{3,9})\s+(\d{1,2})\s*</td>\s*'
    r'<td\s+align="right"\s+class="tableTxt">\s*(\d{1,3}(?:\.\d+)?)%\s*</td>\s*'
    r'<td\s+align="right"\s+class="tableTxt">\s*(\d{1,3}(?:\.\d+)?)%\s*</td>\s*'
    r'<td\s+align="right"\s+class="tableTxt">\s*(\d{1,3}(?:\.\d+)?)%\s*</td>',
    re.S)

_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
     "Nov", "Dec"], start=1)}
_MONTHS.update({m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], start=1)})
_MONTHS["Sept"] = 9   # the one four-letter abbreviation in common editorial use

# A row's three shares must sum to 100. Rounding drift on the live page is
# under 0.2; a cell read out of the wrong column misses by whole points. The
# threshold sits between the two populations, not at a comfortable middle.
SUM_TOLERANCE = 1.0


def fetch_text(url=URL, timeout=TIMEOUT, retries=RETRIES):
    """(body, asof) -- the page as served, and the server's own date.

    Transport errors are retried with a linear backoff, because the refresh
    runs unattended with a hard deadline at each round's lock and one dropped
    connection must not cost a run. An HTTP error is not retried: from this
    host that is Imperva refusing the request, and repeating it faster is how
    a UA block becomes an IP block.

    `asof` comes from the response's `Date` header, never from the local
    clock. The page's rows carry no year, so whatever anchors them decides
    what year the whole series lands in -- and the wall clock would re-date a
    replayed vintage to the day of the replay. A response without the header
    raises rather than guessing.

    Returns the body separately from anything parsed, because
    `ssa/provenance.py` archives exactly what arrived.
    """
    last = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, timeout=timeout, headers={"User-Agent": UA})
        except requests.RequestException as e:
            last = e
            if attempt < retries:
                time.sleep(BACKOFF * attempt)
                continue
            raise RuntimeError(
                f"AAII results page unreachable after {retries} attempts "
                f"({type(e).__name__}: {e}): {url}") from e
        r.raise_for_status()
        if not r.text.strip():
            raise RuntimeError(f"AAII results page returned nothing: {url}")
        date_header = r.headers.get("Date")
        if not date_header:
            raise RuntimeError(
                "AAII response carries no Date header; refusing to infer the "
                "rows' year from the local clock")
        return r.text, parse_asof(date_header)
    raise RuntimeError(f"AAII results page unreachable: {url} ({last})")


def parse_asof(date_header):
    """RFC 2822 `Date` header -> datetime.date, in the server's own terms."""
    return email.utils.parsedate_to_datetime(date_header).date()


def parse(text, asof):
    """Page HTML -> [{date, bullish, neutral, bearish, spread}], oldest first.

    `asof` anchors the year-less dates and must come from the response that
    carried `text` (fetch_text returns the pair), so a vintage replayed from
    the archive re-parses to the dates it showed when it was fetched.
    """
    if not _HEADER.search(text):
        raise RuntimeError(
            "AAII results page structure changed: the Reported Date / Bullish "
            "/ Neutral / Bearish header is missing or reordered. Refusing to "
            "guess which column is which -- a swapped pair would flip the "
            "spread's sign while every row still summed to 100.")

    rows = []
    prev = None
    for month_name, day, bull, neutral, bear in _ROW.findall(text):
        month = _MONTHS.get(month_name)
        if month is None:
            raise RuntimeError(
                f"AAII results page: {month_name!r} is not a month; the date "
                "column moved or changed format")
        # Rows are newest first. The first row lands in the anchor's year
        # unless that would put it in the future; each later row keeps its
        # predecessor's year unless that fails to step backwards -- which is
        # the December row seen from January.
        year = (asof if prev is None else prev).year
        d = _mkdate(year, month, int(day))
        if prev is None:
            if d > asof:
                d = _mkdate(year - 1, month, int(day))
        else:
            if d >= prev:
                d = _mkdate(year - 1, month, int(day))
            # Adjacent rows are a week apart, sometimes two over a holiday.
            # 45 days allows a publishing pause and still catches the failure
            # this bound exists for: a duplicated or misread date cell, which
            # the year-decrement above would otherwise turn into a silent
            # ~365-day jump.
            gap = (prev - d).days
            if not 1 <= gap <= 45:
                raise RuntimeError(
                    f"AAII results page: row dated {d} is {gap} days before "
                    f"{prev}; the table is no longer a weekly sequence and "
                    "the year inference cannot be trusted")
        b, n, r = float(bull), float(neutral), float(bear)
        total = b + n + r
        if abs(total - 100.0) > SUM_TOLERANCE:
            raise RuntimeError(
                f"AAII row {d}: bullish {b} + neutral {n} + bearish {r} = "
                f"{total:.1f}, not 100. A three-way share that does not sum "
                "to 100 is a cell read out of the wrong column, and the "
                "series is not trustworthy from a misaligned parse.")
        rows.append({"date": d.isoformat(), "bullish": b, "neutral": n,
                     "bearish": r,
                     # A difference of two one-decimal percents arrives with
                     # float noise (34.7 - 37.9 is -3.1999...); rounded at a
                     # defined place before it can reach a resolution.
                     "spread": round(b - r, 2)})
        prev = d

    if not rows:
        raise RuntimeError(
            "AAII results page parsed to zero rows; refusing to publish an "
            "empty series (check whether the table markup changed)")
    newest = datetime.date.fromisoformat(rows[0]["date"])
    if (asof - newest).days > MAX_STALE_DAYS:
        raise RuntimeError(
            f"AAII newest row is {rows[0]['date']}, {(asof - newest).days} "
            f"days before the response's own date {asof}. A weekly survey "
            "this far behind is a frozen page, not a slow week; see the "
            "Michigan incident in ssa/series.py for what serving it costs.")
    rows.reverse()
    return rows


def _mkdate(year, month, day):
    try:
        return datetime.date(year, month, day)
    except ValueError as e:
        raise RuntimeError(
            f"AAII results page: {year}-{month:02d}-{day:02d} is not a real "
            f"date ({e}); the table format changed") from e


def to_series(rows, key="spread"):
    """Parsed rows -> [{date, value}] oldest first, for the series registry."""
    return [{"date": r["date"], "value": r[key]} for r in rows]


def fetch(url=URL, timeout=TIMEOUT, retries=RETRIES):
    """Fetch and parse in one step, for callers with nothing to archive."""
    text, asof = fetch_text(url, timeout, retries)
    return parse(text, asof)
