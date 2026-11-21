"""NYC 311 service requests (NYC Open Data, dataset erm2-nwe9).

  GET https://data.cityofnewyork.us/resource/erm2-nwe9.json
      ?$select=date_trunc_ymd(created_date) as d, agency, count(*) as n
      &$where=created_date >= '<fetch day - 280>T00:00:00'
      &$group=d, agency &$order=d, agency &$limit=50000

One grouped request a day returns the daily count of service requests by
responding agency for the trailing 280 days. That one body serves everything
this family needs: the lock-time reference, the outage checks, the fill-in
confirmation and the resolution. The rules are the approved Data Card
`nyc-311-requests@v2` (docs/data-cards/nyc-311-requests.json, sha256
ea4176dc4eb4c1c46084c38f85419a53f620325a52877942c90a60ffd5774e22); this module
implements them and nothing else.

**What is counted.** Every row of erm2-nwe9: all complaint types, agencies,
statuses and channels (card v2, population A), summed over the seven days of
a Monday-to-Sunday week in New York local time. `count(*)` equals
`count(distinct unique_key)` (80,626 both, week of 2026-10-26), so the cheaper
form is used. An agency code not in KNOWN_AGENCIES is counted -- the
population is all requests -- and reported (card v2, policy (6)).

**The timestamp is a Socrata floating timestamp, read as New York local
time**, so `date_trunc_ymd` already yields the local day.

**Stale replicas are refused, never filed.** Socrata serves some queries from
a secondary that can be days behind; it flags such a body with
`X-SODA2-Data-Out-Of-Date: true` (3 of 10 research queries, truth three days
old). `capture.get` raises `Stale` on that header, so the body never enters
the archive, and the next scheduled refresh tries again (lesson 5: no retry
loop). The freshness headers are kept in the capture's fetches row, and the
lock and resolution rules read only captures whose header says `false`.

**The data loads once a day at about 02:40Z** and each load carries rows to
about 02:00 of the load day, so at a Sunday-night lock Saturday is posted and
Sunday is not. Everything here works in whole weeks named by their Monday,
and the reference never touches the week that ends at the lock.

**The live endpoint is not addressable as of a past date**, so the archive is
the resolution source (docs/sources.md §1.2): `sources/nyc311/<fetch day,
UTC>.json`, the body exactly as served, write-once per day, with each
capture's fetch time, URL, sha256, size and freshness headers in
`sources/nyc311/fetches.json` (ssa/capture.py).

**Manual fallback.** If maintainers rule that automated retrieval is not
allowed, a named person downloads the same URL in a browser every Sunday and
files it with tools/ingest_nyc311.py, which proves freshness from the content
(`ingest_check`) because a browser save carries no headers. A round states
its mode when posted and never switches (`resolve(..., mode=)`).

**Rights.** NYC Administrative Code §23-502(b), (d) and the dataset's
`Automation: Yes`; no key, no app token, an honest User-Agent, one request a
day; a 403 or 429 is raised, never retried (lessons 3 and 5). The NYC.gov
Terms of Use returned 403 to automated fetches and wait on a person's reading
(card v2, archive_policy, PERSON TASK) before this merges to main.
"""
import json
import os
import statistics
from datetime import date, datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests

from .. import baselines
from .. import capture as archive_store

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NAME = "nyc311"
ARCHIVE = os.path.join(ROOT, "sources", NAME)
FETCHES = os.path.join(ARCHIVE, "fetches.json")

CARD = "nyc-311-requests@v2"
CARD_SHA256 = "ea4176dc4eb4c1c46084c38f85419a53f620325a52877942c90a60ffd5774e22"
DATASET = "erm2-nwe9"
BASE = f"https://data.cityofnewyork.us/resource/{DATASET}.json"
UA = archive_store.UA
# Card v2, source.access: a stale-secondary answer took 59-68 s and fresh
# ones 23-32 s, so the 60 s of the owner doc's sample code is too short.
TIMEOUT = 180
TZ = ZoneInfo("America/New_York")

