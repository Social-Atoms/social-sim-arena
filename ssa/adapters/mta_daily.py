"""MTA Daily Ridership and Traffic (data.ny.gov `sayj-mze2`): weekly subway riders.

  https://data.ny.gov/api/views/sayj-mze2/rows.csv?accessType=DOWNLOAD

The portal's own "Export > CSV": one file, ~417 KB, every day since
2020-03-01 for nine modes, header `Date,Mode,Count`, dates MM/DD/YYYY, a
count or an empty cell ("not yet posted"). The rules are the approved Data
Card `mta-subway-weekly@v2` (docs/data-cards/mta-subway-weekly.json,
sha256 20269fe1b4524a3133e614048a0baabf3b16b91becc3a89c7b79c4b61843f575);
this module implements them and nothing else.

**Route B: a person downloads it, weekly.** The OPEN-NY terms are clear for
use and for manual download and unclear for automated retrieval (card,
archive_policy), so no workflow fetches this file. A person downloads it once
a week between Saturday 00:00 and Sunday 20:00 New York time and files it with
`tools/ingest_mta_daily.py`, which runs `check_download` before anything is
written and archives the bytes as `sources/mta_daily/<capture day, UTC>.csv`
through `ssa/capture.ingest_manual` (write-once, monotonic, hashed, manifest
row, a `fetches.json` row marked manual). `capture` -- the Route A daily fetch
-- exists so switching routes changes no round, and refuses to run until
`AUTOMATED_CLEARED` is set by the card revision that clears it; the
refresh.yml step that would call it is committed commented out.

**One download serves two rounds.** It is the lock capture for the week
starting the coming Monday D (lock Sunday D-1 23:59 ET), and the resolution
capture for the week starting D-14, which ended six days earlier.

**What is counted.** Mode `Subway`, which the dataset description says
includes the Staten Island Railway (SIR is published separately too, and is
not added). A null or zero count is "not posted" (card policy (3)); there is
no ratio-to-median outage rule, because real storm days fell to 21% of normal.

**The reference** is the arena's `yoy4` (ssa/baselines.PRESETS), exactly the
rule the card names: R = Subway over the four Monday-Sunday weeks starting
D-35..D-14 / the same 28 days 364 days earlier, times last year's week D-364,
spread = RMS of the same rule's errors over the 13 newest target weeks
complete in the lock capture. Policy (1) is checked first: if any reference
day, year-earlier day or day of last year's week is null or absent, the round
is cancelled -- so the preset's one-week step back is never what makes a
round postable.
"""
import csv
import io
import json
import os
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .. import baselines
from .. import capture as archive_store
from .. import us_holidays

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NAME = "mta_daily"
ARCHIVE = os.path.join(ROOT, "sources", NAME)
FETCHES = os.path.join(ARCHIVE, "fetches.json")
EXT = "csv"

CARD = "mta-subway-weekly@v2"
CARD_SHA256 = "20269fe1b4524a3133e614048a0baabf3b16b91becc3a89c7b79c4b61843f575"
DATASET = "sayj-mze2"
URL = f"https://data.ny.gov/api/views/{DATASET}/rows.csv?accessType=DOWNLOAD"
TZ = ZoneInfo("America/New_York")

HEADER = ["Date", "Mode", "Count"]
# Card access check (1): the nine codes seen on 2026-11-09. Card missing-data
# policy (4): a code never seen before is logged and does not block a Subway
# round, so an unknown one is reported (`unknown_modes`), not refused.
MODES = ("AAR", "BT", "Bus", "CBD Entries", "CRZ Entries", "LIRR", "MNR",
         "SIR", "Subway")
TARGET_MODE = "Subway"

# Route A, the scheduled daily fetch, waits on the publisher's or the
# maintainers' written clearance (card archive_policy, person task 2). Setting
# this is a card revision, never an edit on its own.
AUTOMATED_CLEARED = False

# The ranked reference and the unranked one shown beside it (card baseline).
ZERO_POINT = "yoy4"
YOY4 = baselines.PRESETS[ZERO_POINT]
PERSISTENCE_D14 = {
    "name": "persistence_d14", "family": "mean_k", "period": "week",
    "k": 1, "lag": 1, "search": 1,
    "spread": {"rule": "rms", "n": 13, "search": 26}}

# Card resolution pilot gate (b)/(c): a week total that moved less than this
# between the +6-day and the +13-day capture is settled.
PILOT_MAX_CHANGE = 0.005
# Card missing-data policy (7): this many missed Saturday windows in a row
# pause new rounds until a maintainer confirms the rota.
LAPSE_PAUSE = 2

