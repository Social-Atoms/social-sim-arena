"""TSA checkpoint travel numbers (tsa.gov passenger volumes): weekly national total.

  GET https://www.tsa.gov/travel/passenger-volumes          (the current year)
  GET https://www.tsa.gov/travel/passenger-volumes/<YYYY>   (each earlier year)

Each page is one HTML table, header `Date | Numbers`, dates M/D/YYYY and counts
with thousands separators; the current page runs newest first and restarts at
1/1 every January, when the old year moves to its own page. The rules are the
approved Data Card `tsa-checkpoint-weekly@v2`
(docs/data-cards/tsa-checkpoint-weekly.json, sha256
5c69cafebad1610bb2c8aad0d58f706d28ad8b7cf623828debb916133d01c9f8); this module
implements them and nothing else.

**One capture a day, three pages.** The current page, then the two previous
year pages 10 s apart (the 364-day denominators and the 26-week spread search
reach back into them), with the arena's honest User-Agent, no browser
User-Agent, no headless browser and no retry loop (lessons 3 and 11). The
previous years are the two before the newest date on the current page, so a
capture on the first of January still asks for the pages that exist. Every
page is parsed before anything is written, and the capture is all or nothing:
`sources/tsa_volumes/<fetch day, UTC>/{current,<YYYY>,<YYYY>}.html`, the bodies
exactly as served, write-once per day, with each page's URL, sha256, size, row
count and newest date in `sources/tsa_volumes/fetches.json` and the current
page's provenance row in `sources/manifest.json`.

**A page that is not the table is a failed capture** (card missing-data policy
(2)): no table, another header, or fewer rows than the previous capture of the
same year -- a bot challenge or a layout change. Nothing is filed; the failure
is appended to `sources/tsa_volumes/failures.json` and raised, and the refresh
step is non-blocking. A 403 or 429 is raised as it came and never retried: the
card's fallback is a local courier running this same fetch from a maintainer's
Mac, then a person saving the three pages by hand (docs/sources/tsa.md).

**The page updates Monday to Friday by 9 a.m. ET** and not over the weekend,
so a Sunday-night lock sees through Thursday D-4. The refresh's first run of a
UTC day (00:17Z, the evening before in New York) is the day's capture.

**The reference** is `yoy_k` on the daily axis (ssa/baselines.py): R = the
days D-35 .. min(newest posted, D-4) / the same days 364 days earlier, times
last year's week D-364, spread = RMS of the same rule's errors (each past week
with its normal end W-4) over the 13 newest weeks complete in the lock
capture. A day is missing only if absent, empty or zero; no ratio outage rule.
"""
import json
import os
import time as _time
from datetime import date, datetime, time, timedelta, timezone
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

from .. import baselines
from .. import capture as archive_store
from .. import provenance
from .. import us_holidays

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NAME = "tsa_volumes"
ARCHIVE = os.path.join(ROOT, "sources", NAME)
FETCHES = os.path.join(ARCHIVE, "fetches.json")
FAILURES = os.path.join(ARCHIVE, "failures.json")

CARD = "tsa-checkpoint-weekly@v2"
CARD_SHA256 = "5c69cafebad1610bb2c8aad0d58f706d28ad8b7cf623828debb916133d01c9f8"
BASE = "https://www.tsa.gov/travel/passenger-volumes"
TZ = ZoneInfo("America/New_York")
HEADER = ["Date", "Numbers"]
PRIOR_YEARS = 2
PAUSE_SECONDS = 10
CURRENT = "current"

# The ranked reference (card baseline): yoy_k(start=D-35, end=min(newest
# posted, D-4), spread rms13 over 26 weeks with each past week's normal end).
# `max_lag` 34 lets the end move back as far as D-35 and no further: the card
# sets no bound, and a window ending before D-35 would be empty.
ZERO_POINT = "yoy_d35_d4"
YOY = {"name": ZERO_POINT, "family": "yoy_k", "period": "day", "k": 32,
       "lag": 3, "end": "newest", "max_lag": 34, "keep": "start",
       "target_len": 7,
       "spread": {"rule": "rms", "n": 13, "search": 26, "end": "nominal"}}
# Shown, not ranked: persistence on the settled week D-14.
PERSISTENCE_D14 = {
    "name": "persistence_d14", "family": "mean_k", "period": "week",
    "k": 1, "lag": 1, "search": 1,
    "spread": {"rule": "rms", "n": 13, "search": 26}}

# Card pilot gate (b)/(c): a week total that moved less than this between two
# captures is settled.
PILOT_MAX_CHANGE = 0.002

NotPostable = baselines.NotPostable


