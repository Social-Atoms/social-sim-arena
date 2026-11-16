"""Texas mixed-beverage gross receipts (data.texas.gov `naix-2893`): Austin, monthly, year over year.

  GET https://data.texas.gov/resource/naix-2893.json?$select=...&$where=...&$group=d

One grouped request a day: Austin's monthly totals (total, liquor, wine, beer,
cover charges and the record count) for every reporting month whose
obligation end date falls in the trailing 800 days, about 27 rows. Keyless; no
app token, because getting one means an account. The rules are the approved
Data Card `texas-mixed-beverage-austin@v3`
(docs/data-cards/texas-mixed-beverage-austin.json, sha256
b63badbf106d2fb40a62f9edff0085d29a8e4f57df3e6e874c0d118fbf66a459); this module
implements them and nothing else.

**Rights.** Granted 2026-11-15 by the project lead (card archive_policy): the
Comptroller's site says "Information on CPA's sites is public domain and may be
copied and used as permitted by law". Credit: "Source: Texas Comptroller of
Public Accounts" (ssa/source_notices.py).

**Archive.** `sources/tx_mixbev/<fetch day, UTC>.json`, the body exactly as
served, write-once per UTC day (`ssa/capture.DailyArchive`). Rows for past
months are deleted and re-inserted every Saturday load, so the live endpoint
cannot be read as of a past date: the archive is the resolution source.

**The question.** Index = 100 x T(M) / T(M-12), T = Austin's summed
`total_receipts` for the reporting month. T(M) comes from the resolution
snapshot, T(M-12) from the snapshot archived at lock, frozen with the round.

**The clock.** Lock 23:59 America/Chicago on the last day of M-1. Resolution:
the first capture at or after 12:00 CT on the last day of M+1 whose Austin
record count for M is at least 97% of M-12's (policy (2)); the grace is 24
hours, after which the round is void, never resolved from a later pull.

**The reference** (ranked zero point `yoy_k0_rms12`, the card's
yoy(k=0, calendar=false, spread=rms12)): mean 100 (same as last year); sd =
root-mean-square of (index - 100) over the 12 newest target months M-13..M-2,
all from the lock capture. Policy (3): any month the reference or its spread
needs that is missing, or whose record count is under 90% of its year-earlier
month's, and the round is not posted. Shown, not ranked: the doc's growth
reference (M-2..M-4 over M-14..M-16) and persistence of growth (index of M-2).

Not made: the card's optional second request (batch timing by :created_at).
No rule reads it.
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
NAME = "tx_mixbev"
ARCHIVE = os.path.join(ROOT, "sources", NAME)
FETCHES = os.path.join(ARCHIVE, "fetches.json")
EXT = "json"

CARD = "texas-mixed-beverage-austin@v3"
CARD_SHA256 = "b63badbf106d2fb40a62f9edff0085d29a8e4f57df3e6e874c0d118fbf66a459"
DATASET = "naix-2893"
BASE = f"https://data.texas.gov/resource/{DATASET}.json"
CITY = "AUSTIN"
WINDOW_DAYS = 800
ROW_LIMIT = 5000
TIMEOUT = archive_store.TIMEOUT
TZ = ZoneInfo("America/Chicago")

ZERO_POINT = "yoy_k0_rms12"
ZERO_POINT_SPEC = {"name": ZERO_POINT, "family": "yoy_k", "period": "month",
                   "k": 0, "lag": 2, "spread": {"rule": "rms", "n": 12}}
SPREAD_N = 12
# Policy (2) at resolution, policy (3) at lock: record count against the
# year-earlier month's.
RESOLVE_COVERAGE = 0.97
LOCK_COVERAGE = 0.90
GRACE = timedelta(hours=24)
# Lesson 13: a lock reference is never read from an old capture.
LOCK_MAX_AGE = timedelta(hours=72)

FIELDS = ("tot", "liq", "wine", "beer", "cover", "n")

# ---------------------------------------------------------------- months

def month_key(m):
    """'YYYY-MM' from a date, 'YYYY-MM' or 'YYYY-MM-DD...' string."""
    if isinstance(m, date):
        return f"{m.year:04d}-{m.month:02d}"
    s = str(m)
    datetime.strptime(s[:7], "%Y-%m")
    return s[:7]


def shift(month, n):
    """month + n months (n may be negative)."""
    y, m = int(month[:4]), int(month[5:7])
    k = y * 12 + (m - 1) + n
    return f"{k // 12:04d}-{k % 12 + 1:02d}"


def last_day(month):
    y, m = int(month[:4]), int(month[5:7])
    return date(y, m, calendar.monthrange(y, m)[1])


def _ct(day, hh, mm):
    return datetime.combine(day, time(hh, mm), TZ).astimezone(timezone.utc)


def lock_instant(month):
    """23:59 America/Chicago on the last day of the month before M."""
    return _ct(last_day(shift(month_key(month), -1)), 23, 59)


def resolution_instant(month):
    """12:00 America/Chicago on the last day of the month after M."""
    return _ct(last_day(shift(month_key(month), 1)), 12, 0)


# ---------------------------------------------------------------- fetching

def query_params(fetch_day):
    start = date.fromisoformat(str(fetch_day)[:10]) - timedelta(days=WINDOW_DAYS)
    return {
        "$select": ("obligation_end_date_yyyymmdd as d, sum(total_receipts) as tot, "
                    "sum(liquor_receipts) as liq, sum(wine_receipts) as wine, "
                    "sum(beer_receipts) as beer, sum(cover_charge_receipts) as cover, "
                    "count(*) as n"),
        "$where": (f"upper(location_city)='{CITY}' AND obligation_end_date_yyyymmdd "
                   f">= '{start.isoformat()}T00:00:00'"),
        "$group": "d",
        "$order": "d",
        "$limit": str(ROW_LIMIT),
    }


def request_url(fetch_day):
    return BASE + "?" + urlencode(query_params(fetch_day))


def fetch_text(fetch_day, timeout=TIMEOUT, get=requests.get):
    """One honest GET (ssa/capture.get): a 403 or 429 is raised as it came and
    never retried; a body Socrata flags out of date is refused."""
    return archive_store.get(request_url(fetch_day), name=NAME,
                             accept="application/json", timeout=timeout,
                             get=get, refuse_stale=True)


# ---------------------------------------------------------------- parsing

def parse(body):
    """Body -> {'YYYY-MM': {tot, liq, wine, beer, cover: float, n: int}}.

    Fails loud: not a list, empty, at the row limit, a row without its keys,
    a period that is not a month end, a repeated month, a negative amount.
    """
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
        if not isinstance(row, dict) or set(row) != {"d", *FIELDS}:
            raise ValueError(f"{NAME}: row is not {{d, {', '.join(FIELDS)}}}: {row!r}")
        try:
            day = datetime.strptime(str(row["d"])[:10], "%Y-%m-%d").date()
        except ValueError:
            raise ValueError(f"{NAME}: period {row['d']!r} is not a date") from None
        month = month_key(day)
        if day != last_day(month):
            raise ValueError(f"{NAME}: period {day} is not a month end")
        if month in out:
            raise ValueError(f"{NAME}: month {month} appears twice")
        vals = {}
        for k in FIELDS:
            try:
                v = float(row[k]) if k != "n" else int(row[k])
            except (TypeError, ValueError):
                raise ValueError(f"{NAME}: {k} {row[k]!r} is not a number") from None
            if v < 0:
                raise ValueError(f"{NAME}: {k} is negative in {month}")
            vals[k] = v
        out[month] = vals
    return out


def component_gap(month_row):
    """liquor + wine + beer + cover - total: logged, never used (card)."""
    return round(month_row["liq"] + month_row["wine"] + month_row["beer"]
                 + month_row["cover"] - month_row["tot"], 2)


def index(months, m):
    """100 x T(m) / T(m-12) within one capture, or None if either is absent."""
    a, b = months.get(m), months.get(shift(m, -12))
    if not a or not b or b["tot"] <= 0:
        return None
    return 100.0 * a["tot"] / b["tot"]


def _covered(months, m, share):
    a, b = months.get(m), months.get(shift(m, -12))
    return bool(a and b and b["n"] > 0 and a["n"] >= share * b["n"])


# ---------------------------------------------------------------- archive

def _store():
    return archive_store.DailyArchive(NAME, ext=EXT, archive=ARCHIVE,
                                      fetches=FETCHES, root=ROOT)


def load_fetches():
    return _store().load()


def _describe(months):
    newest = max(months)
    return {"newest_month": newest, "months": len(months),
            "component_gap_newest": component_gap(months[newest])}


def capture(now=None, fetch=fetch_text):
    """Archive today's body, write-once per UTC day. Returns the fetches row."""
    return _store().capture(fetch, parse=parse, describe=_describe, now=now)