NotPostable = baselines.NotPostable

REGULAR = "lock+resolution"
LATE = "late-resolution"


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
    """Sunday 23:59 New York time immediately before the target week."""
    return _et(monday(week_start) - timedelta(days=1), 23, 59)


def download_window(saturday):
    """The weekly download window: Saturday 00:00 to Sunday 20:00 ET."""
    if saturday.weekday() != 5:
        raise ValueError(f"{saturday} is not a Saturday")
    return _et(saturday, 0, 0), _et(saturday + timedelta(days=1), 20, 0)


def late_window(saturday):
    """Missing-data policy (2): a resolution-only download after the window,
    up to Tuesday 12:00 ET."""
    return _et(saturday + timedelta(days=1), 20, 0), \
        _et(saturday + timedelta(days=3), 12, 0)


def lock_window(week_start):
    return download_window(monday(week_start) - timedelta(days=2))


def resolution_window(week_start):
    """(Sat D+12 00:00 ET, Tue D+15 12:00 ET): the scheduled capture and its
    grace, earliest first."""
    sat = monday(week_start) + timedelta(days=12)
    return download_window(sat)[0], late_window(sat)[1]


def classify(downloaded_at):
    """(window, its Saturday) for a download instant, or raise: the card's
    access check (2) and missing-data policy (2)."""
    at = downloaded_at.astimezone(timezone.utc)
    local = at.astimezone(TZ).date()
    for back in range(0, 4):
        sat = local - timedelta(days=back)
        if sat.weekday() != 5:
            continue
        a, b = download_window(sat)
        if a <= at <= b:
            return REGULAR, sat
        a, b = late_window(sat)
        if a < at <= b:
            return LATE, sat
    shown = at.astimezone(TZ).strftime("%a %Y-%m-%d %H:%M")
    raise ValueError(
        f"downloaded at {archive_store.iso(at)} ({shown} New York): outside "
        "the Saturday 00:00 - Sunday 20:00 ET window and the Tuesday-noon "
        "resolution grace; archive unchanged")


# ---------------------------------------------------------------- parsing

def parse(body):
    """Body -> {mode: {date: count or None}}. Fails loud on the shape.

    Raises on a header other than exactly Date,Mode,Count (card access check
    (1)), a malformed date or count, a repeated (date, mode), or a file with
    no Subway rows.
    """
    if isinstance(body, (bytes, bytearray)):
        try:
            body = bytes(body).decode("utf-8-sig")
        except UnicodeDecodeError as e:
            raise ValueError(f"{NAME}: body is not UTF-8 text: {e}") from None
    rows = csv.reader(io.StringIO(body))
    header = next(rows, None)
    if header != HEADER:
        raise ValueError(f"{NAME}: header is {header!r}, not {HEADER!r}: the "
                         "export changed shape; archive unchanged")
    out = {}
    for n, row in enumerate(rows, start=2):
        if not row:
            continue
        if len(row) != 3:
            raise ValueError(f"{NAME}: line {n} has {len(row)} fields: {row!r}")
        d, mode, count = row
        try:
            day = datetime.strptime(d, "%m/%d/%Y").date()
        except ValueError:
            raise ValueError(f"{NAME}: line {n} date {d!r} is not MM/DD/YYYY") from None
        if count.strip() == "":
            value = None
        else:
            try:
                value = int(count.replace(",", ""))
            except ValueError:
                raise ValueError(f"{NAME}: line {n} count {count!r} is not an "
                                 "integer") from None
            if value < 0:
                raise ValueError(f"{NAME}: line {n} count {value} is negative")
        per = out.setdefault(mode, {})
        if day in per:
            raise ValueError(f"{NAME}: {day} {mode!r} appears twice")
        per[day] = value
    if not out.get(TARGET_MODE):
        raise ValueError(f"{NAME}: no {TARGET_MODE} rows; archive unchanged")
    return out


def unknown_modes(parsed):
    return sorted(m for m in parsed if m not in MODES)


def newest_posted(parsed):
    """{mode: newest date with a non-null count, ISO}, every mode present."""
    out = {}
    for mode, per in parsed.items():
        days = [d for d, v in per.items() if v is not None]
        if days:
            out[mode] = max(days).isoformat()
    return out


def subway(parsed):
    """{date: count} for the posted Subway days. Null and zero are not posted
    (card policy (3)) and are absent here."""
    return {d: v for d, v in parsed[TARGET_MODE].items() if v}


def week_days(week_start):
    m = monday(week_start)
    return [m + timedelta(days=i) for i in range(7)]