# How far back one capture must reach for the card's rules to be computed
# from it alone. The spread searches 26 past target weeks W <= D-14, so the
# oldest is D-14-7*25 = D-189; its four reference weeks start back to D-224,
# a step back moves that to D-231, and the outage lookback adds 28 days:
# D-259, plus the day between capture and lock. The card's 280 days covers
# it (it counts D-252 without the step-back; the margin absorbs both).
WINDOW_DAYS = 280
# Socrata's $limit. 280 days x ~16 agencies is ~4,500 rows; a body that comes
# back with exactly this many rows was truncated, and is refused.
ROW_LIMIT = 50000

# Card v2, missing_data_policy (2): a target day fails if its total is below
# 40% of the median daily total over the 28 days before the week, or if any
# of these five agencies (together ~88% of requests) has no rows that day.
OUTAGE_FRACTION = 0.40
OUTAGE_LOOKBACK_DAYS = 28
KEY_AGENCIES = ("NYPD", "HPD", "DSNY", "DOT", "DEP")
# Every agency code in the 798-day research capture of 2026-11-09 (card
# evidence) and in the committed 280-day capture. A code outside this list is
# counted and reported, never dropped (policy (6)).
KNOWN_AGENCIES = ("DCWP", "DEP", "DHS", "DOB", "DOE", "DOHMH", "DOT", "DPR",
                  "DSNY", "EDC", "HPD", "NYC311-PRD", "NYPD", "OOS", "OTI",
                  "TLC")
# Card v2, resolution: the fill-in confirmation passes when the week grows by
# less than this share between the +8 and the +11 day snapshots.
FILLIN_MAX_GROWTH = 0.005
# Card v2, lock: the reference is read from the last fresh capture taken in
# the 24 h before the lock; with none, the round is not posted.
MAX_LOCK_SNAPSHOT_AGE = timedelta(hours=24)

# The ranked zero point (card v2, baseline; owner rule G1): one setting of the
# shared mean_k family. Weeks D-35..D-14, never the week that ends at the lock
# (lag 1); a block with a failing week steps back once (policy (3)); sd is the
# RMS of the same rule's own errors over 13 settled weeks, searching 26.
MEAN4 = {"name": "mean4", "family": "mean_k", "period": "week", "k": 4,
         "lag": 1, "step_back": 1,
         "spread": {"rule": "rms", "n": 13, "search": 26}}
# Shown, not ranked, each with its own rms13 spread (owner rule G2).
PERSISTENCE_SETTLED = {"name": "persistence_settled", "family": "mean_k",
                       "period": "week", "k": 1, "lag": 1, "step_back": 1,
                       "spread": {"rule": "rms", "n": 13, "search": 26}}
YOY4 = baselines.PRESETS["yoy4"]

UNDATED = "undated"
NotPostable = baselines.NotPostable


class Truncated(ValueError):
    """The body holds exactly ROW_LIMIT rows, so rows may be missing."""


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
    """Sunday 23:59 ET immediately before the target week, in UTC."""
    return _et(monday(week_start) - timedelta(days=1), 23, 59)


def resolution_instant(week_start):
    """Tuesday 12:00 ET, eight days after the week ends (Mon D+7 00:00)."""
    return _et(monday(week_start) + timedelta(days=15), 12, 0)


def truth_floor(week_start):
    """Monday 18:00 ET before the resolution Tuesday: the snapshot's truth
    time must be after it, so it carries Monday evening's load."""
    return _et(monday(week_start) + timedelta(days=14), 18, 0)


def grace_end(week_start):
    """Friday 12:00 ET after the scheduled resolution: the last chance."""
    return _et(monday(week_start) + timedelta(days=18), 12, 0)


def manual_capture_day(week_start):
    """Manual mode: the Sunday download six days after the week ends."""
    return monday(week_start) + timedelta(days=13)


def manual_grace_end(week_start):
    """Manual mode: a late download serves resolution until Wednesday 12:00 ET."""
    return _et(monday(week_start) + timedelta(days=16), 12, 0)


