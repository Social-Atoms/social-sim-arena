"""Michigan consumer sentiment split by political party, out of the addenda PDF.

  GET https://data.sca.isr.umich.edu/fetchdoc.php?docid=81624
      -> demopoliticalparty202608p.pdf, "Tables Addenda of Political Party
         Variable", 5 pages, ~130 KB

**This series was written off, and the verdict was wrong.** `docs/sources.md` §6
recorded it as not viable on 2026-08-17: the subset tool has no party field, the
demographic tables have no party page, and the only party artifact that is not
two releases stale is a PDF. That last sentence was read as the end of the
inquiry rather than the start of one. `pdftotext -layout` -- poppler, a system
package on every runner, not a Python dependency -- turns this particular PDF
into a fixed-column table that a nine-float regex parses in full.

**What the table is.** Three index groups across the page -- Index of Consumer
Sentiment, Current Economic Conditions, Index of Consumer Expectations -- each
split three ways (Dem, Ind, Rep). One row per surveyed month, "MonthName YYYY"
then nine values. June 1980 to August 2026: 156 rows, of which 115 are an
unbroken monthly run from February 2017 and the other 41 are the sporadic
1980-2016 era, where the survey asked the party question in fifteen separate
stretches. The PDF's own footer says gaps are months in which the question was
not asked, so a gap month stays a gap here rather than being interpolated.

**Rows are read line by line, and the reason is three missing months.** The
probe that opened this route scanned the converted text with one MULTILINE
regex and found 153 rows, concluding the modern run had two holes in it --
November 2019 and June 2023. It has none. `pdftotext` separates pages with a
form feed, `^` under `re.MULTILINE` matches after a newline and not after a
`\f`, and the three rows that happen to sit at the top of a page -- October
2012, November 2019, June 2023 -- were therefore invisible to it. A whole-text
scan drops what it cannot see without saying so; splitting on lines first (which
`str.splitlines` does across form feeds) and then insisting that every line
opening like a data row *parses* as one turns the same event into an exception.
That distinction is why `CANDIDATE` exists separately from `ROW` below.

**Why the arena wants it.** The partisan gap is the structure a persistence null
cannot hold and a simulated society should: it inverts at presidential
transitions, on a calendar known years ahead. October 2016 read Dem 102.1 / Rep
74.4; February 2017, Dem 77.5 / Rep 115.7. October 2024 read Dem 91.4 / Rep
53.6; December 2024, Dem 69.6 / Rep 85.4. Forty points, swapped inside two
months, twice. The level gap is standing at about forty points as of August 2026
(Dem 39.1, Rep 78.7), so the three series are also nowhere near collinear.

**Recurrence is unproven, and the code is built for its absence.** August 2026
is the *first* month this addenda has ever been published -- `reports.php?year=
2025` lists no addenda at all -- so one appearance is a habit of size one, not a
schedule. Two consequences, both deliberate:

- **The archive is the source of truth, not the network.** `load()` parses the
  newest PDF under `sources/umichparty/`, and nothing here fetches on its own.
  A refresh therefore cannot be broken by the document failing to appear, and
  the committed vintage is what every resolution is rechecked against.
- **Fetching is a separate, asked-for act.** `fetch_latest()` exists, tolerates
  a document that is not there yet by returning None, and refuses to archive a
  body that is not a PDF. It also cannot find next month's file on its own: the
  URL is a docid, September's docid is not derivable from August's, and docids
  81618-81631 were scanned by hand to establish that no CSV or XLS twin exists.
  Until the publication recurs often enough to show a pattern, pointing this at
  a new month is a maintainer passing `docid=`. That is stated here rather than
  hidden behind a guess, because a scraper presented as sturdier than it is
  fails in the quietest possible way, mid-season.

**The as-of date comes from inside the document.** The last page carries a bare
`8/14/2026` -- the preliminary's release day -- and that, never the wall clock,
names the archived file and anchors the staleness check. A PDF re-downloaded in
November is still the August vintage, and dating it by the download would make a
three-month-old table look fresh, which is precisely the failure that
mis-resolved `umich-2026-08-prelim` (see `ssa/adapters/umich.py`).

**The newest row is provisional.** The addenda ships with the *preliminary*
national release and its last row is the preliminary reading, revised when the
final lands at the end of the month. That is documentation, not code: rounds
resolve against the frozen lock snapshot and `resolve.candidate` keys releases
on (date, value), so the preliminary and the final of one month are two releases
and each can settle its own round -- exactly as the national series already
does. Nothing here needs to choose between them.

**pdftotext is shelled out to, and its absence is loud.** Same contract as
`ssa/stamps.py` and `ots`: a missing binary raises with the package name in the
message. Silently skipping the series would publish a party question backed by
nothing, and the one thing worse than an outage here is an outage that looks
like data.
"""
import os
import re
import shutil
import subprocess

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARCHIVE = os.path.join(ROOT, "sources", "umichparty")

