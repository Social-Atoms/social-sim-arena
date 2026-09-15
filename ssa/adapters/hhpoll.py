"""Harvard-Harris monthly poll: Trump job approval, out of the topline PDF.

  https://harvardharrispoll.com/all-polls/  ->  one page per poll  ->
      /assets/uploads/<yyyy>/<mm>/<name>.pdf   (Topline / KeyResults / Crosstabs)

**Two table contracts were verified.** The 2017-2020 first-term archive uses
question code ``M3`` and the exact wording "Do you approve or disapprove of
the job Donald Trump is doing as President of the United States?".  Those
tables publish approve and disapprove nets.  The 2025-present archive uses
``M3ALT`` and the reversed wording below; those tables also publish a separate
don't-know net.  All accepted PDFs are converted with ``pdftotext -layout``.
The current contract is:

- exactly one table whose title line is the question code **`M3ALT`** followed
  by the exact wording "Do you disapprove or approve of the job Donald J.
  Trump is doing as President of the United States?";
- under it, `Strongly/Somewhat Approve <count>` with the percent on the
  following `(Net) <pp>%` line, the same shape for Disapprove, then a
  `Don't Know/Not Sure` pair, `Unweighted Base` and `Weighted Base`;
- one `Fielding Period: <Month D - D, YYYY>` line repeated per page, always
  identical within a file;
- one bare `D Mon YYYY` production stamp repeated per page (16 Jul 2026,
  2 Jun 2026, 28 Apr 2026), always identical within a file.

The anchor is the question *code*, not the word "Approve": one table later the
same file prints `M3A_ISS ... Summary Of Strongly/Somewhat Approve` -- fifteen
issue-approval percentages in the identical two-line shape -- and any looser
anchor would read one of those and call it the topline.  The historical
backfill applies the same exact wording, national-base, net-sum and
count-over-weighted-base checks to every first-term document rather than
loosening the current parser until old files happen to fit.

**What no code can know: when the next poll comes.** Harvard-Harris announces
no release calendar, skips months without notice (no June 2026 poll exists),
and neither the poll page slug (`/crosstabs-july-2026/`, `/crosstabs-may-2/`)
nor the PDF name (`July2026_HHP_TOPLINE.pdf`, `HHP_May2026_Topline.pdf`,
`HHP_Apr2026_Crosstabs.pdf`) nor even the upload directory month follows a
derivable pattern.  The all-polls index *is* enumerable, however, so it is the
watcher's cursor: every refresh follows Topline pages above the newest catalog
entry, validates the linked PDF, and files it write-once.  A changed table or
broken index degrades visibly and serves the last validated archive.  A round
on this series must still set `release_estimated: true` and resolve on the next
published wave because discovery does not create a release calendar.

**Who was asked.** Every page says both halves: "HCAPS (Filtered on Registered
Voters) / Weighted To The U.S. General Adult Population". The scored number is
the approve net among those respondents as published; the registration filter
and the adult-population weighting are the pollster's design, stated here so
the prompt can state it too.

Each parsed record retains the **fielding end date**, the day the last
interview was taken ("2026-07-12" for July 10-12).  The scored series is dated
by the document's own production stamp -- when the number became knowable --
and the archive vintage uses that same stamp, never the download day (the
umich lesson: a PDF re-downloaded months later is still the old vintage).

**Every number is cross-checked before it leaves.** The three percentage nets
must sum to ~100 (Sigma is printed as 100%), and each net must agree with its
own count over the weighted base to within a rounding point. A percent read
out of the wrong line fails one of the two; a table restructured out from
under the parser fails both, loudly.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
from urllib.parse import urljoin

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARCHIVE = os.path.join(ROOT, "sources", "hhpoll")
CATALOG = os.path.join(ARCHIVE, "catalog.json")

PAGE_URL = "https://harvardharrispoll.com/all-polls/"
BINARY = "pdftotext"
CONVERT_TIMEOUT = 60
TIMEOUT = 90
# The site is ordinary WordPress today; the browser UA is cheap insurance
# against the WAF mood swings PDF hosts are prone to.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

CODE = "M3ALT"
QUESTION = ("Do you disapprove or approve of the job Donald J. Trump is "
            "doing as President of the United States?")
LEGACY_CODE = "M3"
LEGACY_QUESTION = ("Do you approve or disapprove of the job Donald Trump is "
                   "doing as President of the United States?")

MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}
MON3 = {m[:3]: i for m, i in MONTHS.items()}

# The question-code line: `M3ALT <wording>`, heavily indented by -layout.
ANCHOR = re.compile(rf"^[ \t\f]*{CODE}\b[ \t]*(.*\S)[ \t]*$")
LEGACY_ANCHOR = re.compile(
    rf"^[ \t\f]*{LEGACY_CODE}\b[ \t]*(.*\S)[ \t]*$")
# A `<label>  <count>` line and the `(Net)/bare <pp>%` line that follows it.
APPROVE = re.compile(r"^[ \t\f]*Strongly/Somewhat Approve[ \t]+([\d,]+)[ \t]*$")
DISAPPROVE = re.compile(
    r"^[ \t\f]*Strongly/Somewhat Disapprove[ \t]+([\d,]+)[ \t]*$")
DK = re.compile(r"^[ \t\f]*Don'?t Know/Not Sure[ \t]+([\d,]+)[ \t]*$")
NET = re.compile(r"^[ \t\f]*(?:\(Net\))?[ \t]*(\d{1,2})%[ \t]*$")
LEGACY_APPROVE = re.compile(
    r"^[ \t\f]*(?:Strongly/Somewhat Approve|Approve \(Net\))"
    r"[ \t]+([\d,]+)[ \t]*$")
LEGACY_DISAPPROVE = re.compile(
    r"^[ \t\f]*(?:Strongly/Somewhat Disapprove|Disapprove \(Net\))"
    r"[ \t]+([\d,]+)[ \t]*$")
LEGACY_NET = re.compile(
    r"^[ \t\f]*(?:\(Net\))?[ \t]*(\d{1,3})%[ \t]*$")
UNWEIGHTED = re.compile(r"^[ \t\f]*Unweighted Base[ \t]+([\d,]+)[ \t]*$")
WEIGHTED = re.compile(r"^[ \t\f]*Weighted Base[ \t]+([\d,]+)[ \t]*$")
# `Fielding Period: July 10 - 12, 2026` / `... June 28 - July 1, 2026`.
FIELDING = re.compile(r"^[ \t\f]*Fielding Period:[ \t]*(.+?)[ \t]*$")
FIELDING_SPAN = re.compile(
    r"^([A-Z][a-z]+)[ \t]+\d{1,2}[ \t]*-[ \t]*(?:([A-Z][a-z]+)[ \t]+)?"
    r"(\d{1,2}),[ \t]*(\d{4})$")
# The production stamp, a bare `16 Jul 2026` right-aligned on every page.
STAMP = re.compile(r"^[ \t\f]*(\d{1,2})[ \t]+([A-Z][a-z]{2})[ \t]+(\d{4})[ \t]*$")

# The archive index is the stable discovery surface. It links to a document
# page; that page in turn carries the actual PDF URL in `data-pdf`.  Neither
# slug nor PDF filename is predictable, which is why watching the index is the
# only safe automatic route.
ROW_LINK = re.compile(
    r'<a\s+class="row"\s+href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S)
TOPLINE_LABEL = re.compile(
    r'<span\s+class="rl">\s*Topline\s*</span>', re.I)
PDF_LINK = re.compile(r'data-pdf="([^"]+\.pdf)"', re.I)
WATCH_PAGE_LIMIT = 12

# How many lines below the anchor the whole table must sit. The real table
# spans ~38 lines; a table that wandered past this is a layout change.
WINDOW = 45
# A national topline runs 1,700-2,750 respondents; a three-digit base means
# the parser wandered into a subgroup, not that the poll got small.
MIN_BASE = 500
# The two self-checks: the three nets against Sigma's printed 100%, and each
# net against its own count over the weighted base. One rounding point each.
SUM_TOLERANCE = 2.0
NET_TOLERANCE = 1.0


def have_converter():
    return shutil.which(BINARY) is not None


def to_text(pdf):
    """PDF bytes -> the layout-preserving text `parse` reads."""
    if not have_converter():
        raise RuntimeError(
            f"{BINARY} is not installed, and the Harvard-Harris topline is a "
            "PDF with no CSV or XLS twin. Install poppler: `sudo apt-get "
            "install -y poppler-utils` on Debian and Ubuntu, `brew install "
            "poppler` on macOS.")
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
            "topline may have been replaced by a scan")
    return text


def _int(s):
    return int(s.replace(",", ""))


def _one(name, values):
    """Every page repeats the fielding line and the stamp; they must agree.

    Two different values inside one document mean two polls were stapled
    together, and reading either one silently would date half the file wrong.
    """
    distinct = sorted(set(values))
    if not distinct:
        raise RuntimeError(
            f"no {name} anywhere in the topline; refusing to date a poll by "
            "guesswork")
    if len(distinct) > 1:
        raise RuntimeError(
            f"the topline carries {len(distinct)} different {name}s "
            f"({distinct[:4]}); one document must mean one poll")
    return distinct[0]


def stamp_date(text):
    """The document's own production date, `YYYY-MM-DD`. Names the vintage."""
    hits = [m.groups() for m in
            (STAMP.match(line) for line in text.splitlines()) if m]
    day, mon, year = _one("production stamp (a bare `D Mon YYYY` line)",
                          [h for h in hits])
    if mon not in MON3:
        raise RuntimeError(f"unreadable month {mon!r} in the production stamp")
    return f"{int(year):04d}-{MON3[mon]:02d}-{int(day):02d}"