_iso = archive_store.iso
_parse_iso = archive_store.parse_iso


# ---------------------------------------------------------------- fetching

def query_params(fetch_day):
    start = as_date(fetch_day) - timedelta(days=WINDOW_DAYS)
    return {
        "$select": "date_trunc_ymd(created_date) as d, agency, count(*) as n",
        "$where": f"created_date >= '{start.isoformat()}T00:00:00'",
        "$group": "d, agency",
        "$order": "d, agency",
        "$limit": str(ROW_LIMIT),
    }


def request_url(fetch_day):
    """The exact URL a capture is filed under: what an auditor re-requests,
    and what the manual fallback's person opens in a browser."""
    return BASE + "?" + urlencode(query_params(fetch_day))


def fetch_text(fetch_day, timeout=TIMEOUT, get=requests.get):
    """(body exactly as served, URL) with the freshness headers on `.headers`.

    A 403/429 is raised as it came, never retried (lesson 3: a datacenter
    block means a local courier). A body flagged X-SODA2-Data-Out-Of-Date:
    true raises archive_store.Stale and is never filed (card v2 policy (5)).
    """
    return archive_store.get(request_url(fetch_day), name=NAME,
                             accept="application/json", timeout=timeout,
                             get=get, refuse_stale=True)


# ---------------------------------------------------------------- parsing

def parse(body):
    """Body -> {iso day: {agency: requests}}. Fails loud.

    Raises on anything that is not the grouped shape: an empty list, a
    truncated body, a duplicate (day, agency) pair, a non-integer or negative
    count. A null agency is kept under "" and counted.
    """
    try:
        rows = json.loads(body)
    except (TypeError, ValueError) as e:
        raise ValueError(f"nyc311 body is not JSON: {e}") from e
    if not isinstance(rows, list):
        raise ValueError("nyc311 body is not a list of rows "
                         f"(got {type(rows).__name__}): the endpoint contract changed")
    if not rows:
        raise ValueError("nyc311 body has no rows; refusing to read an empty "
                         "answer as a quiet week")
    if len(rows) >= ROW_LIMIT:
        raise Truncated(f"nyc311 body has {len(rows)} rows, the $limit: rows "
                        "may be missing")
    out = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) - {"d", "agency", "n"} \
                or "n" not in row:
            raise ValueError(f"nyc311 row is not {{d, agency, n}}: {row!r}")
        day = as_date(row["d"]).isoformat() if row.get("d") is not None else UNDATED
        agency = row.get("agency")
        agency = "" if agency is None else str(agency)
        try:
            n = int(row["n"])
        except (TypeError, ValueError):
            raise ValueError(f"nyc311 count is not an integer: {row!r}") from None
        if n < 0 or str(n) != str(row["n"]).strip():
            raise ValueError(f"nyc311 count is not a non-negative integer: {row!r}")
        per = out.setdefault(day, {})
        if agency in per:
            raise ValueError(f"nyc311 body repeats ({day}, {agency!r})")
        per[agency] = n
    return out


def unknown_agencies(daily):
    """Agency codes outside KNOWN_AGENCIES, with their totals (counted anyway)."""
    seen = {}
    for per in daily.values():
        for a, n in per.items():
            if a not in KNOWN_AGENCIES:
                seen[a] = seen.get(a, 0) + n
    return seen


def totals(daily):
    """{date: requests, all agencies}. A day absent from the body is absent."""
    return {as_date(d): sum(per.values()) for d, per in daily.items()
            if d != UNDATED}


def newest_day(daily):
    return max(as_date(d) for d in daily if d != UNDATED)


# ---------------------------------------------------------------- outages

def _floor(tot, before):
    """OUTAGE_FRACTION x median daily total over the 28 days before `before`
    (the days of that lookback the body holds; with none, no floor)."""
    prior = [tot[before - timedelta(days=i)]
             for i in range(1, OUTAGE_LOOKBACK_DAYS + 1)
             if (before - timedelta(days=i)) in tot]
    return OUTAGE_FRACTION * statistics.median(prior) if prior else 0


