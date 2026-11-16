"""CTA daily boarding totals (City of Chicago Data Portal `6iiy-9s97`): monthly rail boardings.

  GET https://data.cityofchicago.org/resource/6iiy-9s97.json?$where=service_date >= '...'&$order=service_date

One row per service date: day_type (W weekday, A Saturday, U Sunday or
holiday), bus, rail_boardings, total_rides. Keyless. The rules are the
approved Data Card `chicago-cta-rail-monthly@v3`
(docs/data-cards/chicago-cta-rail-monthly.json, sha256
2aa2beddb4ff0bfac6e21d7277e7400349c6fcb7f5eb5f0a14b5a379ebfc9a86); this module implements them and nothing else.

**Rights and the City's conditions.** Granted 2026-11-15 by the project lead,
who accepted the City of Chicago Data Terms of Use conditions: the City's
disclaimer is shown verbatim wherever the data or a number derived from it is
shown (ssa/source_notices.py prints it on every question and forecast page),
SSA stops if the City asks, and the indemnity binds SSA as a user. Credit:
"Source: Chicago Transit Authority ... City of Chicago Data Portal".

**Archive.** `sources/cta_daily/<fetch day, UTC>.json`, the body exactly as
served, write-once per UTC day (`ssa/capture.DailyArchive`). CTA re-publishes
whole years (every 2024 row was re-created on 2025-04-29), so the endpoint
cannot be read as of a past date: the archive is the resolution source.

**The question.** Rail boardings summed over every service date of the target
month M, from the first daily snapshot that holds all of them; later
re-publications never change it. Void if M is still incomplete 100 days after
it ends. A row whose day type is not the calendar's, or whose bus + rail is
not total_rides, holds the round for review (policy (2)).

**The clock.** Lock 23:59 America/Chicago on the last day of M-1. The release
is estimated at 14:00 UTC forty days after M ends, the shortest lag measured
(Jan 2025 to Aug 2026: 40 to 150 days); a round resolves from the first
complete snapshot whenever it lands, up to day 100.

**The reference** (ranked zero point `cal_yoy3_rms12`, the card's
yoy(k=3, calendar=true, spread=rms12)), all from the lock capture:
base = sum over day types of (days of that type in M) x (mean rail per day of
that type in M-12); trend = the three newest complete months L, L-1, L-2 over
the same three a year earlier; reference = base x trend; not posted if L is
older than M-5. sd = reference x RMS of the same rule's relative errors over
the 12 newest target months complete in the capture, each with the same
offset M - L. Day types for a month come from the calendar (Saturday A,
Sunday or one of CTA's six observed holidays U, else W), the list fixed at
lock. Shown, not ranked: plain seasonal naive (M-12's total) and the
calendar base without the trend.

**Window.** 1,100 days, not the card's 900: the spread's oldest past target
needs month M - 25 - 2(M - L), which is M-35 at the M-5 limit; 900 days reach
about M-29, so the card's own rule would refuse every round with L older than
M-2 (card v3 access records this). The card's second, batch-timing request is
not made: no rule reads it.
"""
import calendar
import json
import math
import os
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests

from .. import capture as archive_store
from ..baselines import NotPostable

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NAME = "cta_daily"
ARCHIVE = os.path.join(ROOT, "sources", NAME)
FETCHES = os.path.join(ARCHIVE, "fetches.json")
EXT = "json"

CARD = "chicago-cta-rail-monthly@v3"
CARD_SHA256 = "2aa2beddb4ff0bfac6e21d7277e7400349c6fcb7f5eb5f0a14b5a379ebfc9a86"
DATASET = "6iiy-9s97"
BASE = f"https://data.cityofchicago.org/resource/{DATASET}.json"
WINDOW_DAYS = 1100
ROW_LIMIT = 5000
TIMEOUT = archive_store.TIMEOUT
TZ = ZoneInfo("America/Chicago")

ZERO_POINT = "cal_yoy3_rms12"
ZERO_POINT_SPEC = {"name": ZERO_POINT, "family": "yoy_k", "period": "month",
                   "k": 3, "lag": 1, "end": "newest", "max_lag": 5,
                   "base": "cta_day_type",
                   "spread": {"rule": "rms_relative", "n": 12}}
