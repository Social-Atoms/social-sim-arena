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
resolution cites can never change under it. A scheduled release day also keeps
`<day>.post.xlsx`, the first capture after the release that holds the new
month, so the bytes a round resolves on are archived (see `archive`).

**Serving is archive-first on failure, fail-loud on staleness.** A fetch
failure serves the newest committed vintage with a printed warning: a monthly
series with years of committed history must not take a whole refresh down
because one request timed out. But whatever answered -- network or archive -- `history` raises if
its newest reference month trails today by more than MAX_STALE_MONTHS. A
silently stale source is worse than no source (see ssa/adapters/fredcsv.py for
what that cost once), and a warning on the fallback path does not cover a
*live* workbook that quietly stopped being updated.
"""
import hashlib
import io
import json
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

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
#
# **Except on a scheduled release day, which keeps a second capture.** The
# refresh runs at 00:17, 06:17, 12:17 and 18:17Z and the NY Fed releases at
# 11:00 ET (15:00Z in daylight time, 16:00Z in standard time), so the day's
# first capture always predates the release and the file named for release
# day held the previous month: the bytes a round resolved on were archived
# nowhere (found by the housing track research pass, 2026-11-08). On a release
# day, a capture taken at or after the release instant whose newest reference
# month is newer than the day's first file is filed once more as
# `sources/sce/<day>.post.xlsx`, itself write-once. A post-release fetch
# that still holds the old month (the release is late) files nothing, so the
# next run can still catch it. The release instant is the later of the
# rounds' `release_at` and 11:00 America/New_York that day: the stored hour
# is 15:00Z all year, an hour early in standard time.

POST = ".post"
SEASON = os.path.join(ROOT, "questions", "season0.json")
TRACKER = "ny_fed_sce"
RELEASE_LOCAL = (11, 0)
NY = ZoneInfo("America/New_York")


def _utc_today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def archive_path(day):
    return os.path.join(ARCHIVE, f"{day}.xlsx")


def post_path(day):
    """The release day's second capture (see the notes above)."""
    return os.path.join(ARCHIVE, f"{day}{POST}.xlsx")


def release_instants(season_path=None):
    """{UTC day: release instant} for every scheduled SCE release in the
    season, read from the frozen round definitions (never edited here)."""
    try:
        with open(season_path or SEASON) as f:
            rounds = json.load(f).get("rounds", [])
    except (OSError, ValueError):
        return {}
    out = {}
    for r in rounds:
        if r.get("tracker") != TRACKER or not r.get("release_at"):
            continue
        at = datetime.strptime(r["release_at"], "%Y-%m-%dT%H:%M:%SZ") \
            .replace(tzinfo=timezone.utc)
        day = at.date()
        local = datetime.combine(day, time(*RELEASE_LOCAL), NY).astimezone(timezone.utc)
        key = day.isoformat()
        out[key] = max(out.get(key, at), at, local)
    return out


def _newest_month(body):
    return parse(body)[-1]["date"]


def archive(body, day=None, now=None, releases=None, newest=None):
    """File one workbook under its capture day. Returns the path it is in.

    An existing vintage for the day is left untouched -- not compared, not
    rewritten -- so nothing downstream of a morning capture can be changed by
    an afternoon one. The one exception is a scheduled release day (`releases`,
    default `release_instants()`): a capture at or after the release instant
    whose newest month (`newest`, else parsed) is newer than the day's file
    is filed once as `<day>.post.xlsx`.
    """
    if not isinstance(body, (bytes, bytearray)):
        raise TypeError("archive takes the workbook body as bytes")
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    today = now.date().isoformat()
    day = day or today
    path = archive_path(day)
    if not os.path.exists(path):
        _write(path, body)
        return path
    if day != today:
        return path                        # a second capture is only ever today's
    release = (release_instants() if releases is None else releases).get(day)
    if release is None or now < release:
        return path
    post = post_path(day)
    if os.path.exists(post):
        return post
    with open(path, "rb") as f:
        first = f.read()
    try:
        held = _newest_month(first)
    except RuntimeError:
        held = ""                          # an unreadable morning file: keep the new one
    if (newest or _newest_month(bytes(body))) <= held:
        return path                        # the release has not landed yet
    _write(post, body)
    return post


