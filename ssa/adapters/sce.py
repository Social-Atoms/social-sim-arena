"""NY Fed Survey of Consumer Expectations, from the official all-history workbook.

  GET https://www.newyorkfed.org/medialibrary/interactives/sce/sce/downloads/data/frbny-sce-data.xlsx

One xlsx, every month since June 2013, republished in full with each monthly
release (~the first ten days of the following month, per the CMD calendar at
newyorkfed.org/microeconomics/calendar.html). The arena reads two columns of
the "Inflation expectations" sheet: the median one-year-ahead and median
three-year-ahead expected inflation rates.

**The NY Fed 403s generic clients.** A request without a desktop-browser
User-Agent is refused, so every request here sends one.

**The workbook is parsed with the stdlib, deliberately.** The repository's
dependencies are frozen at requests/jsonschema/pillow, and an xlsx is a zip of
XML: `zipfile` + `xml.etree` read it exactly, with nothing inferred. The sheet
is found by its *name* in workbook.xml (never by file position -- sheet4.xml is
an accident of authorship), and the two value columns are found by their exact
header strings (never by letter -- a workbook that reorders or renames its
columns must fail loudly, not serve the 25th percentile as the median).

**Date convention: rows are dated by the reference month, first of the month**
("2026-07-01" for the July 2026 survey), the same label-date convention the
Michigan series uses. Column A carries the month as a
YYYYMM integer (201306, ..., 202607) and that is all the workbook says; the
release *day* lives on the CMD calendar, not in the file. As with every
monthly label-dated series, `date < lock_at` cannot freeze this series -- a
round relies on the lock snapshot, like Michigan does.

**The archive is a dated capture, write-once per day**, under
`sources/sce/<fetch day, UTC>.xlsx`. Unlike the Michigan party addenda the
workbook carries no self-stamp with day precision (its Notes sheet is a
changelog, month-granular), so the vintage is named by the day it was captured
-- provenance.py's "a day is the unit" rule -- and the data's own as-of is the
newest reference month inside it, which `history` checks. The first capture of
a day wins; a re-fetch on the same day never rewrites the file, so a vintage a
resolution cites can never change under it.

**Serving is archive-first on failure, fail-loud on staleness.** A fetch
failure serves the newest committed vintage with a printed warning: a monthly
series with years of committed history must not take a whole refresh down
because one request timed out. But whatever answered -- network or archive -- `history` raises if
its newest reference month trails today by more than MAX_STALE_MONTHS. A
silently stale source is worse than no source (see ssa/adapters/fredcsv.py for
what that cost once), and a warning on the fallback path does not cover a
*live* workbook that quietly stopped being updated.
"""
import io
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARCHIVE = os.path.join(ROOT, "sources", "sce")

URL = ("https://www.newyorkfed.org/medialibrary/interactives/sce/sce/"
       "downloads/data/frbny-sce-data.xlsx")
TIMEOUT = 120
# The NY Fed refuses requests that do not look like a browser.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

SHEET = "Inflation expectations"
# The exact header strings, as row 4 of the sheet prints them. Six sibling
# columns carry the same shape (25th/75th percentiles, point predictions), so
# matching anything looser than the full string risks scoring the wrong one.
HEADERS = {
    "infl_1y": "Median one-year ahead expected inflation rate",
    "infl_3y": "Median three-year ahead expected inflation rate",
}

# 158 data rows in the August 2026 workbook (2013-06 through 2026-07), and the
# history only grows. A sheet that parses to a handful of rows is a layout
# change, not a short month.
MIN_ROWS = 150
# The survey began in June 2013 and the workbook is the *full* history. A
# parse that starts anywhere else read the wrong sheet or a truncated file.
FIRST_MONTH = "2013-06-01"
# Percent, one- and three-year-ahead medians. Observed range 2013-2026 is
# roughly 2.4 to 6.8; these bounds are loose enough to survive a deflation
# scare and a 1970s rerun, and tight enough that a YYYYMM date (201306), a
# year, or a percentile column read out of the wrong place cannot pass.
MIN_VALUE, MAX_VALUE = -5.0, 25.0
# How far the newest reference month may trail the day the data is served.
# The reference month lags its release by ~1 month (July data drops in early
# August) and a fetch can be a month before the next release, so 2 is normal
# life; 4 means the workbook -- or our archive -- has quietly stopped moving.
MAX_STALE_MONTHS = 4

