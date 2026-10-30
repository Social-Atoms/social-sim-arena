"""European Commission consumer survey (DG ECFIN Business and Consumer Surveys).

  GET https://ec.europa.eu/economy_finance/db_indicators/surveys/documents/
      series/nace2_ecfin_{YYMM}/main_indicators_sa_nace2.zip

One zip per monthly release, holding one workbook (`main_indicators_nace2.xlsx`)
with the full seasonally adjusted history of every sectoral confidence
indicator for the EU, the euro area and each country in the programme. The
arena reads one column family of its MONTHLY sheet, `<GEO>.CONS`: the consumer
confidence indicator, a balance in percentage points.

**The release id is in the URL, and old releases stay up.** `{YYMM}` is the
reference month the release is about (`2610` is the October 2026 survey,
published 29 October 2026), and each month's file stays at its own address:
on 2026-10-29 every vintage probed from `nace2_ecfin_2306` to `nace2_ecfin_2610`
still answered (2303 and older redirect), and the Last-Modified stamps of
2608, 2609 and 2610 all predate their release day (2510's is 27 October 2025),
so a release is not rewritten after it is published. A past release is addressable as of its own
date, which is what docs/sources.md §1.2 asks for, and the first print of any
month can be read back from the publisher rather than from us. A vintage that
does not exist yet answers **301 to the survey's home page**, not 404; the
adapter does not follow redirects, so "not published yet" can never be read
as an HTML page.

**Two releases a month, and the arena reads the second.** The calendar
(`documents/calendar/Publication dates 2026.pdf`) fixes a flash estimate for the
EU and euro area only at 16:00 Brussels time about a week before month end,
and the full release with every country at 11:00 Brussels time at month end
(the December survey is published in early January). The flash is a PDF press
release; the time-series zip appears with the full release. Over the thirteen
months October 2025 to October 2026 the flash and the full release's first
EU/EA print were identical in every month but one: December 2025, when Spain's
return to the aggregate put the full release 1.2 (EU) and 1.5 (euro area)
points above the flash. A round on the EU or euro-area number must therefore
lock before the flash, not before the full release, and resolves on the full
release.

**First print, pinned.** The seasonal adjustment and the aggregates are
recomputed every month, and the release notes inside the workbook (INFO sheet)
record level shifts that rewrite whole country histories: Poland's new
provider in May 2026 moved its April 2026 print from -4.9 to -16.5. So the
number a round resolves on is the reference month's value in *that month's*
vintage, and later vintages never overwrite it. The archive is what makes that
mechanical: `sources/ecbcs/<YYMM>.zip`, the release exactly as served,
write-once per vintage. Keyed by the publisher's release id rather than the
capture day because the release is the unit here: a month has exactly one, and
filing the same 700 KB body again every day would add nothing a reader could
use.

**A country can be missing from its own release.** Italy's September 2026 value
was absent from the September vintage (a methodological break, per the
October flash's footnote) and appeared in October's; Spain was suspended from
October 2024 until the December 2025 release and then backfilled. A country
row that ends before the release month is therefore data, not a misparse; the
EU and euro-area rows are the release's own anchor and must carry the release
month.

**The workbook is parsed with the stdlib**, as `ssa/adapters/sce.py` does: an
xlsx is a zip of XML. The sheet is found by its name in workbook.xml and every
column by its exact header (`EU.CONS`, never a column letter), so a reordered
workbook fails loudly instead of serving the services indicator as consumer
confidence.
"""
import io
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARCHIVE = os.path.join(ROOT, "sources", "ecbcs")

URL = ("https://ec.europa.eu/economy_finance/db_indicators/surveys/documents/"
       "series/nace2_ecfin_{vintage}/main_indicators_sa_nace2.zip")
TIMEOUT = 120
UA = "social-simulation-arena/1.0 (research benchmark; contact via repository)"

SHEET = "MONTHLY"
INDICATOR = "CONS"
# The release's own anchor: both aggregates are published every month and
# carry the release month. A workbook whose aggregates end elsewhere is the
# wrong file, not a short month.
ANCHORS = ("EU", "EA")
# The geographies the registry reads. `parse` keeps every `<GEO>.CONS` column
# it finds; these are the ones that must be present for a release to be filed.
REQUIRED = ANCHORS + ("DE", "FR", "IT", "ES", "NL", "PL", "AT", "BE", "SE",
                      "IE")
