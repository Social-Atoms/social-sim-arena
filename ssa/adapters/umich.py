"""Michigan Surveys of Consumers, from the survey's own published tables.

**Two files, because the survey publishes twice a month and only one of them
carries the preliminary.**

  GET http://www.sca.isr.umich.edu/files/tbmics.csv    finals, back to 1952
  GET http://www.sca.isr.umich.edu/files/tbcics.csv    the press-release table

`tbmics.csv` is the long history: Month, YYYY, ICS_ALL, oldest first, one row a
month, and every row is a *final*. `tbcics.csv` is the table printed on the
release itself -- the last twelve months plus the current month marked `(P)`.
The preliminary lives only there. On 2026-08-16, two days after the August
preliminary was released, `tbmics.csv` still ended at July.

This cost the arena its first live resolution. `umich-2026-08-prelim` asked for
the August preliminary (51.0, published 2026-08-14) and was resolved against
July's final (55.2) -- a number that had been public since July, well before the
round's 2026-08-12 lock. Every entrant could have known it. The one claim this
whole repository rests on, that at lock time the answer does not exist, failed
on the very first round, and it failed silently.

**There is no FRED fallback and there must not be one.** FRED carries UMCSENT a
month behind at Michigan's request, which is not a labelling quirk: it removes
the most recent observation from everything the arena does. It is what produced
the failure above -- the 2026-08-12 lock snapshot froze a history ending in June
because the official table was briefly unreachable and the fallback answered
instead, so the round's baselines were anchored a month stale *and* "the next
release after the freeze" silently became July rather than August.

A source that is quietly a month behind is worse than no source at all, because
nothing downstream can tell. If the official tables are unreachable, this module
raises and the run is loudly broken. See ssa/series.py.
"""
import csv
import io

import requests

URL = "http://www.sca.isr.umich.edu/files/tbmics.csv"
PRELIM_URL = "http://www.sca.isr.umich.edu/files/tbcics.csv"

MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}


def parse(text):
    """CSV text -> [{date: 'YYYY-MM-01', value: float}], oldest first."""
    rows = []
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if len(row) < 3:
            continue
        month, year, value = (c.strip() for c in row[:3])
        if month not in MONTHS or not year.isdigit():
            continue          # header and any prose lines
        try:
            v = float(value)
        except ValueError:
            continue          # months with no reading yet
        rows.append({"date": f"{int(year):04d}-{MONTHS[month]:02d}-01",
                     "value": v})
    rows.sort(key=lambda r: r["date"])
    return rows


def fetch_text(timeout=30):
    """The table exactly as served, for ssa/provenance.py to archive.

    A vintage rebuilt from parsed rows is our reading of the file, not the
    file, and this source has already gone 404 mid-afternoon once.
    """
    r = requests.get(URL, timeout=timeout)
    r.raise_for_status()
    return r.text


def fetch_prelim_text(timeout=30):
    """The press-release table exactly as served."""
    r = requests.get(PRELIM_URL, timeout=timeout)
    r.raise_for_status()
    return r.text


def parse_prelim(text):
    """The press-release table -> [{date, value, preliminary}], oldest first.

    Rows read `August (P)` for a preliminary and `July` for a final, so the
    marker is on the month cell. Everything else in the file is layout: a
    title, blank lines, and a two-cell header.
    """
    rows = []
    for row in csv.reader(io.StringIO(text)):
        # The press-release table is laid out for print: data cells sit inside
        # padding columns, so position is meaningless and only the non-empty
        # cells are read. Taking row[:3] silently matched nothing.
        cells = [c.strip() for c in row if c.strip()]
        if len(cells) < 3:
            continue
        month, year, value = cells[:3]
        prelim = month.endswith("(P)")
        if prelim:
            month = month[:-3].strip()
        if month not in MONTHS or not year.isdigit():
            continue
        try:
            v = float(value)
        except ValueError:
            continue
        rows.append({"date": f"{int(year):04d}-{MONTHS[month]:02d}-01",
                     "value": v, "preliminary": prelim})
    rows.sort(key=lambda r: r["date"])
    return rows


def merge(finals, prelim):
    """The finals history with the current preliminary appended, if it is new.

    A preliminary is a *release*, and `ssa/resolve.py` keys the next release on
    (date, value) rather than date alone precisely so that both readings of one
    monthly row can resolve their own round: the preliminary lands on the 14th
    and the final revises the same row on the 28th. So the preliminary belongs
    in the series the moment it is published -- leaving it out is what made a
    preliminary round resolve against the previous month's final.

    Only ever appended, never used to overwrite a final: once a month has a
    final, that is the number, and a stale press-release table must not undo it.
    """
    out = list(finals)
    have = {p["date"] for p in out}
    for p in prelim:
        if p.get("preliminary") and p["date"] not in have:
            out.append({"date": p["date"], "value": p["value"]})
    out.sort(key=lambda r: r["date"])
    return out


def umich_sentiment(timeout=30):
    rows = merge(parse(fetch_text(timeout)),
                 parse_prelim(fetch_prelim_text(timeout)))
    if not rows:
        raise RuntimeError(
            "Michigan table parsed to zero rows; refusing to publish an empty "
            "series (check whether the file moved)")
    return rows
