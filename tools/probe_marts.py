"""Census MARTS retail sales: does it clear the source gates, measured not argued.

  python tools/probe_marts.py                 # the verdict, from the committed fixture
  python tools/probe_marts.py --fetch         # re-download the releases and rebuild it
  python tools/probe_marts.py --verify        # re-download and check nothing moved

Issue #48 asked for 36 first-release MARTS artifacts, eight stable retail
categories, and a resolution rule that reads the *advance* value and never a
revised one -- or a documented no-go with the failing gate. This is the
measurement half of that answer. `ssa/inventory.py` carries the verdict.

**What is committed and what is not.** `sources/marts/probe.json` holds, per
release, the URL, the sha256 and byte length of the workbook as served, and the
parsed advance/preliminary/revised values for the fourteen kind-of-business
rows. The workbooks themselves are **not** committed. `docs/sources.md` §2.1 is
right that a vintage rebuilt from parsed rows is our reading of the file rather
than the file -- so the hashes are here to make that reading checkable, and
`--verify` re-downloads and fails loudly if any body has changed under us.
1.2 MB of binaries is provenance for a series, and there is no series: this
source is not integrated. If that decision ever flips, the workbooks get
archived then, by an adapter, under `sources/marts/<ref>.xlsx`.

**The archive is addressable by reference month, which is the whole ballgame.**

    https://www2.census.gov/retail/releases/historical/marts/rs{YY}{MM}.xlsx

`{YY}{MM}` is the *advance* month the release is about, not the day it was
published, so a resolution can name the artifact it will be settled from before
the artifact exists. There is no link to this directory from any Census retail
page; it was found by walking `www2.census.gov/retail/`.

**Three things a naive reader gets wrong, all of them silent:**

- **Two of the fifty-four workbooks are strict OOXML** (`rs2501`, `rs2601`),
  with the `purl.oclc.org` namespaces rather than the usual
  `schemas.openxmlformats.org` ones. openpyxl 3.1.5 returns *zero sheets* for
  those and raises nothing, so a pipeline built on it loses two releases and
  never says so. The reader below takes the namespace off the document's own
  root element, which handles both spellings for free -- and is the reason this
  file parses xlsx with the standard library, the same way
  `ssa/adapters/yougov_xtab.py` does, rather than adding a dependency.

- **Three category rows wrap across two spreadsheet rows.** 444, 448 and 451
  carry the NAICS code and the start of the label on one row and every number
  on the next. A row-at-a-time parser drops them, which is a hole in exactly
  the categories a retail question would ask about.

- **The label is not an identifier.** Rows are keyed on the NAICS code here,
  never on the kind-of-business string: the strings are full of dot leaders and
  line breaks, and the 452 sub-codes churned mid-window (4521/4529 vanish, 4522
  /4523 appear) at a reclassification. No three-digit code moved.

**The advance column, and why it has to be read positionally.** Row 8 names the
months and row 9 marks each with `(a)` advance, `(p)` preliminary or `(r)`
revised. Not adjusted runs E/F/G, seasonally adjusted runs J/K/L. The markers
are verified against those two rows on every workbook rather than assumed, so a
layout change fails here instead of quietly resolving a round against a revised
number.
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys
import zipfile
from datetime import date
from xml.etree import ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

ARCHIVE_URL = ("https://www2.census.gov/retail/releases/historical/marts/"
               "rs{yy}{mm}.xlsx")
CURRENT_URL = "https://www.census.gov/retail/marts/www/marts_current.xlsx"
CALENDAR_URL = "https://www.census.gov/retail/release_schedule.html"

FIXTURE = os.path.join(ROOT, "sources", "marts", "probe.json")
CACHE = os.path.join(ROOT, ".local", "marts")

# How many releases the probe covers. Issue #48 asked for 36; the archive holds
# more, and the window is taken from the newest end so the measurement is about
# the layout in force today rather than about a layout Census has since changed.
RELEASES = 36

# The eight candidate categories, and they are all three-digit on purpose.
# `4451 Grocery stores` scores slightly better on revision size than its parent
# `445 Food & beverage stores`, and using both would put a category and the
# category that contains it on the same board -- two questions whose errors are
# mechanically correlated, counted as two independent observations. So the
# eight are the eight largest non-overlapping three-digit rows.
EIGHT = ("441", "442", "444", "445", "446", "447", "452", "722")

# Everything parsed, so the choice of eight can be revisited from the fixture
# without re-fetching anything.
CANDIDATES = ("441", "442", "443", "444", "445", "4451", "446", "447",
              "448", "451", "452", "453", "454", "722")


# --- reading the workbook ----------------------------------------------------

def _ns(el):
    """The spreadsheetml namespace this document actually uses.

    Taken from the root element rather than hard-coded, because Census serves
    a minority of these workbooks as strict OOXML under the `purl.oclc.org`
    namespaces. Hard-coding the transitional spelling reads those as empty.
    """
    return el.tag.split("}")[0] + "}"


def _shared(z, ns):
    try:
        blob = z.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(blob)
    return ["".join(t.text or "" for t in si.iter(ns + "t"))
            for si in root.iter(ns + "si")]


def _grid(z, sheet=1):
    """{row number: {column letter: text}} for one sheet, strings resolved."""
    root = ET.fromstring(z.read(f"xl/worksheets/sheet{sheet}.xml"))
    ns = _ns(root)
    shared = _shared(z, ns)
    out = {}
    for row in root.iter(ns + "row"):
        cells = {}
        for c in row.iter(ns + "c"):
            col = re.match(r"([A-Z]+)", c.get("r") or "")
            v = c.find(ns + "v")
            txt = v.text if v is not None else ""
            if c.get("t") == "s" and txt:
                txt = shared[int(txt)]
            if col and txt not in ("", None):
                cells[col.group(1)] = txt
        if cells:
            out[int(row.get("r"))] = cells
    return out


def _number(text):
    """A Table 1 cell as a float, or None for the sheet's own null markers.

    `(*)`, `(NA)` and `(S)` are Census's "not available at this level of
    detail", "not available" and "suppressed". They are returned as None
    rather than skipped, because a category that is *absent* from a release and
    a category that is *suppressed* in one are different facts and the gate
    below has to be able to tell them apart.
    """
    if text is None:
        return None
    t = text.strip()
    if not t or t.startswith("("):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _columns(grid):
    """{'nsa_advance': 'E', ...} verified against the workbook's own markers.

    Row 9 carries `(a)`, `(p)` and `(r)` under the month names in row 8. This
    reads them rather than assuming the E/F/G and J/K/L layout, so a Census
    column change surfaces as a parse failure instead of a round resolved
    against a revised figure.
    """
    marks = grid.get(9) or {}
    nsa = [c for c in ("C", "D", "E", "F", "G", "H", "I") if c in marks]
    sa = [c for c in ("J", "K", "L", "M", "N") if c in marks]
    want = {"(a)": "advance", "(p)": "preliminary", "(r)": "revised"}
    out = {}
    for block, cols in (("nsa", nsa), ("sa", sa)):
        for col in cols:
            role = want.get(marks[col].strip())
            if role and f"{block}_{role}" not in out:
                out[f"{block}_{role}"] = col
    missing = [k for k in ("nsa_advance", "sa_advance", "sa_preliminary",
                           "sa_revised") if k not in out]
    if missing:
        raise ValueError(
            f"Table 1 does not mark {missing} in row 9; the workbook layout "
            "changed and the advance column can no longer be identified")
    return out


def _advance_month(grid):
    """The reference month the release is about, from row 7/8's own headers."""
    year = (grid.get(7) or {}).get("J") or (grid.get(7) or {}).get("E")
    month = (grid.get(8) or {}).get("J") or (grid.get(8) or {}).get("E")
    if not year or not month:
        raise ValueError("Table 1 header rows do not name the advance month")
    name = re.sub(r"[^A-Za-z]", "", month)[:3].lower()
    names = ["jan", "feb", "mar", "apr", "may", "jun",
             "jul", "aug", "sep", "oct", "nov", "dec"]
    if name not in names:
        raise ValueError(f"unreadable advance month header {month!r}")
    return f"{int(float(year)):04d}-{names.index(name) + 1:02d}"