# The aggregates run without a gap from January 1985 in every vintage read.
FIRST_MONTH = "1985-01-01"
# Balances are percent-positive minus percent-negative, averaged over four
# questions, so they are bounded by +/-100 by construction. Observed since
# 1985: -42.5 (PL) to +12.9 (ES).
MIN_VALUE, MAX_VALUE = -100.0, 100.0
# How far the newest reference month may trail the day it is served. The
# December survey is published in early January, so two is normal life for the
# first week of a year; four means the archive or the publisher has stopped.
MAX_STALE_MONTHS = 3

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_RID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_CELL = re.compile(r"^([A-Z]+)(\d+)$")
_HEADER = re.compile(r"^([A-Z]{2})\." + INDICATOR + r"$")
_VINTAGE = re.compile(r"^\d{2}(0[1-9]|1[0-2])$")
# Excel's day zero for the 1900 date system, as every modern writer uses it.
_EXCEL_EPOCH = date(1899, 12, 30)


class NotPublished(Exception):
    """The vintage's URL does not hold a release (yet)."""


def url_for(vintage):
    if not _VINTAGE.match(vintage or ""):
        raise ValueError(f"{vintage!r} is not a YYMM release id")
    return URL.format(vintage=vintage)


def vintage_of(month_iso):
    """'2026-10-01' -> '2610'."""
    return month_iso[2:4] + month_iso[5:7]


def month_of(vintage):
    """'2610' -> '2026-10-01'."""
    return f"20{vintage[:2]}-{vintage[2:]}-01"


def _is_live_failure(exc):
    """Only transport failures may fall back to the committed archive."""
    return isinstance(exc, (requests.RequestException, TimeoutError,
                            ConnectionError))


def fetch_bytes(vintage, timeout=TIMEOUT):
    """One release zip exactly as served, for `archive` to file.

    Redirects are not followed: an unpublished vintage answers 301 to the
    survey's home page, and following it would hand the parser an HTML page
    with a 200. A body that is not a zip is refused for the same reason.
    """
    url = url_for(vintage)
    r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout,
                     allow_redirects=False)
    if r.status_code in (301, 302, 303, 307, 308, 404):
        raise NotPublished(
            f"{url} answered HTTP {r.status_code}; release {vintage} is not "
            "published (yet)")
    r.raise_for_status()
    body = r.content
    if body[:4] != b"PK\x03\x04":
        raise RuntimeError(
            f"{url} answered {len(body)} bytes that are not a zip (starts "
            f"{body[:16]!r}); refusing to file it as a release")
    return body


def _workbook(body):
    """The release zip -> the inner workbook as a ZipFile."""
    try:
        outer = zipfile.ZipFile(io.BytesIO(body))
    except zipfile.BadZipFile:
        raise RuntimeError(
            "the ECFIN release is not a readable zip (BadZipFile)")
    members = [n for n in outer.namelist() if n.endswith(".xlsx")]
    if len(members) != 1:
        raise RuntimeError(
            f"the ECFIN release holds {len(members)} workbooks "
            f"({outer.namelist()!r}); expected exactly one")
    try:
        return zipfile.ZipFile(io.BytesIO(outer.read(members[0])))
    except zipfile.BadZipFile:
        raise RuntimeError(
            f"{members[0]} inside the ECFIN release is not a readable xlsx")


def _shared_strings(z):
    try:
        raw = z.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(raw)
    return ["".join(t.text or "" for t in si.iter(_NS + "t"))
            for si in root.findall(_NS + "si")]


def _sheet_member(z, name=SHEET):
    """The zip member holding the named sheet, resolved via workbook.xml."""
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    targets = {r.get("Id"): r.get("Target") for r in rels}
    names = []
    sheets = wb.find(_NS + "sheets")
    for sh in (sheets if sheets is not None else []):
        names.append(sh.get("name"))
        if sh.get("name") == name:
            target = targets[sh.get(_RID)]
            return "xl/" + target.lstrip("/").removeprefix("xl/")
    raise RuntimeError(
        f"no sheet named {name!r} in the ECFIN workbook; it has: "
        + ", ".join(repr(n) for n in names))