class NotTheTable(ValueError):
    """A body without the passenger-volumes table: a challenge page or a
    layout change. Recorded as a failed capture, never parsed further."""


# ---------------------------------------------------------------- the clock

def as_date(d):
    return d if isinstance(d, date) and not isinstance(d, datetime) \
        else date.fromisoformat(str(d)[:10])


def monday(d):
    d = as_date(d)
    if d.weekday() != 0:
        raise ValueError(f"{d} is not a Monday; weeks are named by their Monday")
    return d


def _et(day, hh, mm):
    return datetime.combine(day, time(hh, mm), TZ).astimezone(timezone.utc)


def lock_instant(week_start):
    """Sunday 23:59 ET immediately before the target week."""
    return _et(monday(week_start) - timedelta(days=1), 23, 59)


def lock_window(week_start):
    """Saturday 00:00 ET to the lock: where the lock capture must come from."""
    d = monday(week_start)
    return _et(d - timedelta(days=2), 0, 0), lock_instant(d)


def resolution_instant(week_start):
    """Tuesday D+8 12:00 ET, two days after the week ends."""
    return _et(monday(week_start) + timedelta(days=8), 12, 0)


def grace_end(week_start):
    """Friday D+11 12:00 ET: no complete capture by then and it is cancelled."""
    return _et(monday(week_start) + timedelta(days=11), 12, 0)


# ---------------------------------------------------------------- parsing