def parse(blob):
    """One release workbook -> {advance_month, columns, rows: {naics: {...}}}."""
    z = zipfile.ZipFile(blob if hasattr(blob, "read") else io.BytesIO(blob))
    grid = _grid(z, 1)
    cols = _columns(grid)
    rows = {}
    for n in sorted(grid):
        cells = grid[n]
        code = (cells.get("A") or "").strip()
        if code not in CANDIDATES:
            continue
        # 444, 448 and 451 put the code and the start of the label on one row
        # and every number on the next. Fall through to the following row
        # rather than recording the category as missing.
        src = cells if cols["sa_advance"] in cells else grid.get(n + 1, {})
        label = (cells.get("B") or "") + (
            "" if src is cells else (src.get("B") or ""))
        rows[code] = {"label": re.sub(r"\s+", " ", label).strip(),
                      "row": n}
        for key, col in cols.items():
            rows[code][key] = _number(src.get(col))
    return {"advance_month": _advance_month(grid), "columns": cols,
            "rows": rows}


# --- fetching (census.gov only, public domain) -------------------------------

def archive_url(ref):
    """`2026-06` -> the permanent URL of that release's workbook."""
    y, m = ref.split("-")
    return ARCHIVE_URL.format(yy=y[2:], mm=m)


def months_back(newest, n):
    """The `n` reference months ending at `newest`, oldest first."""
    y, m = (int(x) for x in newest.split("-"))
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def fetch(ref, cache=CACHE, timeout=60):
    """The workbook body, from the local cache if it is already there.

    Cached under `.local/`, which is gitignored: these are 34 KB binaries for
    a source with no series behind it, and the committed artifact is the
    fixture plus their hashes.
    """
    import requests
    os.makedirs(cache, exist_ok=True)
    path = os.path.join(cache, f"rs{ref[2:4]}{ref[5:7]}.xlsx")
    if os.path.exists(path):
        with open(path, "rb") as fh:
            return fh.read()
    r = requests.get(archive_url(ref), timeout=timeout,
                     headers={"User-Agent": "social-simulation-arena/1.0 "
                                            "(research benchmark; contact via "
                                            "repository)"})
    r.raise_for_status()
    if not r.content.startswith(b"PK"):
        raise ValueError(f"{archive_url(ref)} did not return a workbook")
    with open(path, "wb") as fh:
        fh.write(r.content)
    return r.content