def fielding_end(text):
    """The last day interviews were taken, `YYYY-MM-DD`. Dates the row."""
    raw = _one("fielding period",
               [m.group(1) for m in
                (FIELDING.match(line) for line in text.splitlines()) if m])
    span = FIELDING_SPAN.match(raw)
    if not span:
        raise RuntimeError(
            f"unreadable fielding period {raw!r}; the known shapes are "
            "'July 10 - 12, 2026' and 'June 28 - July 1, 2026'")
    first, second, end_day, year = span.groups()
    month = second or first
    if month not in MONTHS:
        raise RuntimeError(f"unreadable month {month!r} in fielding period {raw!r}")
    return f"{int(year):04d}-{MONTHS[month]:02d}-{int(end_day):02d}"


def _net_after(lines, i, what):
    """The `(Net) NN%` (or bare `NN%`) on the next non-empty line."""
    for j in range(i + 1, min(i + 3, len(lines))):
        if not lines[j].strip():
            continue
        m = NET.match(lines[j])
        if m:
            return float(m.group(1))
        break
    raise RuntimeError(
        f"the {what} count on line {i + 1} is not followed by its percent; "
        "the table layout has changed")


def parse(text):
    """Converted topline text -> the one record the arena scores.

        {"date": "2026-07-12", "approve": 42.0, "disapprove": 54.0,
         "dk": 4.0, "unweighted_n": 1776, "stamp": "2026-07-16",
         "question": <the wording as printed>}

    Anchored on the question code M3ALT and on nothing looser: the same file
    prints fifteen issue-approval percentages under `M3A_ISS ... Summary Of
    Strongly/Somewhat Approve` in the identical two-line shape one table
    later. Everything raises; a topline read out of the wrong table is a
    number nothing downstream can tell from a real one.
    """
    lines = text.splitlines()
    anchors = [(i, m.group(1)) for i, m in
               ((i, ANCHOR.match(ln)) for i, ln in enumerate(lines)) if m]
    if not anchors:
        raise RuntimeError(
            f"no {CODE} table in the topline: either the poll dropped the "
            "presidential approval question or the question code changed")
    if len(anchors) > 1:
        raise RuntimeError(
            f"{len(anchors)} {CODE} tables in one topline; refusing to guess "
            "which one is the approval question")
    start, wording = anchors[0]
    if re.sub(r"\s+", " ", wording) != QUESTION:
        raise RuntimeError(
            f"the {CODE} wording changed: {wording!r}. A different question "
            "scored as the same series is the quietest possible failure, so "
            "the new wording has to be read and this constant updated on "
            "purpose.")

    window = lines[start:start + WINDOW]
    found = {}
    for key, pat in (("approve", APPROVE), ("disapprove", DISAPPROVE),
                     ("dk", DK)):
        hits = [(i, _int(m.group(1))) for i, m in
                ((i, pat.match(ln)) for i, ln in enumerate(window)) if m]
        if len(hits) != 1:
            raise RuntimeError(
                f"{len(hits)} {key!r} rows under the {CODE} anchor "
                f"(expected exactly 1); the table layout has changed")
        i, count = hits[0]
        found[key] = (count, _net_after(window, i, key))
    bases = {}
    for key, pat in (("unweighted", UNWEIGHTED), ("weighted", WEIGHTED)):
        hits = [_int(m.group(1)) for m in
                (pat.match(ln) for ln in window) if m]
        if len(hits) != 1:
            raise RuntimeError(
                f"{len(hits)} {key} base rows under the {CODE} anchor; the "
                "table layout has changed")
        bases[key] = hits[0]

    if bases["unweighted"] < MIN_BASE:
        raise RuntimeError(
            f"unweighted base {bases['unweighted']} is smaller than any "
            f"national Harvard-Harris wave ({MIN_BASE}+); the parser read a "
            "subgroup, not the topline")
    total = sum(net for _, net in found.values())
    if abs(total - 100.0) > SUM_TOLERANCE:
        raise RuntimeError(
            f"approve {found['approve'][1]} + disapprove "
            f"{found['disapprove'][1]} + don't-know {found['dk'][1]} = "
            f"{total}, not ~100; a percent was read off the wrong line")
    for key, (count, net) in found.items():
        implied = 100.0 * count / bases["weighted"]
        if abs(implied - net) > NET_TOLERANCE:
            raise RuntimeError(
                f"the {key} net is printed as {net}% but its own count "
                f"({count} of {bases['weighted']}) is {implied:.1f}%; one of "
                "the two was read out of the wrong table")

    return {"date": fielding_end(text),
            "approve": found["approve"][1],
            "disapprove": found["disapprove"][1],
            "dk": found["dk"][1],
            "unweighted_n": bases["unweighted"],
            "stamp": stamp_date(text),
            "question": re.sub(r"\s+", " ", wording)}