def day_fails(daily, tot, d, floor):
    """Card v2 policy (2) for one day: absent, below the floor, or a key
    agency with no rows (an absent agency row is a zero)."""
    n = tot.get(d)
    if n is None or n == 0 or n < floor:
        return True
    per = daily.get(d.isoformat(), {})
    return any(per.get(a, 0) == 0 for a in KEY_AGENCIES)


def week_days(week_start):
    m = monday(week_start)
    return [m + timedelta(days=i) for i in range(7)]


def week_total(tot, week_start):
    """Sum of the seven days, or None if any day is not in the body."""
    days = week_days(week_start)
    if any(d not in tot for d in days):
        return None
    return sum(tot[d] for d in days)


def outage_days(daily, week_start, tot=None):
    """Days of the week that fail the outage check, oldest first. The floor
    is 40% of the median daily total over the 28 days before the week."""
    tot = totals(daily) if tot is None else tot
    m = monday(week_start)
    floor = _floor(tot, m)
    return [d for d in week_days(m) if day_fails(daily, tot, d, floor)]


def weekly(daily):
    """({Monday: week total} for every complete week in the body, valid):
    `valid(week)` is the card's outage check, memoised per call."""
    tot = totals(daily)
    if not tot:
        return {}, lambda w: False
    m = min(tot)
    m = m + timedelta(days=(7 - m.weekday()) % 7)
    out = {}
    while m <= max(tot):
        t = week_total(tot, m)
        if t is not None:
            out[m] = t
        m += timedelta(days=7)
    seen = {}

    def valid(w):
        if w not in seen:
            seen[w] = not outage_days(daily, w, tot)
        return seen[w]
    return out, valid


def weekly_series(daily):
    """[{date: Sunday, value}] for every complete week strictly older than the
    newest posted day. Display only; rounds read their reference from
    `lock_reference` on the lock snapshot, never from this."""
    tot = totals(daily)
    if not tot:
        return []
    newest = max(tot)
    m = min(tot)
    m = m + timedelta(days=(7 - m.weekday()) % 7)
    out = []
    while m + timedelta(days=6) < newest:
        t = week_total(tot, m)
        if t is not None:
            out.append({"date": (m + timedelta(days=6)).isoformat(),
                        "value": float(t)})
        m += timedelta(days=7)
    return out


# ---------------------------------------------------------------- references

def reference(spec, daily, target_week):
    """One reference from one capture, with the weekly counts it averaged
    (card v2: the mean, the sd and the four weekly counts are published)."""
    weeks, valid = weekly(daily)
    ref = baselines.reference(spec, weeks, monday(target_week), valid)
    ref["counts"] = [weeks[as_date(w)] for w in ref["window"]]
    return ref


def mean4(daily, target_week):
    """The ranked zero point, `mean4`: NotPostable when it cannot be built."""
    return reference(MEAN4, daily, target_week)


def shown(daily, target_week):
    """The unranked references the board shows (card v2), each with its own
    measured spread. One this capture cannot support says why instead of
    taking a number: yoy4 needs a year-earlier block, which the card's
    280-day window does not reach."""
    out = {}
    for spec in (PERSISTENCE_SETTLED, YOY4):
        try:
            out[spec["name"]] = reference(spec, daily, target_week)
        except NotPostable as e:
            out[spec["name"]] = {"not_postable": str(e)}
    return out


# ---------------------------------------------------------------- archive

def _store():
    """The shared write-once daily archive (ssa/capture.py), built per call so
    tests that point ROOT / ARCHIVE / FETCHES at a sandbox are honoured."""
    return archive_store.DailyArchive(NAME, ext="json", archive=ARCHIVE,
                                      fetches=FETCHES, root=ROOT)


def archive_path(day):
    return _store().path(as_date(day).isoformat())


def load_fetches():
    return _store().load()