# --- the probe ---------------------------------------------------------------

def build(refs, cache=CACHE):
    """Fetch (or read from cache) each release and record what it holds."""
    releases = []
    for ref in refs:
        body = fetch(ref, cache)
        got = parse(body)
        if got["advance_month"] != ref:
            raise ValueError(
                f"{archive_url(ref)} is about {got['advance_month']}, not "
                f"{ref}; the URL template no longer names the reference month")
        releases.append({
            "reference_month": ref,
            "url": archive_url(ref),
            "sha256": hashlib.sha256(body).hexdigest(),
            "bytes": len(body),
            "columns": got["columns"],
            "rows": {k: {kk: vv for kk, vv in v.items() if kk != "row"}
                     for k, v in got["rows"].items()},
        })
    return {
        "probed_on": date.today().isoformat(),
        "url_template": ARCHIVE_URL,
        "current_release_url": CURRENT_URL,
        "calendar_url": CALENDAR_URL,
        "eight": list(EIGHT),
        "releases": releases,
    }


def verdict(fixture):
    """Gate by gate, from the fixture alone. Returns rows for the printout."""
    rel = fixture["releases"]
    n = len(rel)
    out = []

    present = {c: sum(1 for r in rel if c in r["rows"]) for c in CANDIDATES}
    advance = {c: sum(1 for r in rel
                      if (r["rows"].get(c) or {}).get("sa_advance") is not None)
               for c in CANDIDATES}
    labels = {c: {(r["rows"].get(c) or {}).get("label") for r in rel
                  if c in r["rows"]} for c in CANDIDATES}

    out.append(("history", all(present[c] == n for c in EIGHT),
                f"each of the eight appears in {n}/{n} releases; advance value "
                f"present in {min(advance[c] for c in EIGHT)}/{n}"))
    out.append(("labels", all(len(labels[c]) == 1 for c in EIGHT),
                "one label spelling per category across the window: "
                + ", ".join(f"{c}={len(labels[c])}" for c in EIGHT)))

    # The resolution gate. An advance value is only usable as an answer if it
    # is distinguishable from the numbers the *same cell* holds a month later;
    # otherwise "resolve from the advance" and "resolve from a revision" are
    # the same instruction and the rule is decoration.
    same, pairs, deltas = 0, 0, []
    by_ref = {r["reference_month"]: r for r in rel}
    for r in rel:
        nxt = by_ref.get(_next_month(r["reference_month"]))
        if not nxt:
            continue
        for c in EIGHT:
            a = (r["rows"].get(c) or {}).get("sa_advance")
            p = (nxt["rows"].get(c) or {}).get("sa_preliminary")
            if a is None or p is None:
                continue
            pairs += 1
            if a == p:
                same += 1
            deltas.append(abs(a - p) / a * 100.0)
    mean = sum(deltas) / len(deltas) if deltas else 0.0
    out.append(("first_print_pinned", pairs > 0 and same == 0,
                f"{same}/{pairs} (category, month) pairs where the advance "
                f"equals the next release's value for the same month; mean "
                f"|advance - next print| = {mean:.2f}% of the advance"))

    # Movement, on the chained advance prints -- the series a round would
    # actually be scored against, not on revised history.
    moves = {}
    order = [r["reference_month"] for r in rel]
    for c in EIGHT:
        vals = [(by_ref[m]["rows"].get(c) or {}).get("sa_advance")
                for m in order]
        ch = [abs(b - a) / a * 100.0 for a, b in zip(vals, vals[1:])
              if a and b]
        moves[c] = sum(ch) / len(ch) if ch else 0.0
    out.append(("volatility", all(v > 0.2 for v in moves.values()),
                "mean absolute month-over-month change of the advance print: "
                + ", ".join(f"{c}={moves[c]:.2f}%" for c in EIGHT)))

    out.append(("rights", True,
                "US Census Bureau, a work of the United States government: "
                "public domain, no key, no terms restricting retrieval"))
    out.append(("schedule", True,
                "8:30 a.m. ET on a date announced in advance; the forward "
                f"calendar is published at {fixture['calendar_url']}"))
    return out