TREND_K = 3
MAX_OFFSET = 5
SPREAD_N = 12
VOID_DAYS = 100
RELEASE_LAG_DAYS = 40
LOCK_MAX_AGE = timedelta(hours=72)
DAY_TYPES = ("W", "A", "U")
FIELDS = ("service_date", "day_type", "bus", "rail_boardings", "total_rides")


# ---------------------------------------------------------------- months and days

def month_key(m):
    if isinstance(m, date):
        return f"{m.year:04d}-{m.month:02d}"
    s = str(m)
    datetime.strptime(s[:7], "%Y-%m")
    return s[:7]


def shift(month, n):
    y, m = int(month[:4]), int(month[5:7])
    k = y * 12 + (m - 1) + n
    return f"{k // 12:04d}-{k % 12 + 1:02d}"


def days_of(month):
    y, m = int(month[:4]), int(month[5:7])
    return [date(y, m, d) for d in range(1, calendar.monthrange(y, m)[1] + 1)]


def _observed(d):
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _nth(year, month, weekday, n):
    """n-th `weekday` of the month; n = -1 is the last."""
    days = [d for d in days_of(f"{year:04d}-{month:02d}") if d.weekday() == weekday]
    return days[n] if n < 0 else days[n - 1]


def holidays(year):
    """CTA's six observed holidays (card): New Year's Day, Memorial Day,
    Independence Day, Labor Day, Thanksgiving, Christmas."""
    return {
        _observed(date(year, 1, 1)): "New Year's Day",
        _nth(year, 5, 0, -1): "Memorial Day",
        _observed(date(year, 7, 4)): "Independence Day",
        _nth(year, 9, 0, 1): "Labor Day",
        _nth(year, 11, 3, 4): "Thanksgiving",
        _observed(date(year, 12, 25)): "Christmas",
    }


def day_type(d):
    """The calendar's day type: U for Sunday or a CTA holiday, A Saturday, W."""
    if d in holidays(d.year) or d in holidays(d.year + 1) or d.weekday() == 6:
        return "U"
    return "A" if d.weekday() == 5 else "W"


def day_type_counts(month):
    out = {t: 0 for t in DAY_TYPES}
    for d in days_of(month):
        out[day_type(d)] += 1
    return out


def _ct(day, hh, mm):
    return datetime.combine(day, time(hh, mm), TZ).astimezone(timezone.utc)


def lock_instant(month):
    """23:59 America/Chicago on the last day of the month before M."""
    return _ct(days_of(shift(month_key(month), -1))[-1], 23, 59)


def release_instant(month):
    """Estimated: 14:00 UTC forty days after M ends (the shortest lag seen)."""
    end = days_of(month_key(month))[-1] + timedelta(days=RELEASE_LAG_DAYS)
    return datetime.combine(end, time(14, 0), timezone.utc)


def void_instant(month):
    """Day 100 after M ends: still incomplete and the round is void."""
    end = days_of(month_key(month))[-1] + timedelta(days=VOID_DAYS)
    return datetime.combine(end, time(23, 59), timezone.utc)


# ---------------------------------------------------------------- fetching

def query_params(fetch_day):
    start = date.fromisoformat(str(fetch_day)[:10]) - timedelta(days=WINDOW_DAYS)
    return {"$where": f"service_date >= '{start.isoformat()}T00:00:00'",
            "$order": "service_date", "$limit": str(ROW_LIMIT)}


def request_url(fetch_day):
    return BASE + "?" + urlencode(query_params(fetch_day))


def fetch_text(fetch_day, timeout=TIMEOUT, get=requests.get):
    """One honest GET (ssa/capture.get). A 403 from the runner is raised as it
    came, never retried and never worked around: it means a local courier."""
    return archive_store.get(request_url(fetch_day), name=NAME,
                             accept="application/json", timeout=timeout,
                             get=get, refuse_stale=True)


# ---------------------------------------------------------------- parsing