def _describe(daily):
    row = {"newest_day": newest_day(daily).isoformat()}
    full = newest_full_day(daily)
    if full:
        row["newest_full_day"] = full.isoformat()
    unknown = unknown_agencies(daily)
    if unknown:
        row["unknown_agencies"] = unknown
    if UNDATED in daily:
        row["undated_requests"] = sum(daily[UNDATED].values())
    return row


def capture(now=None, fetch=fetch_text):
    """Archive today's body, write-once per UTC day. Returns the fetches row.

    A day already held costs no request. A stale-replica body raises
    archive_store.Stale before anything is written; a malformed one raises
    from `parse`. Either way the next scheduled run tries again.
    """
    return _store().capture(fetch, parse=parse, describe=_describe, now=now)


def read_capture(day, fetches=None):
    """(parsed daily, fetches row) for one archived day; checks the hash."""
    body, row = _store().read(day, fetches)
    return parse(body), row


def truth_time(row):
    """X-SODA2-Truth-Last-Modified of a capture as a UTC datetime, or None."""
    h = (row.get("headers") or {}).get("x-soda2-truth-last-modified")
    if not h:
        return None
    try:
        return parsedate_to_datetime(h).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def truth_after(row, instant):
    """The capture's truth time is known and after `instant`."""
    t = truth_time(row)
    return t is not None and t > instant


def is_fresh(row):
    """A capture the rules may read. Automated: the server said
    X-SODA2-Data-Out-Of-Date: false (a missing header is not proof).
    Manual: `ingest_check` proved it from the content."""
    if row.get("manual"):
        return row.get("fresh_by_content") is True
    h = (row.get("headers") or {}).get("x-soda2-data-out-of-date", "")
    return h.strip().lower() == "false"


def _mode(row):
    return "manual" if row.get("manual") else "auto"


# ---------------------------------------------------------------- manual ingest

def newest_full_day(daily):
    """The newest day that passes the day outage check against the 28 days
    before it: the newest day the body carries in full."""
    tot = totals(daily)
    for d in sorted(tot, reverse=True):
        if not day_fails(daily, tot, d, _floor(tot, d)):
            return d
    return None


def ingest_check(body, capture_day):
    """Card v2 manual fallback, INGEST: validate a browser-saved body before
    it is filed under `capture_day` (the New York date of the download).

    Raises ValueError when the body cannot be the card's request for that
    day or cannot be shown fresh: it does not parse; its oldest day is not
    capture day - 280 (another URL); a day between that and capture day - 2
    is missing; or the newest day posted in full is not capture day - 2
    (Friday for a Sunday download), which is how a stale replica shows
    itself without headers.

    Outage failures in the four reference weeks of the next round and in the
    week being resolved are *reported*, not refused: the lock and resolution
    rules handle them (step back, next capture, cancel) on this same body.
    Returns the fields for the fetches row.
    """
    day = as_date(capture_day)
    daily = parse(body)
    tot = totals(daily)
    start = day - timedelta(days=WINDOW_DAYS)
    if min(tot) != start:
        raise ValueError(f"nyc311: the body starts {min(tot)}, not {start}: it is "
                         f"not the request for capture day {day}; open "
                         f"{request_url(day)}")
    want = day - timedelta(days=2)
    full = newest_full_day(daily)
    if full != want:
        raise ValueError(f"nyc311: the newest day posted in full is {full}, not "
                         f"{want} (capture day - 2): the body is not fresh, or "
                         "the download was taken before Saturday evening's "
                         "load; archive unchanged")
    missing = [d for d in (start + timedelta(days=i)
                           for i in range((want - start).days + 1)) if d not in tot]
    if missing:
        raise ValueError(f"nyc311: {len(missing)} day(s) missing between {start} "
                         f"and {want}, first {missing[0]}; archive unchanged")
    report = {"fresh_by_content": True, "newest_full_day": full.isoformat(),
              "newest_day": newest_day(daily).isoformat()}
    checks = {}
    if day.weekday() == 6:                        # a Sunday: it serves a lock
        nxt = day + timedelta(days=1)
        for w in (nxt - timedelta(days=7 * k) for k in (5, 4, 3, 2)):
            bad = outage_days(daily, w, tot)
            if bad:
                checks[f"reference week {w}"] = [d.isoformat() for d in bad]
    resolving = day - timedelta(days=13)
    resolving -= timedelta(days=resolving.weekday())
    bad = outage_days(daily, resolving, tot)
    if bad:
        checks[f"resolution week {resolving}"] = [d.isoformat() for d in bad]
    if checks:
        report["outage_days"] = checks
    unknown = unknown_agencies(daily)
    if unknown:
        report["unknown_agencies"] = unknown
    return report