def read_capture(day, fetches=None):
    body, row = _store().read(day, fetches)
    return parse(body), row


# ---------------------------------------------------------------- the round

def references(months, month):
    """Ranked and shown references for target `month` from one capture."""
    m = month_key(month)
    need = [shift(m, -12)] + [shift(m, -i) for i in range(2, 2 + SPREAD_N)]
    bad = [x for x in need if not _covered(months, x, LOCK_COVERAGE)]
    if bad:
        raise NotPostable(f"{CARD} policy (3): month(s) {', '.join(sorted(bad))} "
                          "missing or under 90% of the year-earlier record count "
                          "in the lock capture; the round is not posted")
    errs = [index(months, shift(m, -i)) - 100.0 for i in range(2, 2 + SPREAD_N)]
    sd = math.sqrt(sum(e * e for e in errs) / len(errs))
    if sd <= 0:
        raise NotPostable(f"{CARD}: the spread is zero")
    shown = {"persistence_of_growth": round(index(months, shift(m, -2)), 4)}
    num = [months.get(shift(m, -i)) for i in (2, 3, 4)]
    den = [months.get(shift(m, -i)) for i in (14, 15, 16)]
    if all(num) and all(den):
        shown["growth3"] = round(100.0 * sum(x["tot"] for x in num)
                                 / sum(x["tot"] for x in den), 4)
    base = months[shift(m, -12)]
    return {"zero_point": ZERO_POINT,
            "ranked": {"mean": 100.0, "sd": round(sd, 4)},
            "denominator": {"month": shift(m, -12), "total_receipts": base["tot"],
                            "records": base["n"]},
            "shown": shown}