HORIZONS = ("1y", "3y")

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_RID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_CELL = re.compile(r"^([A-Z]+)(\d+)$")
_YYYYMM = re.compile(r"^(19|20)\d{4}$")


def _is_live_failure(exc):
    """Only transport/status failures may use a same-source archive."""
    if isinstance(exc, (requests.RequestException, TimeoutError,
                        ConnectionError)):
        return True
    # Test/probe seams often raise RuntimeError instead of constructing a
    # requests.HTTPError. Keep those recognizable without treating validation
    # messages (not-an-xlsx, malformed workbook) as network failures.
    msg = str(exc).lower()
    return any(mark in msg for mark in (
        "http 401", "401 unauthorized", "http 403", "403 forbidden",
        "http 404", "404 not found", "http 429", "429 too many",
        "rate-limit", "rate limit", "timeout", "timed out", "unreachable",
        "connection", "sslerror", "sslzeroreturn"))


def fetch_bytes(timeout=TIMEOUT, url=URL):
    """The workbook exactly as served, as bytes, for `archive` to file.

    A body that is not a zip is a hard error, not a retry: this host answers
    HTML (an interstitial, an error page) with a 200 when it is unhappy, and
    an HTML page filed as a vintage would poison the archive-first fallback.
    """
    r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
    r.raise_for_status()
    body = r.content
    if body[:4] != b"PK\x03\x04":
        raise RuntimeError(
            f"{url} answered {len(body)} bytes that are not an xlsx "
            f"(starts {body[:16]!r}); the NY Fed serves HTML with a 200 to "
            "clients it dislikes, and an HTML page filed as a vintage would "
            "poison every later archive-first read")
    return body


def _shared_strings(z):
    try:
        raw = z.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(raw)
    return ["".join(t.text or "" for t in si.iter(_NS + "t"))
            for si in root.findall(_NS + "si")]


def _sheet_member(z, name=SHEET):
    """The zip member holding the named sheet, resolved via workbook.xml.

    By name and never by file position: `sheet4.xml` holding the fourth tab is
    an accident of how the workbook was authored, and the NY Fed reorders tabs
    (the workbook has gained sheets twice per its own Notes changelog).
    """
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    targets = {r.get("Id"): r.get("Target") for r in rels}
    names = []
    for sh in wb.find(_NS + "sheets"):
        names.append(sh.get("name"))
        if sh.get("name") == name:
            target = targets[sh.get(_RID)]
            return "xl/" + target.lstrip("/")
    raise RuntimeError(
        f"no sheet named {name!r} in the SCE workbook; it has: "
        + ", ".join(repr(n) for n in names))


def _cells(row, strings):
    """One <row> -> {column letter: value}, shared strings resolved.

    A cell without an address is a layout this parser has never seen, and
    guessing which column it meant is how a median becomes a percentile.
    """
    out = {}
    for c in row.findall(_NS + "c"):
        ref = c.get("r")
        if ref is None:
            raise RuntimeError("a cell in the SCE workbook has no address; "
                               "the sheet layout has changed")
        col = _CELL.match(ref)
        if not col:
            raise RuntimeError(f"unreadable cell address {ref!r}")
        v = c.find(_NS + "v")
        if v is None or v.text is None:
            continue
        if c.get("t") == "s":
            out[col.group(1)] = strings[int(v.text)]
        else:
            out[col.group(1)] = v.text
    return out