BINARY = "pdftotext"
# The docid of the August 2026 addenda, which is the only one that has ever
# existed. Not a base to increment from: see the module docstring.
DOCID = 81624
BASE = "https://data.sca.isr.umich.edu/fetchdoc.php"
UA = "social-simulation-arena/1.0 (research benchmark; contact via repository)"
TIMEOUT = 60
# Seconds for pdftotext itself. A 5-page, 130 KB document converts in well under
# one; anything near this bound means the binary is wedged, not busy.
CONVERT_TIMEOUT = 60

PARTIES = ("dem", "ind", "rep")
# The three index groups, in the left-to-right order the page prints them.
GROUPS = ("ics", "conditions", "expectations")

MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}

# Any line that opens with a month name and a year is a data row and must parse
# as one. Detecting the shape separately from parsing it is the point: a regex
# that only ever *matches* turns a row it cannot read into a row that was never
# there, and a table silently one row short is the failure this whole module is
# guarding against. It already cost three months once -- see the docstring.
#
# `\f` is in the leading class as well as `[ \t]`. `str.splitlines` does break
# on form feeds, so a page's first row arrives clean today; allowing it costs
# nothing and closes the door on the exact character that hid those three rows.
CANDIDATE = re.compile(
    r"^[ \t\f]*(" + "|".join(MONTHS) + r")[ \t]+(\d{4})\b")

# Month, year, an optional preliminary marker, then nine fixed-column floats.
# The August 2026 document does *not* mark its newest row `(P)` even though it
# is a preliminary reading, so the marker is optional and is recorded when
# present rather than relied upon.
#
# Anchored at both ends on purpose. Without the `$` a row carrying a tenth
# number matches its first nine and the extra column is dropped without a word.
ROW = re.compile(
    r"^[ \t\f]*(" + "|".join(MONTHS) + r")[ \t]+(\d{4})[ \t]*(\(P\))?[ \t]+"
    + r"[ \t]+".join([r"(-?\d+\.\d)"] * 9) + r"[ \t]*$")

# The release stamp, printed bare on the last page: `8/14/2026`. Matched per
# line rather than with re.MULTILINE, for the form-feed reason above -- the
# stamp sits on the last page, which is exactly where a page break is.
STAMP = re.compile(r"^[ \t\f]*(\d{1,2})/(\d{1,2})/(\d{4})[ \t]*$")

# 156 rows today and the history only grows. A table that suddenly parses to
# thirty rows is a layout change, not a revision.
MIN_ROWS = 100
# Index points on the 1966 = 100 base. The observed range is 27.9 to 133.9, so
# these bounds are loose enough to survive a decade and tight enough that a
# column read out of the wrong place -- a year, a page number, a footnote
# marker -- cannot pass for a reading.
MIN_VALUE, MAX_VALUE = 0.0, 200.0
# How far the newest row may trail the document's own stamp. The addenda ships
# with the preliminary, so the gap is normally zero; two months of slack covers
# a month the party question is not asked without covering a document that has
# quietly stopped being updated.
MAX_STALE_MONTHS = 2


def doc_url(docid=DOCID):
    return f"{BASE}?docid={docid}"


def have_converter():
    return shutil.which(BINARY) is not None