class _Table(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []            # each: list of rows, each a list of cells
        self._depth = 0
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._depth += 1
            if self._depth == 1:
                self.tables.append([])
        elif self._depth == 1 and tag == "tr":
            self._row = []
        elif self._depth == 1 and tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "table":
            self._depth = max(0, self._depth - 1)
        elif self._depth == 1 and tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif self._depth == 1 and tag == "tr" and self._row is not None:
            self.tables[-1].append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse_page(body):
    """One page's body -> {date: count}. Raises NotTheTable on anything but
    exactly one Date/Numbers table with at least one row, ValueError on a bad
    cell or a repeated date."""
    if isinstance(body, (bytes, bytearray)):
        body = bytes(body).decode("utf-8", errors="replace")
    p = _Table()
    p.feed(body)
    p.close()
    if len(p.tables) != 1:
        raise NotTheTable(f"{NAME}: {len(p.tables)} tables, expected one")
    rows = p.tables[0]
    if not rows or rows[0] != HEADER:
        raise NotTheTable(f"{NAME}: header {rows[0] if rows else None!r}, "
                          f"expected {HEADER!r}")
    out = {}
    for row in rows[1:]:
        if len(row) != 2:
            raise ValueError(f"{NAME}: row {row!r} is not (Date, Numbers)")
        try:
            day = datetime.strptime(row[0], "%m/%d/%Y").date()
        except ValueError:
            raise ValueError(f"{NAME}: date {row[0]!r} is not M/D/YYYY") from None
        if day in out:
            raise ValueError(f"{NAME}: {day} appears twice on one page")
        cell = row[1].replace(",", "").strip()
        if cell == "":
            out[day] = None
            continue
        if not cell.isdigit():
            raise ValueError(f"{NAME}: count {row[1]!r} on {day} is not a number")
        out[day] = int(cell)
    if not out:
        raise NotTheTable(f"{NAME}: the table has no rows")
    return out


def merge(pages):
    """{label: {date: count}} -> ({date: count}, conflicts).

    Card policy (5): a date on two pages with different numbers is logged and
    read from the current page. Absent, empty and zero are all "not posted"
    (policy (4)) and are left out.
    """
    out, conflicts = {}, []
    order = [k for k in pages if k != CURRENT] + ([CURRENT] if CURRENT in pages else [])
    for label in order:
        for d, v in pages[label].items():
            if d in out and v is not None and out[d] != v:
                conflicts.append({"date": d.isoformat(), "page": label,
                                  "was": out[d], "now": v})
            if v:
                out[d] = v
            elif label == CURRENT:
                out.pop(d, None)
    return out, conflicts


def week_days(week_start):
    m = monday(week_start)
    return [m + timedelta(days=i) for i in range(7)]


def week_total(daily, week_start):
    days = week_days(week_start)
    if any(d not in daily for d in days):
        return None
    return sum(daily[d] for d in days)


def weekly(daily):
    """{Monday: total} for every week with all seven dates posted."""
    if not daily:
        return {}
    m = min(daily)
    m += timedelta(days=(7 - m.weekday()) % 7)
    out = {}
    while m <= max(daily):
        t = week_total(daily, m)
        if t is not None:
            out[m] = t
        m += timedelta(days=7)
    return out


# ---------------------------------------------------------------- fetching

def page_url(year=None):
    return BASE if year is None else f"{BASE}/{int(year)}"


def fetch_page(url, timeout=archive_store.TIMEOUT, get=None):
    kw = {"get": get} if get else {}
    return archive_store.get(url, name=NAME, accept="text/html", timeout=timeout, **kw)


# ---------------------------------------------------------------- archive

def _rel(path):
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


def _load(path, empty):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return empty


def _save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def load_fetches():
    return _load(FETCHES, {})


def day_dir(day):
    return os.path.join(ARCHIVE, day)


def _year_of(parsed):
    return max(parsed).year


def _previous_rows(rows, year):
    """Row count of the newest earlier capture's page holding `year`."""
    for day in sorted(rows, reverse=True):
        for pg in rows[day]["pages"].values():
            if pg.get("year") == year:
                return pg["rows"]
    return None


def record_failure(now, url, error):
    fails = _load(FAILURES, [])
    fails.append({"at": archive_store.iso(now), "url": url,
                  "error": f"{type(error).__name__}: {error}"})
    _save(FAILURES, fails)


def capture(now=None, fetch=fetch_page, sleep=_time.sleep):
    """Archive today's three pages, write-once per UTC day. Returns the row.

    A day already held costs no request. All three bodies are fetched and
    parsed before any is written; a failure files nothing, is appended to
    failures.json, and is raised.
    """
    now = (now or archive_store.utcnow()).astimezone(timezone.utc)
    day = now.date().isoformat()
    rows = load_fetches()
    folder = day_dir(day)
    if day in rows:
        return rows[day]
    if os.path.exists(folder) and os.listdir(folder):
        raise RuntimeError(f"{NAME}: {_rel(folder)} exists without a fetches.json "
                           "row; refusing to overwrite a capture whose fetch "
                           "time is unknown")
    got = {}
    url = page_url()
    try:
        cur = fetch(url)
        parsed = {CURRENT: parse_page(cur[0])}
        got[CURRENT] = cur
        newest_year = _year_of(parsed[CURRENT])
        for y in range(newest_year - 1, newest_year - 1 - PRIOR_YEARS, -1):
            sleep(PAUSE_SECONDS)
            url = page_url(y)
            got[str(y)] = fetch(url)
            parsed[str(y)] = parse_page(got[str(y)][0])
            if _year_of(parsed[str(y)]) != y:
                raise NotTheTable(f"{NAME}: {url} holds {_year_of(parsed[str(y)])}")
        for label, per in parsed.items():
            prev = _previous_rows(rows, _year_of(per))
            if prev is not None and len(per) < prev:
                raise NotTheTable(f"{NAME}: the {label} page has {len(per)} rows, "
                                  f"fewer than the {prev} of the previous capture")
    except Exception as e:                                     # noqa: BLE001
        record_failure(now, url, e)
        raise
    daily, conflicts = merge(parsed)
    pages = {}
    for label in [CURRENT] + [k for k in got if k != CURRENT]:
        body, src = got[label][0], got[label][1]
        if label == CURRENT:
            # The manifest row is the current page's: one row per source, and
            # its changed_at must compare today's current page with
            # yesterday's, not with another year's page.
            block = provenance.record(NAME, src, body,
                                      fetched_at=archive_store.iso(now),
                                      ext="html", day=f"{day}/{label}")
        else:
            path = provenance.archive_path(NAME, f"{day}/{label}", "html")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".tmp", "wb") as f:
                f.write(body)
            os.replace(path + ".tmp", path)
            block = {"sha256": provenance.digest(body), "bytes": len(body),
                     "file": provenance.rel(path).replace(os.sep, "/")}
        per = parsed[label]
        pages[label] = {"url": src, "sha256": block["sha256"],
                        "bytes": block["bytes"], "file": block["file"],
                        "year": _year_of(per), "rows": len(per),
                        "newest_day": max(per).isoformat()}
        headers = getattr(got[label], "headers", None)
        if headers:
            pages[label]["headers"] = dict(headers)
    row = {"fetched_at": archive_store.iso(now), "pages": pages,
           "newest_day": max(daily).isoformat()}
    if conflicts:
        row["conflicts"] = conflicts
    rows[day] = row
    _save(FETCHES, rows)
    return row