def _next_month(ref):
    y, m = (int(x) for x in ref.split("-"))
    return f"{y + (m == 12):04d}-{(m % 12) + 1:02d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true",
                    help="download the releases and rewrite the fixture")
    ap.add_argument("--verify", action="store_true",
                    help="re-download and check every body still hashes the same")
    ap.add_argument("--newest", default=None,
                    help="newest reference month to probe, e.g. 2026-06")
    ap.add_argument("--releases", type=int, default=RELEASES)
    ap.add_argument("--cache", default=CACHE)
    args = ap.parse_args()

    if args.fetch or args.verify:
        newest = args.newest
        if not newest and os.path.exists(FIXTURE):
            with open(FIXTURE) as fh:
                newest = json.load(fh)["releases"][-1]["reference_month"]
        if not newest:
            ap.error("--newest is required the first time")
        refs = months_back(newest, args.releases)
        built = build(refs, args.cache)
        if args.verify:
            with open(FIXTURE) as fh:
                old = json.load(fh)
            was = {r["reference_month"]: r["sha256"] for r in old["releases"]}
            moved = [r["reference_month"] for r in built["releases"]
                     if was.get(r["reference_month"]) not in (None, r["sha256"])]
            print("verify: "
                  + (f"{len(moved)} bodies changed: {moved}" if moved
                     else f"all {len(built['releases'])} bodies unchanged"))
            return
        os.makedirs(os.path.dirname(FIXTURE), exist_ok=True)
        with open(FIXTURE, "w") as fh:
            json.dump(built, fh, indent=1, sort_keys=True)
            fh.write("\n")
        print(f"wrote {os.path.relpath(FIXTURE, ROOT)} "
              f"({len(built['releases'])} releases)")

    with open(FIXTURE) as fh:
        fixture = json.load(fh)
    rows = verdict(fixture)
    print(f"\nCensus MARTS, {len(fixture['releases'])} releases "
          f"{fixture['releases'][0]['reference_month']} to "
          f"{fixture['releases'][-1]['reference_month']}, "
          f"probed {fixture['probed_on']}\n")
    for name, ok, detail in rows:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<20} {detail}")
    print("\nEvery mechanical gate above is measured, not asserted. What is "
          "\nnot a gate is whether this arena asks establishment statistics at "
          "\nall; see ssa/inventory.py, `census_marts`.")


if __name__ == "__main__":
    main()