def ingest_manual(path, capture_day=None, now=None):
    """File a person-downloaded body (tools/ingest_nyc311.py)."""
    now = (now or archive_store.utcnow()).astimezone(timezone.utc)
    day = as_date(capture_day) if capture_day else now.astimezone(TZ).date()
    return archive_store.ingest_manual(
        NAME, path, ext="json", capture_day=day.isoformat(), now=now,
        url=request_url(day), check=lambda b: ingest_check(b, day),
        describe=lambda r: r)


# ---------------------------------------------------------------- rounds

def lock_reference(target_week, fetches=None, reader=read_capture):
    """The ranked and shown references from the last fresh capture in the
    24 h before the lock, with its citation and the round's mode.
    NotPostable when there is none or the rule cannot be computed."""
    fetches = fetches if fetches is not None else load_fetches()
    lock = lock_instant(target_week)
    days = archive_store.DailyArchive.snapshots_between(
        fetches, lock - MAX_LOCK_SNAPSHOT_AGE, lock)
    fresh = [d for d in days if is_fresh(fetches[d])]
    if not fresh:
        raise NotPostable(f"no fresh capture in the {MAX_LOCK_SNAPSHOT_AGE} "
                          f"before the lock of {target_week} ({_iso(lock)}): "
                          "the round is not posted")
    day = fresh[-1]
    daily, row = reader(day, fetches)
    ranked = mean4(daily, target_week)
    out = {"card": CARD, "snapshot": row["file"], "sha256": row["sha256"],
           "fetched_at": row["fetched_at"], "mode": _mode(row),
           "zero_point": MEAN4["name"], "ranked": ranked,
           "shown": shown(daily, target_week)}
    if ranked["step_back"]:
        out["note"] = ("a reference week failed the outage check, so the "
                       "block stepped back one week (card v2 policy (3)); "
                       "the question text must say so")
    return out


def _passing(fetches, days, reader, target_week):
    tried = []
    for day in days:
        daily, row = reader(day, fetches)
        tot = totals(daily)
        bad = outage_days(daily, target_week, tot)
        total = week_total(tot, target_week)
        if not bad and total is not None:
            out = {"status": "resolved", "value": float(total),
                   "snapshot": row["file"], "sha256": row["sha256"],
                   "fetched_at": row["fetched_at"], "mode": _mode(row)}
            in_week = {d.isoformat() for d in week_days(target_week)}
            unknown = unknown_agencies({d: p for d, p in daily.items()
                                        if d in in_week})
            if unknown:
                out["unknown_agencies"] = unknown       # counted, logged (6)
            return out, tried
        tried.append({"snapshot": row["file"],
                      "outage_days": [d.isoformat() for d in bad]})
    return None, tried