def parse_legacy(text):
    """Parse the separately verified 2017-2020 M3 approval table.

    First-term releases predate ``M3ALT`` and omit a separate don't-know row.
    They use either ``Strongly/Somewhat Approve`` or ``Approve (Net)`` for the
    two net rows.  The same count/base and 100-percent checks as the current
    parser keep this a strict source adapter rather than a PDF heuristic.
    """
    lines = text.splitlines()
    anchors = []
    for i, line in enumerate(lines):
        match = LEGACY_ANCHOR.match(line)
        if match and re.sub(r"\s+", " ", match.group(1)).strip() == LEGACY_QUESTION:
            anchors.append((i, LEGACY_QUESTION))
    if len(anchors) != 1:
        raise RuntimeError(
            f"found {len(anchors)} exact legacy {LEGACY_CODE} presidential-"
            "approval tables; expected 1")

    start, wording = anchors[0]
    end = min(start + WINDOW, len(lines))
    next_question = re.compile(r"^[ \t\f]*M\d+[A-Z_]*\b")
    for i in range(start + 1, end):
        if next_question.match(lines[i]):
            end = i
            break
    window = lines[start:end]
    found = {}
    for key, pattern in (("approve", LEGACY_APPROVE),
                         ("disapprove", LEGACY_DISAPPROVE)):
        hits = [(i, _int(match.group(1)))
                for i, line in enumerate(window)
                if (match := pattern.match(line))]
        if len(hits) != 1:
            raise RuntimeError(
                f"found {len(hits)} legacy {key} rows; expected 1")
        i, count = hits[0]
        found[key] = (count, _net_after_pattern(
            window, i, key, LEGACY_NET))

    bases = {}
    for key, pattern in (("unweighted", UNWEIGHTED),
                         ("weighted", WEIGHTED)):
        hits = [_int(match.group(1)) for line in window
                if (match := pattern.match(line))]
        if len(hits) != 1:
            raise RuntimeError(
                f"found {len(hits)} legacy {key} base rows; expected 1")
        bases[key] = hits[0]
    if min(bases.values()) < MIN_BASE:
        raise RuntimeError("legacy table base is below 500; likely a subgroup")

    total = sum(net for _count, net in found.values())
    if abs(total - 100.0) > SUM_TOLERANCE:
        raise RuntimeError(
            f"legacy approve and disapprove sum to {total}, not approximately 100")
    for key, (count, net) in found.items():
        implied = 100.0 * count / bases["weighted"]
        if abs(implied - net) > NET_TOLERANCE:
            raise RuntimeError(
                f"legacy {key} is {net}% but {count}/{bases['weighted']} "
                f"implies {implied:.1f}%")

    return {"date": fielding_end(text),
            "approve": found["approve"][1],
            "disapprove": found["disapprove"][1],
            "dk": 0.0,
            "unweighted_n": bases["unweighted"],
            "stamp": stamp_date(text),
            "question": wording}