def parse(body):
    """Body -> {date: {day_type, bus, rail, total}}. Fails loud on the shape."""
    try:
        rows = json.loads(body)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{NAME}: body is not JSON: {e}") from e
    if not isinstance(rows, list):
        raise ValueError(f"{NAME}: body is not a list of rows")
    if not rows:
        raise ValueError(f"{NAME}: no rows; refusing to read an empty answer")
    if len(rows) >= ROW_LIMIT:
        raise ValueError(f"{NAME}: {len(rows)} rows, at the $limit; rows may be missing")
    out = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != set(FIELDS):
            raise ValueError(f"{NAME}: row is not {{{', '.join(FIELDS)}}}: {row!r}")
        try:
            d = datetime.strptime(str(row["service_date"])[:10], "%Y-%m-%d").date()
            vals = {k: int(row[k]) for k in ("bus", "rail_boardings", "total_rides")}
        except ValueError:
            raise ValueError(f"{NAME}: malformed row {row!r}") from None
        if d in out:
            raise ValueError(f"{NAME}: {d} appears twice")
        if any(v < 0 for v in vals.values()):
            raise ValueError(f"{NAME}: negative count on {d}")
        out[d] = {"day_type": str(row["day_type"]), "bus": vals["bus"],
                  "rail": vals["rail_boardings"], "total": vals["total_rides"]}
    return out


def complete(daily, month):
    return all(d in daily for d in days_of(month))


def rail_total(daily, month):
    return sum(daily[d]["rail"] for d in days_of(month)) if complete(daily, month) else None


def newest_complete(daily):
    """L: the newest month every day of which is present."""
    if not daily:
        return None
    m = month_key(max(daily))
    for _ in range(24):
        if complete(daily, m):
            return m
        m = shift(m, -1)
    return None


def anomalies(daily, month):
    """Policy (2): unexpected day types and bus + rail != total, for review."""
    out = []
    for d in days_of(month):
        row = daily.get(d)
        if not row:
            continue
        if row["day_type"] != day_type(d):
            out.append(f"{d} coded {row['day_type']}, calendar says {day_type(d)}")
        if row["bus"] + row["rail"] != row["total"]:
            out.append(f"{d} bus + rail != total_rides")
    return out


# ---------------------------------------------------------------- archive

def _store():
    return archive_store.DailyArchive(NAME, ext=EXT, archive=ARCHIVE,
                                      fetches=FETCHES, root=ROOT)


def load_fetches():
    return _store().load()


def _describe(daily):
    return {"newest_day": max(daily).isoformat(),
            "newest_complete_month": newest_complete(daily)}


def capture(now=None, fetch=fetch_text):
    return _store().capture(fetch, parse=parse, describe=_describe, now=now)


def read_capture(day, fetches=None):
    body, row = _store().read(day, fetches)
    return parse(body), row


# ---------------------------------------------------------------- the reference

def _type_means(daily, month):
    """Mean rail per day of each type in `month`, by the month's own codes."""
    sums, counts = {t: 0 for t in DAY_TYPES}, {t: 0 for t in DAY_TYPES}
    for d in days_of(month):
        row = daily[d]
        t = row["day_type"] if row["day_type"] in DAY_TYPES else day_type(d)
        sums[t] += row["rail"]
        counts[t] += 1
    return {t: sums[t] / counts[t] for t in DAY_TYPES if counts[t]}


def rule(daily, month, offset):
    """(reference, base, trend) for `month` with L = month - offset, or raise."""
    year_ago = shift(month, -12)
    if not complete(daily, year_ago):
        raise NotPostable(f"{CARD} policy (4): {year_ago} is missing a day")
    means = _type_means(daily, year_ago)
    counts = day_type_counts(month)
    if any(counts[t] and t not in means for t in DAY_TYPES):
        raise NotPostable(f"{CARD}: {year_ago} has no day of a type {month} needs")
    base = sum(counts[t] * means[t] for t in DAY_TYPES if counts[t])
    L = shift(month, -offset)
    num = [rail_total(daily, shift(L, -i)) for i in range(TREND_K)]
    den = [rail_total(daily, shift(L, -12 - i)) for i in range(TREND_K)]
    if None in num or None in den:
        raise NotPostable(f"{CARD}: a trend month for {month} (L = {L}) is missing a day")
    trend = sum(num) / sum(den)
    return base * trend, base, trend