def _cells(row, strings):
    out = {}
    for c in row.findall(_NS + "c"):
        ref = c.get("r")
        m = _CELL.match(ref or "")
        if not m:
            raise RuntimeError(
                f"unreadable cell address {ref!r} in the ECFIN workbook")
        v = c.find(_NS + "v")
        if v is None or v.text is None:
            continue
        out[m.group(1)] = strings[int(v.text)] if c.get("t") == "s" else v.text
    return out


def _month(serial):
    """An Excel date serial -> 'YYYY-MM-01'. Rows are month-end dates."""
    try:
        d = _EXCEL_EPOCH + timedelta(days=int(float(serial)))
    except (TypeError, ValueError, OverflowError):
        raise RuntimeError(
            f"the ECFIN date cell holds {serial!r}, not an Excel date")
    return f"{d.year:04d}-{d.month:02d}-01"


def parse(body, vintage=None):
    """Release bytes -> {geo: [{date, value}]} oldest first, for every
    `<GEO>.CONS` column the MONTHLY sheet carries.

    `vintage`, when given, is checked against the release itself: both
    aggregates must end on that reference month. Every check raises.
    """
    z = _workbook(body)
    strings = _shared_strings(z)
    sheet = ET.fromstring(z.read(_sheet_member(z)))
    data = sheet.find(_NS + "sheetData")
    if data is None:
        raise RuntimeError(f"sheet {SHEET!r} has no sheetData element")

    cols = None
    out = {}
    for row in data.findall(_NS + "row"):
        cells = _cells(row, strings)
        if cols is None:
            hit = {}
            for col, val in cells.items():
                m = _HEADER.match(val.strip()) if isinstance(val, str) else None
                if m:
                    if m.group(1) in hit.values():
                        raise RuntimeError(
                            f"the ECFIN header names {val!r} twice")
                    hit[col] = m.group(1)
            if hit:
                missing = sorted(set(REQUIRED) - set(hit.values()))
                if missing:
                    raise RuntimeError(
                        f"the ECFIN header row has no {INDICATOR} column for "
                        f"{missing}; the workbook layout has changed")
                cols = hit
            continue
        if "A" not in cells:
            continue
        month = _month(cells["A"])
        for col, geo in cols.items():
            raw = cells.get(col)
            if raw is None or raw.strip() == "NA":
                continue          # a country not surveyed that month
            try:
                value = float(raw)
            except ValueError:
                raise RuntimeError(
                    f"{geo}.{INDICATOR} {month}: {raw!r} is not a number")
            if not MIN_VALUE < value < MAX_VALUE:
                raise RuntimeError(
                    f"{geo}.{INDICATOR} {month}: {value} is not a balance "
                    f"(expected strictly between {MIN_VALUE} and {MAX_VALUE})")
            out.setdefault(geo, []).append(
                {"date": month, "value": round(value, 1)})
    if cols is None:
        raise RuntimeError(
            f"the ECFIN MONTHLY sheet never named its {INDICATOR} columns")

    for geo, rows in out.items():
        dates = [r["date"] for r in rows]
        if dates != sorted(set(dates)):
            raise RuntimeError(
                f"{geo}.{INDICATOR} is not one row per month, oldest first")
    for geo in ANCHORS:
        rows = out.get(geo) or []
        if not rows or rows[0]["date"] != FIRST_MONTH:
            raise RuntimeError(
                f"the ECFIN {geo} history starts at "
                f"{rows[0]['date'] if rows else None}, not {FIRST_MONTH}; the "
                "workbook was truncated or the wrong sheet was read")
        for prev, cur in zip(rows, rows[1:]):
            if _month_index(cur["date"]) != _month_index(prev["date"]) + 1:
                raise RuntimeError(
                    f"the ECFIN {geo} history jumps from {prev['date']} to "
                    f"{cur['date']}; the aggregates are published every month")
        if vintage is not None and rows[-1]["date"] != month_of(vintage):
            raise RuntimeError(
                f"release {vintage} ends its {geo} row at {rows[-1]['date']}, "
                f"not {month_of(vintage)}; this is not that month's release")
    missing = sorted(set(REQUIRED) - set(out))
    if missing:
        raise RuntimeError(f"the ECFIN release has no values for {missing}")
    return out


def _month_index(iso):
    return int(iso[:4]) * 12 + int(iso[5:7])