def _net_after_pattern(lines, i, what, pattern):
    for line in lines[i + 1:i + 3]:
        if not line.strip():
            continue
        match = pattern.match(line)
        if match:
            return float(match.group(1))
        break
    raise RuntimeError(
        f"the {what} count on line {i + 1} is not followed by its percent; "
        "the table layout has changed")


def parse_any(text):
    """Parse either verified presidential-term table contract."""
    try:
        return parse(text)
    except RuntimeError as current_error:
        try:
            return parse_legacy(text)
        except RuntimeError as legacy_error:
            raise RuntimeError(
                "no supported Harvard-Harris presidential-approval table; "
                f"current parser: {current_error}; legacy parser: "
                f"{legacy_error}") from legacy_error


# --- the archive -------------------------------------------------------------
#
# `sources/hhpoll/<production stamp>.pdf`, committed, named by the date the
# document prints on itself rather than the day it was downloaded.


def archive_path(day):
    return os.path.join(ARCHIVE, f"{day}.pdf")


def archived_days():
    """Vintages held, oldest first, as `YYYY-MM-DD` strings."""
    if not os.path.isdir(ARCHIVE):
        return []
    return sorted(fn[:-4] for fn in os.listdir(ARCHIVE)
                  if fn.endswith(".pdf") and len(fn) == 14)