def references(daily, month):
    m = month_key(month)
    L = newest_complete(daily)
    if L is None:
        raise NotPostable(f"{CARD}: no complete month in the lock capture")
    offset = (int(m[:4]) * 12 + int(m[5:7])) - (int(L[:4]) * 12 + int(L[5:7]))
    if offset > MAX_OFFSET:
        raise NotPostable(f"{CARD} policy (4): the newest complete month {L} is "
                          f"older than M-{MAX_OFFSET} for {m}; the round is not posted")
    if offset < 1:
        raise NotPostable(f"{CARD}: {m} is already complete in the lock capture")
    ref, base, trend = rule(daily, m, offset)
    errs = []
    for i in range(SPREAD_N):
        p = shift(L, -i)
        actual = rail_total(daily, p)
        r_p, _, _ = rule(daily, p, offset)
        errs.append(actual / r_p - 1.0)
    rel = math.sqrt(sum(e * e for e in errs) / len(errs))
    plain = rail_total(daily, shift(m, -12))
    return {"zero_point": ZERO_POINT,
            "ranked": {"mean": round(ref, 1), "sd": round(ref * rel, 1)},
            "newest_complete_month": L, "offset": offset,
            "trend": round(trend, 6), "rms_relative_error": round(rel, 6),
            "day_types": day_type_counts(m),
            "holidays": {d.isoformat(): n for d, n in sorted(holidays(int(m[:4])).items())
                         if month_key(d) == m},
            "shown": {"seasonal_naive": float(plain), "calendar_base": round(base, 1)}}


def lock_capture(month, fetches):
    lock = lock_instant(month)
    day = archive_store.DailyArchive.snapshot_at_or_before(fetches, lock)
    if day is None:
        return None
    if lock - archive_store.parse_iso(fetches[day]["fetched_at"]) > LOCK_MAX_AGE:
        return None
    return day


def lock_reference(month, fetches=None, reader=read_capture):
    fetches = fetches if fetches is not None else load_fetches()
    m = month_key(month)
    day = lock_capture(m, fetches)
    if day is None:
        raise NotPostable(f"{CARD}: no capture in the {LOCK_MAX_AGE} before the "
                          f"lock of {m}; the round is not posted")
    daily, row = reader(day, fetches)
    out = {"snapshot": row["file"], "sha256": row["sha256"],
           "fetched_at": row["fetched_at"], "card": CARD}
    out.update(references(daily, m))
    return out


def resolve(month, now, fetches=None, reader=read_capture):
    """The first snapshot after M ends that holds every day of M; void at day 100."""
    fetches = fetches if fetches is not None else load_fetches()
    m = month_key(month)
    after = datetime.combine(days_of(m)[-1] + timedelta(days=1), time(0, 0), TZ) \
        .astimezone(timezone.utc)
    end = void_instant(m)
    now = now.astimezone(timezone.utc)
    if now < after:
        return {"status": "pending", "reason": f"{m} has not ended"}
    for d in archive_store.DailyArchive.snapshots_between(fetches, after, min(now, end)):
        daily, row = reader(d, fetches)
        total = rail_total(daily, m)
        if total is None:
            continue
        odd = anomalies(daily, m)
        if odd:
            return {"status": "review", "reason": "; ".join(odd[:5]),
                    "snapshot": row["file"]}
        return {"status": "resolved", "value": float(total),
                "snapshot": row["file"], "sha256": row["sha256"],
                "fetched_at": row["fetched_at"]}
    if now > end:
        return {"status": "cancelled",
                "reason": f"{m} still incomplete at day {VOID_DAYS} ({archive_store.iso(end)})"}
    return {"status": "pending", "reason": f"{m} not yet complete in any snapshot"}


def history(fetches=None, reader=read_capture):
    """[{date: 'YYYY-MM-01', value: rail boardings}] for complete months, newest capture."""
    fetches = fetches if fetches is not None else load_fetches()
    if not fetches:
        return []
    day = max(fetches, key=lambda d: fetches[d]["fetched_at"])
    daily, _ = reader(day, fetches)
    months = sorted({month_key(d) for d in daily})
    return [{"date": m + "-01", "value": float(rail_total(daily, m))}
            for m in months if complete(daily, m)]


if __name__ == "__main__":                                   # pragma: no cover
    import sys
    if sys.argv[1:2] == ["capture"]:
        try:
            print(json.dumps(capture(), indent=2, default=str))
        except Exception as e:                               # noqa: BLE001
            sys.exit(f"{NAME} capture failed: {type(e).__name__}: {e}")
    elif sys.argv[1:2] == ["reference"] and len(sys.argv) == 3:
        print(json.dumps(lock_reference(sys.argv[2]), indent=2, default=str))
    else:
        sys.exit(f"usage: python -m ssa.adapters.{NAME} capture | reference <YYYY-MM>")