def read_capture(day, fetches=None):
    """({date: count} merged, row) for one archived day; checks every hash."""
    fetches = fetches if fetches is not None else load_fetches()
    row = fetches[day]
    parsed = {}
    for label, pg in row["pages"].items():
        with open(os.path.join(ROOT, pg["file"]), "rb") as f:
            body = f.read()
        if provenance.digest(body) != pg["sha256"]:
            raise RuntimeError(f"{NAME}: {pg['file']} does not match its "
                               "recorded sha256; refusing a changed archive")
        parsed[label] = parse_page(body)
    return merge(parsed)[0], row


def _between(fetches, start, end):
    return archive_store.DailyArchive.snapshots_between(fetches, start, end)


# ---------------------------------------------------------------- rounds

def holiday_flags(week_start):
    d = monday(week_start)
    return {"target_week": us_holidays.misaligned(d, d + timedelta(days=6)),
            "reference_days": us_holidays.misaligned(d - timedelta(days=35),
                                                     d - timedelta(days=4))}


def references(daily, week_start):
    """Both references for the target week from one capture's days."""
    d = monday(week_start)
    ranked = baselines.reference(YOY, daily, d)
    shown = baselines.reference(PERSISTENCE_D14, weekly(daily), d)
    ranked["end_day"] = ranked["window"][-1]       # policy (3): recorded
    return {"zero_point": ZERO_POINT, "ranked": ranked, "shown": shown,
            "flags": holiday_flags(d)}


def lock_reference(week_start, fetches=None, reader=read_capture):
    """References from the newest capture between Saturday 00:00 ET and the
    lock, cited; none in that window and the round is not posted."""
    fetches = fetches if fetches is not None else load_fetches()
    a, b = lock_window(week_start)
    days = _between(fetches, a, b)
    if not days:
        raise NotPostable(f"{CARD}: no capture between Saturday 00:00 ET and the "
                          f"lock of {week_start}; the round is not posted")
    daily, row = reader(days[-1], fetches)
    out = {"snapshot": [pg["file"] for pg in row["pages"].values()],
           "sha256": {k: pg["sha256"] for k, pg in row["pages"].items()},
           "fetched_at": row["fetched_at"], "card": CARD}
    out.update(references(daily, week_start))
    return out


def resolve(week_start, now, fetches=None, reader=read_capture):
    """Card resolution and policy (1): the first capture at or after Tuesday
    D+8 12:00 ET holding all seven dates; none by Friday D+11 12:00 ET and the
    round is cancelled, never resolved later. Later revisions never change a
    resolved round."""
    fetches = fetches if fetches is not None else load_fetches()
    start, end = resolution_instant(week_start), grace_end(week_start)
    now = now.astimezone(timezone.utc)
    if now < start:
        return {"status": "pending",
                "reason": f"resolves at {archive_store.iso(start)}"}
    tried = []
    for day in _between(fetches, start, min(now, end)):
        daily, row = reader(day, fetches)
        total = week_total(daily, week_start)
        if total is not None:
            return {"status": "resolved", "value": float(total),
                    "snapshot": row["pages"][CURRENT]["file"],
                    "sha256": row["pages"][CURRENT]["sha256"],
                    "fetched_at": row["fetched_at"]}
        tried.append(day)
    if now > end:
        return {"status": "cancelled", "tried": tried,
                "reason": f"no capture with all seven dates by {archive_store.iso(end)}"}
    return {"status": "pending", "tried": tried,
            "reason": f"inside the grace window until {archive_store.iso(end)}"}


def pilot_change(week_start, early_day, late_day, fetches=None, reader=read_capture):
    """Pilot gate (b)/(c): the week's total in two captures and its change."""
    fetches = fetches if fetches is not None else load_fetches()
    a = week_total(reader(early_day, fetches)[0], week_start)
    b = week_total(reader(late_day, fetches)[0], week_start)
    out = {"week": as_date(week_start).isoformat(), "early": a, "late": b}
    if a is None or b is None or a <= 0:
        out.update(settled=False, reason="week incomplete in a capture")
        return out
    g = (b - a) / a
    out.update(change=round(g, 6), settled=abs(g) < PILOT_MAX_CHANGE)
    return out


if __name__ == "__main__":                                   # pragma: no cover
    import sys
    if sys.argv[1:2] == ["capture"]:
        try:
            print(json.dumps(capture(), indent=2))
        except Exception as e:                               # noqa: BLE001
            # One line the run log can be read for; the workflow step is
            # continue-on-error, so this never stops the refresh.
            sys.exit(f"{NAME} capture failed: {type(e).__name__}: {e}")
    elif sys.argv[1:2] == ["reference"] and len(sys.argv) == 3:
        print(json.dumps(lock_reference(sys.argv[2]), indent=2, default=str))
    else:
        sys.exit(f"usage: python -m ssa.adapters.{NAME} capture | reference <monday>")