def week_total(daily, week_start):
    days = week_days(week_start)
    if any(d not in daily for d in days):
        return None
    return sum(daily[d] for d in days)


def weekly(daily):
    """{Monday: total} for every week with all seven days posted."""
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


# ---------------------------------------------------------------- the download

def required_newest(saturday):
    """Card access check (3): the newest Subway day must reach the Sunday
    six days before the window's Saturday -- D-8 for the coming lock, and the
    last day of the week this capture resolves."""
    return saturday - timedelta(days=6)


def check_download(body, downloaded_at):
    """Everything the ingest checks before a byte is written. Returns the
    fields filed with the capture; raises ValueError naming the failed check.
    """
    window, sat = classify(downloaded_at)
    parsed = parse(body)
    newest = newest_posted(parsed)
    need = required_newest(sat)
    have = newest.get(TARGET_MODE)
    if have is None or as_date(have) < need:
        raise ValueError(
            f"{NAME}: Friday posting missing: newest {TARGET_MODE} day is "
            f"{have}, the {window} capture of the weekend of {sat} needs "
            f"{need} or later; archive unchanged")
    lock_week = sat + timedelta(days=2)
    return {"window": window, "weekend": sat.isoformat(),
            "lock_of": lock_week.isoformat() if window == REGULAR else None,
            "resolves": (lock_week - timedelta(days=14)).isoformat(),
            "newest_posted": newest, "unknown_modes": unknown_modes(parsed),
            "card": CARD}


def ingest(path, downloaded_at, url=URL):
    """File a person's download (card route B). See tools/ingest_mta_daily.py."""
    at = downloaded_at.astimezone(timezone.utc)
    return archive_store.ingest_manual(
        NAME, path, ext=EXT, check=lambda body: check_download(body, at),
        capture_day=at.date().isoformat(), now=at, url=url,
        describe=lambda fields: fields)


def fetch_text(timeout=archive_store.TIMEOUT, get=None):
    """Route A's one honest GET (the same file and rules as route B)."""
    kw = {"get": get} if get else {}
    return archive_store.get(URL, name=NAME, accept="text/csv", timeout=timeout, **kw)


def capture(now=None, fetch=None):
    """Route A daily capture. Refuses while AUTOMATED_CLEARED is False."""
    if not AUTOMATED_CLEARED:
        raise RuntimeError(
            f"{NAME}: automated retrieval is not cleared (card {CARD}, route "
            "A waits on a written answer); download it by hand and run "
            "tools/ingest_mta_daily.py")
    return _store().capture(lambda day: (fetch or fetch_text)(), parse=parse,
                            describe=lambda p: {"newest_posted": newest_posted(p)},
                            now=now)


# ---------------------------------------------------------------- archive

def _store():
    return archive_store.DailyArchive(NAME, ext=EXT, archive=ARCHIVE,
                                      fetches=FETCHES, root=ROOT)


def load_fetches():
    return _store().load()


def read_capture(day, fetches=None):
    body, row = _store().read(day, fetches)
    return parse(body), row


def _captures(fetches, start, end, windows=(REGULAR, LATE)):
    days = archive_store.DailyArchive.snapshots_between(fetches, start, end)
    return [d for d in days if fetches[d].get("window", REGULAR) in windows]


def lock_capture(week_start, fetches):
    """The newest regular capture in the lock's Saturday-Sunday window."""
    a, b = lock_window(week_start)
    days = _captures(fetches, a, b, (REGULAR,))
    return days[-1] if days else None


# ---------------------------------------------------------------- rounds

def policy_one(daily, week_start):
    """Card missing-data policy (1): every reference day D-35..D-8, each one
    364 days earlier, and last year's week D-364.. must be posted."""
    d = monday(week_start)
    ref = [d - timedelta(days=i) for i in range(8, 36)]
    need = ref + [x - timedelta(days=364) for x in ref] + \
        [x - timedelta(days=364) for x in week_days(d)]
    return sorted(x.isoformat() for x in need if x not in daily)


def holiday_flags(week_start):
    """The round-text flags (owner decision rev 2): holidays the 364-day
    shift misaligns in the target week, and in the reference weeks."""
    d = monday(week_start)
    return {"target_week": us_holidays.misaligned(d, d + timedelta(days=6)),
            "reference_weeks": us_holidays.misaligned(d - timedelta(days=35),
                                                      d - timedelta(days=8))}