def _write(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(bytes(body))
    os.replace(tmp, path)


def archived_days():
    """Capture days held, oldest first, as `YYYY-MM-DD` strings."""
    if not os.path.isdir(ARCHIVE):
        return []
    return sorted(fn[:-5] for fn in os.listdir(ARCHIVE)
                  if fn.endswith(".xlsx") and len(fn) == 15)


def archived_vintages():
    """Every vintage held, oldest first, as paths: a day's `.post` capture
    sorts after that day's first one."""
    if not os.path.isdir(ARCHIVE):
        return []
    out = []
    for fn in os.listdir(ARCHIVE):
        if fn.endswith(".xlsx") and len(fn) == 15:
            out.append((fn[:10], 0, fn))
        elif fn.endswith(POST + ".xlsx") and len(fn) == 15 + len(POST):
            out.append((fn[:10], 1, fn))
    return [os.path.join(ARCHIVE, fn) for _, _, fn in sorted(out)]


def newest_archived():
    vintages = archived_vintages()
    return vintages[-1] if vintages else None


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
            archive(body, newest=rows[-1]["date"])
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


# --- moving expectations (card nyfed-sce-moving@v2) --------------------------
#
# The same workbook, two more sheets, nothing fetched: the rounds read the
# vintages `history` above already archives (card nyfed-sce-moving rev 2,
# sha256 bd4130d2434db06cd3620aa3d6cef9e92165e08c081c0fe38b31f1f0f2faf676,
# docs/data-cards/nyfed-sce-moving.json). The inflation reader above is left
# byte-identical; the moving reader shares only its zip/XML helpers.
#
# - "Moving expectations Demo": the mean percent chance of moving to a
#   different primary residence in the next 12 months, by demographic cell.
#   The card scores ten of its fifteen columns as one profile; education and
#   numeracy are left out. Located by sheet name and the exact header string.
# - "Moving expectations": the national mean, shown and not scored.
#
# Values are kept at the workbook's full stored precision (float32 written as
# text, e.g. 25.86676597595215): the card scores them unrounded.
#
# **Resolution is "the first archived workbook carrying month M"**, not the
# file dated release day: the day's first capture predates the 11:00 ET
# release (sce-capture-bug.md, 74 captures read). `moving_resolution` walks
# the vintages in order -- a day's `.post` capture after its morning file --
# and takes the first, on or after the release day, whose newest month is M.
# In 74 daily captures no published month's value was ever revised, so the
# first vintage carrying M is also the release-day number.

MOVING_SHEET = "Moving expectations"
MOVING_DEMO_SHEET = "Moving expectations Demo"
MOVING_NATIONAL = "national"
MOVING_NATIONAL_HEADER = ("Mean probability of changing primary residence "
                          "over the next 12 months")
# The ten scored cells, in the card's order (age 3, income 3, region 4).
MOVING_CELLS = (
    "Age Under 40", "Age 40-60", "Age Over 60",
    "Income under 50k", "Income 50-100k", "Income Over 100k",
    "Region West", "Region Midwest", "Region South", "Region Northeast",
)
# A percent chance. The card: a value outside 0-100 is a misread, and the
# round waits for a maintainer rather than resolving on it.
MOVING_MIN, MOVING_MAX = 0.0, 100.0
# The card's cancellation window: no vintage carrying M by release + 7 days.
MOVING_CANCEL_DAYS = 7


def _read_columns(xlsx, sheet_name, headers, lo, hi, min_rows, what):
    """One sheet -> [{date, key: value}] oldest first, columns by exact header.

    The moving sheets' twin of `parse`, with the same refusals: the sheet by
    name, every column by its full header string, a YYYYMM row that does not
    read as numbers raises, and the history must be consecutive months from
    June 2013. Bounds are inclusive (`lo <= value <= hi`).
    """
    try:
        z = zipfile.ZipFile(io.BytesIO(xlsx))
    except zipfile.BadZipFile:
        raise RuntimeError(
            "the SCE body is not a readable xlsx (BadZipFile); refusing to "
            "parse whatever this is")
    strings = _shared_strings(z)
    sheet = ET.fromstring(z.read(_sheet_member(z, sheet_name)))
    data = sheet.find(_NS + "sheetData")
    if data is None:
        raise RuntimeError(f"sheet {sheet_name!r} has no sheetData element")
    cols, rows = {}, []
    for row in data.findall(_NS + "row"):
        cells = _cells(row, strings)
        if not cols:
            hit = {key: col for col, val in cells.items()
                   for key, header in headers.items() if val == header}
            if hit:
                missing = set(headers) - set(hit)
                if missing:
                    raise RuntimeError(
                        f"the SCE {sheet_name!r} header row lacks "
                        f"{sorted(headers[k] for k in missing)!r}; the "
                        "columns have been renamed or split")
                cols = hit
        a = cells.get("A")
        if a is None or not _YYYYMM.match(a.split(".")[0]):
            continue
        if not cols:
            raise RuntimeError(
                f"the SCE {sheet_name!r} sheet reached its data rows before "
                "naming its columns; refusing to read by column position")
        yyyymm = a.split(".")[0]
        year, month = int(yyyymm[:4]), int(yyyymm[4:])
        if not 1 <= month <= 12:
            raise RuntimeError(f"{yyyymm} is not a YYYYMM reference month")
        rec = {"date": f"{year:04d}-{month:02d}-01"}
        for key, header in headers.items():
            raw = cells.get(cols[key])
            try:
                value = float(raw)
            except (TypeError, ValueError):
                raise RuntimeError(
                    f"{rec['date']}: column {cols[key]} ({header!r}) holds "
                    f"{raw!r}, not a number; a month with no {what} is a "
                    "misread, not a data point")
            if not lo <= value <= hi:
                raise RuntimeError(
                    f"{rec['date']}: {value} in {header!r} is not a {what} "
                    f"(expected {lo} to {hi}); a column was read out of the "
                    "wrong place")
            rec[key] = value
        rows.append(rec)
    if len(rows) < min_rows:
        raise RuntimeError(
            f"the SCE {sheet_name!r} sheet parsed to {len(rows)} rows, fewer "
            f"than the {min_rows} it has carried since 2013")
    if min_rows >= MIN_ROWS and rows[0]["date"] != FIRST_MONTH:
        raise RuntimeError(
            f"the SCE {sheet_name!r} history starts at {rows[0]['date']}, not "
            f"{FIRST_MONTH}; the workbook was truncated or the wrong sheet read")
    for prev, cur in zip(rows, rows[1:]):
        if _month_index(cur["date"]) != _month_index(prev["date"]) + 1:
            raise RuntimeError(
                f"the SCE {sheet_name!r} history jumps from {prev['date']} to "
                f"{cur['date']}; a gap or a duplicate is a misparse")
    return rows


def parse_moving(xlsx, min_rows=MIN_ROWS):
    """Workbook bytes -> one dict per reference month, oldest first.

        {"date": "2026-09-01", "national": 14.248..., "Age Under 40": 25.866...,
         ... one key per MOVING_CELLS header}

    The two sheets must cover the same months: a national row without its
    cells (or the reverse) is a workbook mid-edit, not a release.
    """
    demo = _read_columns(xlsx, MOVING_DEMO_SHEET,
                         {c: c for c in MOVING_CELLS},
                         MOVING_MIN, MOVING_MAX, min_rows,
                         "percent chance of moving")
    nat = _read_columns(xlsx, MOVING_SHEET,
                        {MOVING_NATIONAL: MOVING_NATIONAL_HEADER},
                        MOVING_MIN, MOVING_MAX, min_rows,
                        "percent chance of moving")
    if [r["date"] for r in demo] != [r["date"] for r in nat]:
        raise RuntimeError(
            f"the SCE moving sheets disagree on their months: the demo sheet "
            f"runs {demo[0]['date']}..{demo[-1]['date']} ({len(demo)} rows), "
            f"the national sheet {nat[0]['date']}..{nat[-1]['date']} "
            f"({len(nat)} rows)")
    return [dict(d, national=n[MOVING_NATIONAL]) for d, n in zip(demo, nat)]


def vintage_day(path):
    """The capture day a vintage's file name carries (`.post` or not)."""
    return os.path.basename(path)[:10]


def moving_history(today=None, path=None, min_rows=MIN_ROWS):
    """The moving rows from the newest archived vintage. Never the network.

    Run after `history`, which files the day's capture first, so the rows
    come from the same vintage the rounds resolve on. Raises, like `history`,
    when the newest month trails `today` by more than MAX_STALE_MONTHS.
    """
    body, _ = read_archived(path)
    rows = parse_moving(body, min_rows=min_rows)
    stale = _month_index(today or _utc_today()) - _month_index(rows[-1]["date"])
    if stale > MAX_STALE_MONTHS:
        raise RuntimeError(
            f"the newest SCE moving month is {rows[-1]['date']}, {stale} "
            f"months behind today -- over the {MAX_STALE_MONTHS} allowed")
    return rows


def moving_series(rows, cell):
    """One cell (a MOVING_CELLS header, or MOVING_NATIONAL) as [{date, value}]."""
    if cell != MOVING_NATIONAL and cell not in MOVING_CELLS:
        raise ValueError(f"unknown SCE moving cell {cell!r}; expected one of "
                         f"{MOVING_CELLS} or {MOVING_NATIONAL!r}")
    if rows is None:
        rows = moving_history()
    return [{"date": r["date"], "value": r[cell]} for r in rows]


def survey_month(release_at):
    """The survey month M a release reports: the month before the release's.

    Every SCE release lands early in M+1 (the December survey in January), so
    a round needs no separate field to say which month it asks about.
    """
    year, month = int(release_at[:4]), int(release_at[5:7])
    i = year * 12 + (month - 1) - 1
    return f"{i // 12:04d}-{i % 12 + 1:02d}-01"


def first_vintage_carrying(month, release_day, vintages=None, parse=parse_moving):
    """(path, rows) of the first archived vintage on or after `release_day`
    whose newest reference month is `month`, or (None, None).

    A vintage that already carries a *later* month than M without any earlier
    one carrying M means the archive skipped a whole release; that raises for
    a maintainer rather than reading M out of a later file.
    """
    for path in (archived_vintages() if vintages is None else vintages):
        if vintage_day(path) < release_day:
            continue
        with open(path, "rb") as f:
            rows = parse(f.read())
        newest = rows[-1]["date"]
        if newest < month:
            continue
        if newest > month:
            raise RuntimeError(
                f"{os.path.relpath(path, ROOT)} is the first archived vintage "
                f"carrying {month[:7]}, but its newest month is {newest[:7]}: "
                "no archived file holds the release that first printed it")
        return path, rows
    return None, None


def moving_resolution(month, release_at, cells=MOVING_CELLS, now=None,
                      vintages=None):
    """The card's resolution for survey month `month` ("YYYY-MM-01").

    Returns {"values": {cell: value}, "vintage": path, "sha256", "newest"}.
    Raises -- never a partial profile -- when no vintage carries M yet (with
    the cancellation date once release + 7 days has passed), or when a cell
    is missing or out of range in it (parse_moving refuses the whole file).
    """
    release_day = release_at[:10]
    path, rows = first_vintage_carrying(month, release_day, vintages)
    if path is None:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        release = datetime.strptime(release_at, "%Y-%m-%dT%H:%M:%SZ") \
            .replace(tzinfo=timezone.utc)
        deadline = release + timedelta(days=MOVING_CANCEL_DAYS)
        tail = (f"past the card's cancellation deadline {deadline:%Y-%m-%d}: "
                "cancel the round, never resolve it from a later month"
                if now > deadline else
                f"the round waits; it is cancelled if none arrives by "
                f"{deadline:%Y-%m-%d}")
        raise ValueError(
            f"no archived SCE workbook on or after {release_day} carries "
            f"{month[:7]} yet; {tail}")
    row = next(r for r in rows if r["date"] == month)
    with open(path, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    return {"values": {c: row[c] for c in cells}, "vintage": path,
            "sha256": sha, "newest": rows[-1]["date"]}