def archive(body, day):
    """File one topline under its stamp day, write-once. Returns the path.

    The same bytes again are a no-op; *different* bytes under a day already
    filed are an error, never an overwrite -- a vintage a resolution cites
    must not change under it, and two different documents claiming one stamp
    is a fact the maintainer needs to see, not a race to disk.
    """
    if not isinstance(body, (bytes, bytearray)):
        raise TypeError("archive takes the PDF body as bytes")
    path = archive_path(day)
    if os.path.exists(path):
        with open(path, "rb") as f:
            if f.read() == bytes(body):
                return path
        raise RuntimeError(
            f"{os.path.relpath(path, ROOT)} already holds a different "
            "document; refusing to overwrite an archived vintage")
    os.makedirs(ARCHIVE, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(bytes(body))
    os.replace(tmp, path)
    return path


def load():
    """Every archived vintage, parsed, oldest fielding date first."""
    days = archived_days()
    if not days:
        raise RuntimeError(
            "no Harvard-Harris topline has been archived under "
            f"{os.path.relpath(ARCHIVE, ROOT)}; the parser reads the archive "
            "and never the network, so a vintage has to be fetched and "
            "committed first (hhpoll.fetch, URL in hand)")
    records = []
    for day in days:
        with open(archive_path(day), "rb") as f:
            records.append(parse_any(to_text(f.read())))
    return to_records(records)


def archive_pages(index_html):
    """Topline document pages from the all-polls index, newest first."""
    pages = []
    for match in ROW_LINK.finditer(index_html):
        if not TOPLINE_LABEL.search(match.group(2)):
            continue
        url = urljoin(PAGE_URL, match.group(1))
        if url not in pages:
            pages.append(url)
    if not pages:
        raise RuntimeError("the Harvard-Harris archive index contains no Topline links")
    return pages


def document_url(page_html, page_url):
    """The one PDF linked by a Topline document page."""
    links = list(dict.fromkeys(
        urljoin(page_url, match.group(1)) for match in PDF_LINK.finditer(page_html)))
    if len(links) != 1:
        raise RuntimeError(
            f"{page_url} carries {len(links)} data-pdf links; expected exactly 1")
    return links[0]


def _catalog():
    if not os.path.exists(CATALOG):
        return []
    with open(CATALOG) as handle:
        rows = json.load(handle)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise RuntimeError("sources/hhpoll/catalog.json is not a list of records")
    return rows


def _write_catalog(rows):
    os.makedirs(ARCHIVE, exist_ok=True)
    tmp = CATALOG + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(rows, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, CATALOG)


def _get(url, timeout=TIMEOUT):
    response = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
    response.raise_for_status()
    return response.content


def fetch_index(timeout=TIMEOUT):
    """Fetch and minimally validate the publisher's discovery page."""
    body = _get(PAGE_URL, timeout)
    archive_pages(body.decode("utf-8", "replace"))
    return body


def update(timeout=TIMEOUT, page_limit=WATCH_PAGE_LIMIT, index_html=None):
    """Discover and archive new toplines; return newly filed records.

    The committed catalog is the cursor.  The all-polls page is newest-first,
    so a normal run follows only pages above the first already-known page.  A
    missing or changed approval table is an error: the watcher never labels a
    novel release irrelevant on its own.
    """
    rows = _catalog()
    known = {row.get("page_url") for row in rows}
    index = (fetch_index(timeout).decode("utf-8", "replace")
             if index_html is None else index_html)
    unseen = []
    for page_url in archive_pages(index):
        if page_url in known:
            break
        unseen.append(page_url)
        if len(unseen) > page_limit:
            raise RuntimeError(
                f"more than {page_limit} unseen topline pages precede the "
                "catalog cursor; run the explicit backfill before the watcher")

    added = []
    for page_url in reversed(unseen):
        page = _get(page_url, timeout).decode("utf-8", "replace")
        pdf_url = document_url(page, page_url)
        body = _get(pdf_url, timeout)
        if body[:4] != b"%PDF":
            raise RuntimeError(
                f"{pdf_url} answered {len(body)} bytes that are not a PDF")
        record = parse_any(to_text(body))
        path = archive(body, record["stamp"])
        rows.append({
            "approve": record["approve"],
            "bytes": len(body),
            "fielding_end": record["date"],
            "file": os.path.basename(path),
            "page_url": page_url,
            "pdf_url": pdf_url,
            "sha256": hashlib.sha256(body).hexdigest(),
            "stamp": record["stamp"],
        })
        _write_catalog(rows)
        added.append(record)
    return added


def to_records(records):
    """Validated, sorted poll records -- the shape `to_series` consumes.

    Split out of `load` so tests (and callers holding already-parsed rows)
    exercise the ordering and duplicate checks without touching disk or
    pdftotext.
    """
    records = sorted(records, key=lambda r: r["date"])
    for prev, cur in zip(records, records[1:]):
        if prev["date"] == cur["date"]:
            raise RuntimeError(
                f"two archived toplines claim the fielding date "
                f"{cur['date']}; the same poll was filed under two stamps")
    return records


def to_series(records=None):
    """Trump approve % as `[{date, value}]`, oldest first.

    `records` may be None, in which case every archived vintage is parsed
    (never the network -- fetching is a maintainer act, see `fetch`).
    Months Harvard-Harris skipped stay absent: they are polls that never
    happened, not gaps in our reading.

    Rows are dated by the production STAMP -- the day the topline became
    public -- not the fielding end the record keeps. Harvard-Harris
    publishes two to four days after fielding closes, and the arena keys
    everything on when a number could first have been seen: dated by
    fielding, a late-published wave would slide a pre-lock-dated point into
    history *after* the lock froze it, shifting the baselines under a round
    already bought against them, and resolution ("first point after the
    frozen history") would skip a wave the world only learned of post-lock.
    Dated by stamp, a point exists from the day it was knowable, and "the
    next wave published after the lock" is exactly what resolution finds.
    """
    if records is None:
        records = load()
    return [{"date": r["stamp"], "value": r["approve"]} for r in records]


def fetch(url, timeout=TIMEOUT):
    """Download one topline PDF and archive it under its own stamp date.

    `url` is always passed by a maintainer: Harvard-Harris publishes no
    calendar and no derivable URL pattern (see the module docstring), so
    there is deliberately no default and no way to point this at "the next
    poll". The document is converted and fully parsed *before* it is filed;
    an archive is only worth committing if it holds the table this module
    can still read.
    """
    body = _get(url, timeout)
    if body[:4] != b"%PDF":
        raise RuntimeError(
            f"{url} answered {len(body)} bytes that are not a PDF (starts "
            f"{body[:16]!r}); WordPress serves an HTML error page with a "
            "200, so this is a dead or moved link, not a poll")
    rec = parse_any(to_text(body))
    path = archive(body, rec["stamp"])
    print(f"  hhpoll  {len(body):>9,}B  stamped {rec['stamp']}  fielded "
          f"through {rec['date']}  approve {rec['approve']:.0f}%")
    return path