def parse(xlsx, min_rows=MIN_ROWS):
    """Workbook bytes -> one dict per reference month, oldest first.

        {"date": "2026-07-01", "infl_1y": 3.632..., "infl_3y": 3.261...}

    Every check raises. A month silently dropped, a median read out of a
    percentile column, or a truncated history would each hand downstream a
    number nothing can tell from a real one.

    `min_rows` is a parameter only so a fixture can exercise the parser
    without carrying thirteen years of history; pipeline callers take the
    default, and a fixture small enough to relax it also skips the
    FIRST_MONTH check for the same reason.
    """
    try:
        z = zipfile.ZipFile(io.BytesIO(xlsx))
    except zipfile.BadZipFile:
        raise RuntimeError(
            "the SCE body is not a readable xlsx (BadZipFile); refusing to "
            "parse whatever this is")
    strings = _shared_strings(z)
    sheet = ET.fromstring(z.read(_sheet_member(z)))
    data = sheet.find(_NS + "sheetData")
    if data is None:
        raise RuntimeError(f"sheet {SHEET!r} has no sheetData element")

    # Locate the two medians by their exact header strings. The header row is
    # whichever row carries both; six sibling columns (percentiles, point
    # predictions) share the same numeric shape, so the full strings are the
    # only safe anchor.
    cols = {}
    rows = []
    for row in data.findall(_NS + "row"):
        cells = _cells(row, strings)
        if not cols:
            hit = {key: col for col, val in cells.items()
                   for key, header in HEADERS.items() if val == header}
            if hit:
                missing = set(HEADERS) - set(hit)
                if missing:
                    raise RuntimeError(
                        "the SCE header row names only "
                        f"{sorted(set(hit))} -- missing "
                        f"{sorted(HEADERS[k] for k in missing)!r}; the "
                        "columns have been renamed or split")
                cols = hit
        # A row whose A-cell is a YYYYMM integer is a data row and must parse
        # as one -- detecting the shape separately from reading it is the
        # umichparty lesson: a filter that only ever *matches* turns a row it
        # cannot read into a row that was never there.
        a = cells.get("A")
        if a is None or not _YYYYMM.match(a.split(".")[0]):
            continue
        if not cols:
            raise RuntimeError(
                "the SCE sheet reached its data rows before naming its "
                "columns; refusing to read medians by column position")
        yyyymm = a.split(".")[0]
        year, month = int(yyyymm[:4]), int(yyyymm[4:])
        if not 1 <= month <= 12:
            raise RuntimeError(f"{yyyymm} is not a YYYYMM reference month")
        rec = {"date": f"{year:04d}-{month:02d}-01"}
        for key in HEADERS:
            raw = cells.get(cols[key])
            try:
                value = float(raw)
            except (TypeError, ValueError):
                raise RuntimeError(
                    f"{rec['date']}: column {cols[key]} "
                    f"({HEADERS[key]!r}) holds {raw!r}, not a number; a "
                    "month with no median is a misread, not a data point")
            if not MIN_VALUE < value < MAX_VALUE:
                raise RuntimeError(
                    f"{rec['date']}: {value} is not a median expected "
                    f"inflation rate (expected strictly between {MIN_VALUE} "
                    f"and {MAX_VALUE}); a column was read out of the wrong "
                    "place")
            rec[key] = value
        rows.append(rec)

    if len(rows) < min_rows:
        raise RuntimeError(
            f"the SCE inflation sheet parsed to {len(rows)} rows, fewer than "
            f"the {min_rows} it has carried since 2013; refusing to publish "
            "a series built from a table that stopped being read")
    # The workbook is the full history and the survey is monthly with no
    # skipped months: consecutive, no duplicates, starting at the survey's
    # first field month. A gap is a misparse, not a month off.
    if min_rows >= MIN_ROWS and rows[0]["date"] != FIRST_MONTH:
        raise RuntimeError(
            f"the SCE history starts at {rows[0]['date']}, not {FIRST_MONTH}; "
            "either the workbook was truncated or the wrong sheet was read")
    for prev, cur in zip(rows, rows[1:]):
        if _month_index(cur["date"]) != _month_index(prev["date"]) + 1:
            raise RuntimeError(
                f"the SCE history jumps from {prev['date']} to {cur['date']}; "
                "the survey has fielded every month since 2013, so a gap or a "
                "duplicate is a misparse")
    return rows


def _month_index(iso):
    return int(iso[:4]) * 12 + int(iso[5:7])


# --- the archive -------------------------------------------------------------
#
# `sources/sce/<fetch day, UTC>.xlsx`, committed. Write-once per day: the
# first capture wins, and a vintage a resolution cites can never change
# under it.