def references(daily, week_start):
    """Both references for the target week from one capture's Subway days."""
    d = monday(week_start)
    missing = policy_one(daily, d)
    if missing:
        raise NotPostable(f"{CARD} policy (1): {len(missing)} reference or "
                          f"year-earlier day(s) not posted, first {missing[0]}: "
                          "the round is cancelled")
    weeks = weekly(daily)
    ranked = baselines.reference(YOY4, weeks, d)
    if ranked["step_back"]:                       # unreachable after policy (1)
        raise NotPostable(f"{CARD}: the reference stepped back")
    shown = baselines.reference(PERSISTENCE_D14, weeks, d)
    return {"zero_point": ZERO_POINT, "ranked": ranked, "shown": shown,
            "flags": holiday_flags(d)}


def lock_reference(week_start, fetches=None, reader=read_capture):
    """The references from the capture filed in the lock's window, cited."""
    fetches = fetches if fetches is not None else load_fetches()
    day = lock_capture(week_start, fetches)
    if day is None:
        raise NotPostable(f"{CARD} policy (1): no download was filed in the "
                          f"Saturday-Sunday window before the lock of "
                          f"{week_start}; the round is cancelled")
    parsed, row = reader(day, fetches)
    out = {"snapshot": row["file"], "sha256": row["sha256"],
           "fetched_at": row["fetched_at"], "card": CARD}
    out.update(references(subway(parsed), week_start))
    return out


def resolve(week_start, now, fetches=None, reader=read_capture):
    """Card resolution and missing-data policy (2).

    The first capture from Saturday D+12 00:00 ET whose seven target days are
    all posted resolves the week; a late (resolution-only) download up to
    Tuesday D+15 12:00 ET may stand in. None by then: cancelled, never
    resolved from a later download.
    """
    fetches = fetches if fetches is not None else load_fetches()
    start, end = resolution_window(week_start)
    now = now.astimezone(timezone.utc)
    if now < start:
        return {"status": "pending", "reason": f"resolves from {archive_store.iso(start)}"}
    tried = []
    for day in _captures(fetches, start, min(now, end)):
        parsed, row = reader(day, fetches)
        total = week_total(subway(parsed), week_start)
        if total is not None:
            return {"status": "resolved", "value": float(total),
                    "snapshot": row["file"], "sha256": row["sha256"],
                    "fetched_at": row["fetched_at"], "window": row.get("window")}
        tried.append(row["file"])
    if now > end:
        return {"status": "cancelled", "tried": tried,
                "reason": f"no capture with all seven {TARGET_MODE} days by "
                          f"{archive_store.iso(end)}"}
    return {"status": "pending", "tried": tried,
            "reason": f"inside the grace window until {archive_store.iso(end)}"}


def pilot_change(week_start, early_day, late_day, fetches=None, reader=read_capture):
    """Pilot gate (b)/(c): the week's total in two captures and its change."""
    fetches = fetches if fetches is not None else load_fetches()
    a = week_total(subway(reader(early_day, fetches)[0]), week_start)
    b = week_total(subway(reader(late_day, fetches)[0]), week_start)
    out = {"week": as_date(week_start).isoformat(), "early": a, "late": b}
    if a is None or b is None or a <= 0:
        out.update(settled=False, reason="week incomplete in a capture")
        return out
    g = (b - a) / a
    out.update(change=round(g, 6), settled=abs(g) < PILOT_MAX_CHANGE)
    return out


def lapsed(fetches, now):
    """Missing-data policy (7): consecutive past Saturday windows, newest
    first, with no regular capture; `paused` at LAPSE_PAUSE."""
    now = now.astimezone(timezone.utc)
    sat = now.astimezone(TZ).date()
    sat -= timedelta(days=(sat.weekday() - 5) % 7)
    missed = []
    for _ in range(8):
        a, b = download_window(sat)
        if b < now:
            if _captures(fetches, a, b, (REGULAR,)):
                break
            missed.append(sat.isoformat())
        sat -= timedelta(days=7)
    return {"missed": missed, "paused": len(missed) >= LAPSE_PAUSE}


if __name__ == "__main__":                                   # pragma: no cover
    import sys
    if sys.argv[1:2] == ["capture"]:
        try:
            print(json.dumps(capture(), indent=2))
        except Exception as e:                               # noqa: BLE001
            sys.exit(f"{NAME} capture failed: {type(e).__name__}: {e}")
    elif sys.argv[1:2] == ["reference"] and len(sys.argv) == 3:
        print(json.dumps(lock_reference(sys.argv[2]), indent=2, default=str))
    elif sys.argv[1:2] == ["lapsed"]:
        print(json.dumps(lapsed(load_fetches(), archive_store.utcnow()), indent=2))
    else:
        sys.exit(f"usage: python -m ssa.adapters.{NAME} reference <monday> | lapsed")