def resolve(target_week, now, mode="auto", fetches=None, reader=read_capture):
    """Card v2 resolution and missing-data policy (1), (2).

    auto: the captures at or after Tuesday 12:00 ET (+8 days) up to Friday
    12:00 ET, earliest first, that are fresh and whose truth time is after
    Monday 18:00 ET; the first whose seven days pass the outage check
    resolves. manual: the person's downloads from the Sunday six days after
    the week ends, until Wednesday 12:00 ET. Before the window opens:
    pending; inside it with nothing passing: pending; after it: cancelled,
    never resolved from a later pull. A round never switches mode.
    """
    if mode not in ("auto", "manual"):
        raise ValueError(f"mode is 'auto' or 'manual', not {mode!r}")
    fetches = fetches if fetches is not None else load_fetches()
    now = now.astimezone(timezone.utc)
    if mode == "auto":
        start, end = resolution_instant(target_week), grace_end(target_week)
        floor = truth_floor(target_week)
        days = [d for d in archive_store.DailyArchive.snapshots_between(
                    fetches, start, min(now, end))
                if not fetches[d].get("manual") and is_fresh(fetches[d])
                and truth_after(fetches[d], floor)]
    else:
        first = manual_capture_day(target_week)
        start, end = _et(first, 0, 0), manual_grace_end(target_week)
        days = [d for d in archive_store.DailyArchive.snapshots_between(
                    fetches, start, min(now, end))
                if fetches[d].get("manual") and is_fresh(fetches[d])
                and d >= first.isoformat()]
    if now < start:
        return {"status": "pending", "reason": f"resolves at {_iso(start)}"}
    out, tried = _passing(fetches, days, reader, target_week)
    if out:
        return out
    if now > end:
        return {"status": "cancelled", "tried": tried,
                "reason": "no fresh capture passed the outage check by the "
                          f"deadline {_iso(end)}"}
    return {"status": "pending", "tried": tried,
            "reason": f"inside the grace window until {_iso(end)}"}


def fillin_check(week_start, fetches=None, reader=read_capture):
    """Card v2 resolution, the one confirmation check (owner rule G3):
    the week's count in the +8 snapshot (the auto resolution rule's) against
    the +11 snapshot (the first fresh capture at or after Friday 12:00 ET).
    `settled` when the change is under 0.5%; otherwise the first scored round
    runs unscored and a new card revision sets a longer lag."""
    fetches = fetches if fetches is not None else load_fetches()
    floor = truth_floor(week_start)
    auto = [d for d in sorted(fetches, key=lambda d: fetches[d]["fetched_at"])
            if not fetches[d].get("manual") and is_fresh(fetches[d])
            and truth_after(fetches[d], floor)]
    early = next((d for d in auto if _parse_iso(fetches[d]["fetched_at"])
                  >= resolution_instant(week_start)), None)
    late = next((d for d in auto if _parse_iso(fetches[d]["fetched_at"])
                 >= grace_end(week_start)), None)
    week = monday(week_start).isoformat()
    if early is None or late is None:
        return {"week": week, "early": early, "late": late, "settled": False,
                "reason": "a snapshot is missing: no-go"}
    a = week_total(totals(reader(early, fetches)[0]), week_start)
    b = week_total(totals(reader(late, fetches)[0]), week_start)
    if a is None or b is None or a <= 0:
        return {"week": week, "early": early, "late": late, "settled": False,
                "reason": "week incomplete in a snapshot"}
    g = (b - a) / a
    return {"week": week, "early": early, "late": late, "early_count": a,
            "late_count": b, "growth": round(g, 6),
            "settled": abs(g) < FILLIN_MAX_GROWTH}


if __name__ == "__main__":                                   # pragma: no cover
    import sys
    cmd = sys.argv[1:2]
    if cmd == ["capture"]:
        try:
            print(json.dumps(capture(), indent=2))
        except Exception as e:                               # noqa: BLE001
            # One line the run log can be read for; the workflow step is
            # continue-on-error, so this never stops the refresh.
            sys.exit(f"nyc311 capture failed: {type(e).__name__}: {e}")
    elif cmd == ["reference"] and len(sys.argv) == 3:
        print(json.dumps(lock_reference(sys.argv[2]), indent=2, default=str))
    elif cmd == ["resolve"] and len(sys.argv) in (3, 4):
        print(json.dumps(resolve(sys.argv[2], archive_store.utcnow(),
                                 *(sys.argv[3:4] or ["auto"])), indent=2))
    elif cmd == ["fillin"] and len(sys.argv) == 3:
        print(json.dumps(fillin_check(sys.argv[2]), indent=2))
    else:
        sys.exit("usage: python -m ssa.adapters.nyc311 capture | reference "
                 "<monday> | resolve <monday> [auto|manual] | fillin <monday>")