def _utc_today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def archive_path(day):
    return os.path.join(ARCHIVE, f"{day}.xlsx")


def archive(body, day=None):
    """File one workbook under its capture day. Returns the path.

    An existing vintage for the day is left untouched -- not compared, not
    rewritten -- so nothing downstream of a morning capture can be changed by
    an afternoon one.
    """
    if not isinstance(body, (bytes, bytearray)):
        raise TypeError("archive takes the workbook body as bytes")
    day = day or _utc_today()
    path = archive_path(day)
    if os.path.exists(path):
        return path
    os.makedirs(ARCHIVE, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(bytes(body))
    os.replace(tmp, path)
    return path


def archived_days():
    """Vintages held, oldest first, as `YYYY-MM-DD` strings."""
    if not os.path.isdir(ARCHIVE):
        return []
    return sorted(fn[:-5] for fn in os.listdir(ARCHIVE)
                  if fn.endswith(".xlsx") and len(fn) == 15)


def newest_archived():
    days = archived_days()
    return archive_path(days[-1]) if days else None


def read_archived(path=None):
    """The newest archived workbook as bytes, with its path."""
    path = path or newest_archived()
    if not path:
        raise RuntimeError(
            "no SCE workbook has been archived under "
            f"{os.path.relpath(ARCHIVE, ROOT)}; fetch one vintage and commit "
            "it (sce.history() does both) before anything can be served "
            "offline")
    with open(path, "rb") as f:
        return f.read(), path


def load(path=None, min_rows=MIN_ROWS):
    """The newest archived vintage, parsed."""
    body, _ = read_archived(path)
    return parse(body, min_rows=min_rows)


def history(fetch=True, today=None, diagnostics=None):
    """[{date, infl_1y, infl_3y}] oldest first. The pipeline's entry point.

    With fetch=True the live workbook is read, validated by a full parse
    *before* it is filed, and captured write-once under today's UTC date. A
    fetch failure serves the newest committed vintage with a loud warning --
    unless there is none, in which case the failure is re-raised, because an
    empty answer must never look like a quiet week.

    Whichever answered, a newest reference month more than MAX_STALE_MONTHS
    behind `today` raises. `today` exists for tests; every pipeline caller
    takes the clock.
    """
    rows = None
    live_error = None
    if fetch:
        try:
            body = fetch_bytes()
        except Exception as e:                                  # noqa: BLE001
            if not _is_live_failure(e) or newest_archived() is None:
                raise
            live_error = e
            print(f"  sce: fetch failed ({type(e).__name__}: "
                  f"{str(e)[:120]}); serving the committed archive")
        else:
            # Only transport failures may degrade to the archive. A workbook
            # that arrived but no longer satisfies the parser contract, or one
            # we could not preserve, is unusable and must fail the refresh.
            rows = parse(body)
            archive(body)
    if rows is None:
        rows = load()
    stale = _month_index(today or _utc_today()) - _month_index(rows[-1]["date"])
    if stale > MAX_STALE_MONTHS:
        raise RuntimeError(
            f"the newest SCE reference month is {rows[-1]['date']}, "
            f"{stale} months behind today -- over the {MAX_STALE_MONTHS} "
            "allowed. A silently stale source is worse than no source: see "
            "ssa/adapters/fredcsv.py for what that cost once.")
    if live_error is not None and diagnostics is not None:
        path = newest_archived()
        diagnostics.append({
            "source": "sce",
            "scope": "live",
            "error": live_error,
            "archive_evidence": (
                f"{os.path.relpath(path, ROOT)} ({len(rows)} rows; newest "
                f"reference month {rows[-1]['date']})"),
        })
    return rows


def to_series(rows, horizon):
    """One horizon's median as `[{date, value}]`, oldest first.

    `rows` may be None, in which case the newest archived vintage is parsed
    (never the network -- fetching is `history`'s job).
    """
    if horizon not in HORIZONS:
        raise ValueError(
            f"unknown horizon {horizon!r}; expected one of {HORIZONS}")
    if rows is None:
        rows = load()
    return [{"date": r["date"], "value": r["infl_" + horizon]} for r in rows]