def lock_capture(month, fetches):
    """The newest capture at or before the lock, no older than LOCK_MAX_AGE."""
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
    months, row = reader(day, fetches)
    out = {"snapshot": row["file"], "sha256": row["sha256"],
           "fetched_at": row["fetched_at"], "card": CARD}
    out.update(references(months, m))
    return out


def resolve(month, now, fetches=None, reader=read_capture):
    """Card resolution and missing-data policies (1) and (2)."""
    fetches = fetches if fetches is not None else load_fetches()
    m = month_key(month)
    start = resolution_instant(m)
    end = start + GRACE
    now = now.astimezone(timezone.utc)
    if now < start:
        return {"status": "pending", "reason": f"resolves from {archive_store.iso(start)}"}
    day = lock_capture(m, fetches)
    if day is None:
        return {"status": "cancelled", "reason": "no lock capture: the round was never posted"}
    at_lock, _ = reader(day, fetches)
    base = at_lock.get(shift(m, -12))
    if not base or base["tot"] <= 0:
        return {"status": "cancelled", "reason": f"{shift(m, -12)} absent at lock"}
    tried = []
    for d in archive_store.DailyArchive.snapshots_between(fetches, start, min(now, end)):
        months, row = reader(d, fetches)
        got = months.get(m)
        if got and got["n"] >= RESOLVE_COVERAGE * base["n"]:
            return {"status": "resolved", "value": round(100.0 * got["tot"] / base["tot"], 6),
                    "snapshot": row["file"], "sha256": row["sha256"],
                    "fetched_at": row["fetched_at"]}
        tried.append(row["file"])
    if now > end:
        return {"status": "cancelled", "tried": tried,
                "reason": f"no snapshot with {m} at 97% coverage by {archive_store.iso(end)}"}
    return {"status": "pending", "tried": tried,
            "reason": f"inside the grace window until {archive_store.iso(end)}"}


def history(fetches=None, reader=read_capture):
    """[{date: 'YYYY-MM-01', value: index}] from the newest capture, for the board."""
    fetches = fetches if fetches is not None else load_fetches()
    if not fetches:
        return []
    day = max(fetches, key=lambda d: fetches[d]["fetched_at"])
    months, _ = reader(day, fetches)
    out = []
    for m in sorted(months):
        v = index(months, m)
        if v is not None and _covered(months, m, LOCK_COVERAGE):
            out.append({"date": m + "-01", "value": round(v, 4)})
    return out


if __name__ == "__main__":                                   # pragma: no cover
    import sys
    if sys.argv[1:2] == ["capture"]:
        try:
            print(json.dumps(capture(), indent=2))
        except Exception as e:                               # noqa: BLE001
            sys.exit(f"{NAME} capture failed: {type(e).__name__}: {e}")
    elif sys.argv[1:2] == ["reference"] and len(sys.argv) == 3:
        print(json.dumps(lock_reference(sys.argv[2]), indent=2, default=str))
    else:
        sys.exit(f"usage: python -m ssa.adapters.{NAME} capture | reference <YYYY-MM>")