def to_text(pdf):
    """PDF bytes -> the layout-preserving text `parse` reads.

    `-layout` is load-bearing: without it poppler emits the nine columns in
    reading order with no column structure, and the row regex has nothing to
    anchor on.
    """
    if not have_converter():
        raise RuntimeError(
            f"{BINARY} is not installed, and the Michigan party series is a PDF "
            "with no CSV or XLS twin (docs/sources.md section 6). Install "
            "poppler: `sudo apt-get install -y poppler-utils` on Debian and "
            "Ubuntu, `brew install poppler` on macOS.")
    if not isinstance(pdf, (bytes, bytearray)):
        raise TypeError("to_text takes the PDF body as bytes")
    try:
        p = subprocess.run([BINARY, "-layout", "-", "-"], input=bytes(pdf),
                           capture_output=True, timeout=CONVERT_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(f"{BINARY} failed to run: {type(e).__name__}: {e}")
    if p.returncode != 0:
        detail = (p.stderr or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(f"{BINARY} exited {p.returncode}: {detail[:200]}")
    text = p.stdout.decode("utf-8", "replace")
    if not text.strip():
        raise RuntimeError(
            f"{BINARY} produced no text from a {len(pdf)}-byte document; the "
            "addenda may have been replaced by a scan")
    return text


def stamp_date(text):
    """The document's own release date, as `YYYY-MM-DD`.

    The last such line wins: the table body carries no bare dates, but a future
    edition adding a header stamp must not outrank the footer one.
    """
    hits = [m.groups() for m in
            (STAMP.match(line) for line in text.splitlines()) if m]
    if not hits:
        raise RuntimeError(
            "no release stamp (a bare `M/D/YYYY` line) in the addenda; refusing "
            "to date this table by the wall clock -- a re-download of an old "
            "vintage would then look like a fresh release")
    month, day, year = hits[-1]
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def _month_index(iso):
    return int(iso[:4]) * 12 + int(iso[5:7])


def parse(text, min_rows=MIN_ROWS):
    """Converted addenda text -> one dict per surveyed month, oldest first.

        {"date": "2026-08-01", "preliminary": False,
         "ics":          {"dem": 39.1, "ind": 48.5, "rep": 78.7},
         "conditions":   {"dem": 41.1, "ind": 50.3, "rep": 75.1},
         "expectations": {"dem": 37.9, "ind": 47.4, "rep": 81.0}}

    Every check here raises. There is no partial reading of this table worth
    publishing: an unparsed row is a month that silently never happened, and a
    value read out of the wrong column is a number nothing downstream can tell
    from a real one.

    `min_rows` is a parameter only so a fixture can exercise the parser without
    carrying forty-six years of history; every caller in the pipeline takes the
    default.
    """
    rows = []
    seen = set()
    for line in text.splitlines():
        if not CANDIDATE.match(line):
            continue
        m = ROW.match(line)
        if not m:
            raise RuntimeError(
                "a line opens like a data row and does not parse as one -- the "
                f"addenda's layout has changed: {line.strip()!r}")
        month, year, prelim = m.group(1), m.group(2), m.group(3)
        values = [float(v) for v in m.groups()[3:]]
        for v in values:
            if not MIN_VALUE < v < MAX_VALUE:
                raise RuntimeError(
                    f"{month} {year}: {v} is not a sentiment index reading "
                    f"(expected strictly between {MIN_VALUE} and {MAX_VALUE}); "
                    "a column was read out of the wrong place")
        date = f"{int(year):04d}-{MONTHS[month]:02d}-01"
        if date in seen:
            raise RuntimeError(f"{date} appears twice in the addenda")
        seen.add(date)
        row = {"date": date, "preliminary": bool(prelim)}
        for i, group in enumerate(GROUPS):
            row[group] = dict(zip(PARTIES, values[3 * i:3 * i + 3]))
        rows.append(row)

    if len(rows) < min_rows:
        raise RuntimeError(
            f"the addenda parsed to {len(rows)} rows, fewer than the {min_rows} "
            "this table has carried since 1980; refusing to publish a series "
            "built from a table that stopped being read")
    rows.sort(key=lambda r: r["date"])

    stamp = stamp_date(text)
    drift = _month_index(stamp) - _month_index(rows[-1]["date"])
    if abs(drift) > MAX_STALE_MONTHS:
        raise RuntimeError(
            f"the addenda is stamped {stamp} but its newest row is "
            f"{rows[-1]['date']} -- {abs(drift)} months apart, over the "
            f"{MAX_STALE_MONTHS} allowed. A source that is quietly months "
            "behind is worse than no source: see ssa/adapters/umich.py.")
    return rows


# --- the archive -------------------------------------------------------------
#
# `sources/umichparty/<stamp date>.pdf`, committed, and named by the date the
# document prints on itself rather than the day it was downloaded.


def archive_path(day):
    return os.path.join(ARCHIVE, f"{day}.pdf")


def archived_days():
    """Vintages held, oldest first, as `YYYY-MM-DD` strings."""
    if not os.path.isdir(ARCHIVE):
        return []
    return sorted(fn[:-4] for fn in os.listdir(ARCHIVE)
                  if fn.endswith(".pdf") and len(fn) == 14)


def newest_archived():
    days = archived_days()
    return archive_path(days[-1]) if days else None


def read_archived(path=None):
    """The newest archived PDF as bytes, with its path."""
    path = path or newest_archived()
    if not path:
        raise RuntimeError(
            "no Michigan party addenda has been archived under "
            f"{os.path.relpath(ARCHIVE, ROOT)}; the parser reads the archive "
            "and never the network, so a vintage has to be fetched and "
            "committed first (umichparty.fetch_latest)")
    with open(path, "rb") as f:
        return f.read(), path


def load(path=None, min_rows=MIN_ROWS):
    """The newest archived vintage, parsed. The pipeline's entry point."""
    body, _ = read_archived(path)
    return parse(to_text(body), min_rows=min_rows)


def fetch_latest(docid=DOCID, timeout=TIMEOUT, url=None):
    """Download one addenda and archive it under its own stamp date.

    Returns the archived path, or None when the document is not there -- which
    is the expected answer for most docids and, until the publication recurs, a
    possible answer for a month that never gets one.

    A response that *is* served but is not a PDF is a hard error rather than a
    None: `data.sca.isr.umich.edu` answers 200 with its homepage for paths that
    do not exist, so "not a PDF" here means an HTML page wearing a 200, and
    treating that as "no document this month" would let the series go dark
    without a word.
    """
    r = requests.get(url or doc_url(docid), headers={"User-Agent": UA},
                     timeout=timeout)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    body = r.content
    if body[:4] != b"%PDF":
        raise RuntimeError(
            f"{url or doc_url(docid)} answered {len(body)} bytes that are not a "
            f"PDF (starts {body[:16]!r}); this host serves its homepage with a "
            "200 for anything it does not have, so this is a dead link, not a "
            "month without a release")
    # Converted and validated *before* it is filed: an archive is only worth
    # committing if what it holds is the table this module can still read.
    text = to_text(body)
    rows = parse(text)
    day = stamp_date(text)
    os.makedirs(ARCHIVE, exist_ok=True)
    path = archive_path(day)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(body)
    os.replace(tmp, path)
    print(f"  umichparty  {len(body):>9,}B  stamped {day}  "
          f"{len(rows)} rows through {rows[-1]['date']}")
    return path


def to_series(rows, party, group="ics"):
    """One party's index as `[{date, value}]`, oldest first.

    `rows` may be None, in which case the newest archived vintage is parsed.
    Months the party question was not asked stay absent -- they are gaps in the
    survey, not gaps in our reading of it, and filling them would hand every
    baseline a reading nobody was ever given.
    """
    if party not in PARTIES:
        raise ValueError(f"unknown party {party!r}; expected one of {PARTIES}")
    if group not in GROUPS:
        raise ValueError(f"unknown index group {group!r}; expected one of {GROUPS}")
    if rows is None:
        rows = load()
    return [{"date": r["date"], "value": r[group][party]} for r in rows]