# --- the archive -------------------------------------------------------------
#
# `sources/ecbcs/<YYMM>.zip`, committed. Write-once per release: the first
# capture is the first print, and nothing later can change it.


def _utc_today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def archive_path(vintage):
    if not _VINTAGE.match(vintage or ""):
        raise ValueError(f"{vintage!r} is not a YYMM release id")
    return os.path.join(ARCHIVE, f"{vintage}.zip")


def archive(body, vintage):
    """File one release under its own id. An existing file is never touched."""
    if not isinstance(body, (bytes, bytearray)):
        raise TypeError("archive takes the release body as bytes")
    path = archive_path(vintage)
    if os.path.exists(path):
        return path
    os.makedirs(ARCHIVE, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(bytes(body))
    os.replace(tmp, path)
    return path


def archived_vintages():
    """Releases held, oldest first, as 'YYMM' strings."""
    if not os.path.isdir(ARCHIVE):
        return []
    return sorted(fn[:4] for fn in os.listdir(ARCHIVE)
                  if fn.endswith(".zip") and len(fn) == 8
                  and _VINTAGE.match(fn[:4]))


def load(vintage=None):
    """One archived release, parsed (default: the newest)."""
    held = archived_vintages()
    vintage = vintage or (held[-1] if held else None)
    if vintage is None:
        raise RuntimeError(
            "no ECFIN release has been archived under "
            f"{os.path.relpath(ARCHIVE, ROOT)}; ecbcs.history() fetches and "
            "files one")
    with open(archive_path(vintage), "rb") as f:
        return parse(f.read(), vintage=vintage)


def first_print(geo, month_iso):
    """The value `geo` carried for `month_iso` in that month's own release.

    This is the resolution value: the release named by the reference month,
    read from the archive, never from a later vintage. None when the release
    is not archived or did not carry the country that month.
    """
    vintage = vintage_of(month_iso)
    if vintage not in archived_vintages():
        return None
    for r in load(vintage).get(geo, []):
        if r["date"] == month_iso:
            return r["value"]
    return None


def _candidates(today):
    """The releases that could be current on `today`: this month, then last."""
    y, m = int(today[:4]), int(today[5:7])
    py, pm = (y, m - 1) if m > 1 else (y - 1, 12)
    return [f"{y % 100:02d}{m:02d}", f"{py % 100:02d}{pm:02d}"]


def history(fetch=True, today=None, diagnostics=None, fetcher=None):
    """{geo: [{date, value}]} from the newest archived release.

    With fetch=True, a release that could be current and is not yet archived
    is requested, validated by a full parse, and filed before it is served. A
    month whose release is already held costs no request. A transport failure
    serves the archive with a diagnostic; a release that arrived but does not
    parse fails the refresh.
    """
    today = today or _utc_today()
    fetcher = fetcher or fetch_bytes
    live_error = None
    if fetch:
        held = set(archived_vintages())
        for vintage in _candidates(today):
            if vintage in held:
                break
            try:
                body = fetcher(vintage)
            except NotPublished:
                continue
            except Exception as e:                              # noqa: BLE001
                if not _is_live_failure(e) or not held:
                    raise
                live_error = e
                print(f"  ecbcs: fetch of {vintage} failed "
                      f"({type(e).__name__}: {str(e)[:120]}); serving the "
                      "committed archive")
                break
            parse(body, vintage=vintage)
            archive(body, vintage)
            break
    rows = load()
    newest = rows["EU"][-1]["date"]
    stale = _month_index(today) - _month_index(newest)
    if stale > MAX_STALE_MONTHS:
        raise RuntimeError(
            f"the newest ECFIN release is for {newest}, {stale} months behind "
            f"today, over the {MAX_STALE_MONTHS} allowed; a silently stale "
            "source is worse than no source")
    if live_error is not None and diagnostics is not None:
        diagnostics.append({
            "source": "ecbcs",
            "scope": "live",
            "error": live_error,
            "archive_evidence": (
                f"{os.path.relpath(archive_path(vintage_of(newest)), ROOT)} "
                f"(newest reference month {newest})"),
        })
    return rows


def to_series(rows, geo):
    """One geography's consumer confidence as [{date, value}], oldest first."""
    if rows is None:
        rows = load()
    if geo not in rows:
        raise RuntimeError(f"the ECFIN release carries no {geo}.{INDICATOR}")
    return [dict(r) for r in rows[geo]]
