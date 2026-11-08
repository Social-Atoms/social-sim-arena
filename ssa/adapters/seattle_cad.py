"""Seattle Police Department calls for service (City of Seattle open data).

  GET https://cos-data.seattle.gov/resource/33kz-ixgy.json
      ?$select=date_trunc_ymd(cad_event_original_time_queued) as d,
               call_type, count(distinct cad_event_number) as n
      &$where=cad_event_original_time_queued >= '<fetch day - 420>T00:00:00'
      &$group=d, call_type &$order=d, call_type &$limit=50000

One grouped request a day returns the daily count of distinct CAD events by
how the call reached SPD, for the trailing 420 days. That one body serves
everything this family needs: the lock-time reference, the outage checks, the
pilot comparison and the resolution. The rules are the approved Data Card
`seattle-community-calls@v3` (docs/data-cards/seattle-community-calls.json);
this module implements them and nothing else.

**What is counted.** Community-initiated calls: call_type in COMMUNITY_TYPES
(911, non-emergency telephone, text message, in-person complaint), summed over
the seven days of a Monday-to-Sunday week in Seattle local time. Officer
activity (ONVIEW, PROACTIVE), automated alarm-company signals and
administrative entries are excluded (EXCLUDED_TYPES). A call_type in neither
list is excluded from the count and reported by `unknown_types`, never guessed
into one side. Events, not rows: the dataset carries several rows per event
(week of 2026-10-26: 11,591 rows for 6,614 events), hence the server-side
`count(distinct cad_event_number)`.

**The timestamp is a Socrata floating timestamp, read as Seattle local time.**
The hourly profile bottoms out at 05:00 and peaks at 16:00 (card evidence), so
`date_trunc_ymd` already yields the local day and no conversion is applied.

**The data trails the clock by about three days, in whole days.** On
2026-11-08 22:10Z the newest queue time was 2026-11-05 23:59:09. So at a
Sunday-night lock the week that is just ending is not posted, and a generic
`date < lock` freeze would hand the reference a partial week. Everything here
works in whole weeks named by their Monday, and the reference never touches
the week that ends at the lock (`reference_weeks`).

**The live endpoint is not addressable as of a past date**, so the archive is
the resolution source (docs/sources.md §1.2, the civiqs and sce pattern):
`sources/seattle_cad/<fetch day, UTC>.json`, the body exactly as served,
write-once per day -- the first capture of a day wins and a later fetch on the
same day costs no request at all. Because the resolution and lock rules are
stated in *instants* ("first snapshot at or after Tue 12:00 PT"), and the
provenance manifest keeps only the newest fetch per source, each capture's
`fetched_at`, URL, sha256 and size are also kept in `sources/seattle_cad/
fetches.json`, one row per day.

**Rights.** City of Seattle Open Data Policy OD-1 §D and §E and the dataset's
own licenseId PUBLIC_DOMAIN (inventory row `seattle_cad`). No key, no app
token, an honest User-Agent, one request a day; a 403 or 429 is raised, never
retried (lessons 3 and 5 in the track agent's lessons file).
"""
import json
import os
import statistics
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests

from .. import provenance

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NAME = "seattle_cad"
ARCHIVE = os.path.join(ROOT, "sources", NAME)
FETCHES = os.path.join(ARCHIVE, "fetches.json")

DATASET = "33kz-ixgy"
BASE = f"https://cos-data.seattle.gov/resource/{DATASET}.json"
UA = "social-simulation-arena/1.0 (research benchmark; contact via repository)"
TIMEOUT = 120
TZ = ZoneInfo("America/Los_Angeles")

# Card v2, target.population. Frozen at season start: changing either tuple is
# a rule change and needs a new card revision, not an edit here.
COMMUNITY_TYPES = ("911", "TELEPHONE OTHER, NOT 911", "TEXT MESSAGE",
                   "IN PERSON COMPLAINT")
EXCLUDED_TYPES = ("ONVIEW", "PROACTIVE (OFFICER INITIATED)",
                  "ALARM CALL (NOT POLICE ALARM)",
                  "SCHEDULED EVENT (RECURRING)", "HISTORY CALL (RETRO)")

# How far back one capture must reach for the card's rules to be computed
# from it alone. The spread (card v3) looks for SPREAD_WEEKS past target weeks
# W <= D-14, searching back at most SPREAD_SEARCH_WEEKS, so the oldest is
# D-14-7*25 = D-189; that week's own reference, with a step-back, reaches its
# year-earlier weeks at -42-364, and their outage check needs 28 days more:
# D-623, plus the day between capture and lock. Card v3 says 540, which covers
# thirteen weeks with no skip (D-532) but not the 26-week search it also
# states; 630 computes the card's rule exactly (named in the PR).
WINDOW_DAYS = 630
# Socrata's $limit. 420 days x ~7 call types is ~3,000 rows; a body that comes
# back with exactly this many rows was truncated, and is refused.
ROW_LIMIT = 50000

# Card v2, missing_data_policy (2): a day below this share of the median daily
# count over the 28 days before its week is an outage, not behaviour. Two years
# of history bottom out at 64% (Christmas Day 2025).
OUTAGE_FRACTION = 0.40
OUTAGE_LOOKBACK_DAYS = 28
# Card v2, resolution pilot gate: growth after the +7-day snapshot below this
# share of the week's count is "settled".
PILOT_MAX_GROWTH = 0.005
# Card v3, the reference's spread: root-mean-square error of the same rule over
# the 13 most recent target weeks complete in the lock capture (W <= D-14),
# skipping a week that is not postable or fails its outage check, looking back
# at most 26 weeks; fewer than 13 and the round is not posted.
SPREAD_WEEKS = 13
SPREAD_SEARCH_WEEKS = 26
# "The snapshot archived at lock" (card v2, baseline): the newest capture at or
# before the lock, and no older than this. With daily captures it is the same
# day's; a stale one -- the refresh failed for days -- would hand the reference
# a step-back nobody chose, so the round is refused instead.
MAX_LOCK_SNAPSHOT_AGE = timedelta(hours=24)


# Events with a null queue time come back as a group with no `d` key. They
# belong to no week, so they are kept apart under this key, counted, and never
# summed -- a single such row must not stop every capture after it.
UNDATED = "undated"


class Truncated(ValueError):
    """The body holds exactly ROW_LIMIT rows, so rows may be missing."""


class NotPostable(ValueError):
    """The lock snapshot cannot support a reference (card v2 policy (3))."""


# ---------------------------------------------------------------- the clock

def as_date(d):
    return d if isinstance(d, date) and not isinstance(d, datetime) \
        else date.fromisoformat(str(d)[:10])


def monday(d):
    d = as_date(d)
    if d.weekday() != 0:
        raise ValueError(f"{d} is not a Monday; weeks are named by their Monday")
    return d


def _pt(day, hh, mm):
    return datetime.combine(day, time(hh, mm), TZ).astimezone(timezone.utc)


def lock_instant(week_start):
    """Sunday 23:59 PT immediately before the target week, in UTC."""
    return _pt(monday(week_start) - timedelta(days=1), 23, 59)


def resolution_instant(week_start):
    """Tuesday 12:00 PT, eight days after the week ends (Mon D+7 00:00)."""
    return _pt(monday(week_start) + timedelta(days=15), 12, 0)


def grace_end(week_start):
    """Friday 12:00 PT after the scheduled resolution: the last chance."""
    return _pt(monday(week_start) + timedelta(days=18), 12, 0)


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- fetching

def query_params(fetch_day):
    start = as_date(fetch_day) - timedelta(days=WINDOW_DAYS)
    return {
        "$select": ("date_trunc_ymd(cad_event_original_time_queued) as d, "
                    "call_type, count(distinct cad_event_number) as n"),
        "$where": f"cad_event_original_time_queued >= '{start.isoformat()}T00:00:00'",
        "$group": "d, call_type",
        "$order": "d, call_type",
        "$limit": str(ROW_LIMIT),
    }


def request_url(fetch_day):
    """The exact URL a capture is filed under: what an auditor re-requests."""
    return BASE + "?" + urlencode(query_params(fetch_day))


def fetch_text(fetch_day, timeout=TIMEOUT, get=requests.get):
    """(body exactly as served, as bytes; the URL it came from).

    An HTTP error is raised as it came (a 403 from a datacenter runner is a
    finding: the source then needs a local courier, docs/sources.md and lesson
    3). A dropped connection is retried once, never more (docs/sources.md §2.5).
    """
    url = request_url(fetch_day)
    for attempt in (1, 2):
        try:
            r = get(url, headers={"User-Agent": UA, "Accept": "application/json"},
                    timeout=timeout, allow_redirects=False)
        except (requests.ConnectionError, requests.Timeout):
            if attempt == 2:
                raise
            continue
        if r.status_code != 200:
            raise requests.HTTPError(
                f"seattle_cad: HTTP {r.status_code} from {url}", response=r)
        return r.content, url


# ---------------------------------------------------------------- parsing

def parse(body):
    """Body -> {iso day: {call_type: distinct events}}. Fails loud.

    Raises on anything that is not the grouped shape: an empty list (a quiet
    week and a broken query look identical otherwise), a truncated body, a
    duplicate (day, call_type) pair, a non-integer or negative count.
    """
    try:
        rows = json.loads(body)
    except (TypeError, ValueError) as e:
        raise ValueError(f"seattle_cad body is not JSON: {e}") from e
    if not isinstance(rows, list):
        raise ValueError("seattle_cad body is not a list of rows "
                         f"(got {type(rows).__name__}): the endpoint contract changed")
    if not rows:
        raise ValueError("seattle_cad body has no rows; refusing to read an "
                         "empty answer as a quiet week")
    if len(rows) >= ROW_LIMIT:
        raise Truncated(f"seattle_cad body has {len(rows)} rows, the $limit: "
                        "rows may be missing")
    out = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) - {"d", "call_type", "n"} \
                or "n" not in row:
            raise ValueError(f"seattle_cad row is not {{d, call_type, n}}: {row!r}")
        day = as_date(row["d"]).isoformat() if row.get("d") is not None else UNDATED
        ctype = row.get("call_type")          # Socrata omits a null group key
        ctype = "" if ctype is None else str(ctype)
        try:
            n = int(row["n"])
        except (TypeError, ValueError):
            raise ValueError(f"seattle_cad count is not an integer: {row!r}") from None
        if n < 0 or str(n) != str(row["n"]).strip():
            raise ValueError(f"seattle_cad count is not a non-negative integer: {row!r}")
        per = out.setdefault(day, {})
        if ctype in per:
            raise ValueError(f"seattle_cad body repeats ({day}, {ctype!r})")
        per[ctype] = n
    return out


def unknown_types(daily):
    """Call types in the body that neither list names, with their totals."""
    known = set(COMMUNITY_TYPES) | set(EXCLUDED_TYPES)
    seen = {}
    for per in daily.values():
        for t, n in per.items():
            if t not in known:
                seen[t] = seen.get(t, 0) + n
    return seen


def community_daily(daily):
    """{date: community-initiated events}. A day absent from the body is absent."""
    return {as_date(d): sum(per.get(t, 0) for t in COMMUNITY_TYPES)
            for d, per in daily.items() if d != UNDATED}


def newest_day(daily):
    return max(as_date(d) for d in daily if d != UNDATED)


# ---------------------------------------------------------------- weeks

def week_days(week_start):
    m = monday(week_start)
    return [m + timedelta(days=i) for i in range(7)]


def week_total(cd, week_start):
    """Sum of the seven days, or None if any day is not in the body."""
    days = week_days(week_start)
    if any(d not in cd for d in days):
        return None
    return sum(cd[d] for d in days)


def outage_days(cd, week_start):
    """Days of the week that fail the card's outage check, oldest first.

    A day fails if it is absent, zero, or below OUTAGE_FRACTION of the median
    daily count over the OUTAGE_LOOKBACK_DAYS before the week. The median uses
    the days of that lookback the body holds; with none, only absence and zero
    are checked.
    """
    m = monday(week_start)
    prior = [cd[m - timedelta(days=i)] for i in range(1, OUTAGE_LOOKBACK_DAYS + 1)
             if (m - timedelta(days=i)) in cd]
    floor = OUTAGE_FRACTION * statistics.median(prior) if prior else 0
    bad = []
    for d in week_days(m):
        n = cd.get(d)
        if n is None or n == 0 or n < floor:
            bad.append(d)
    return bad


def reference_weeks(target_week, step_back=0):
    """The four settled reference weeks, oldest first (card v2, baseline).

    Target week starts Monday D, lock is Sunday D-1 23:59 PT. The weeks are
    the four starting D-35, D-28, D-21, D-14; the week D-7..D-1, which ends at
    the lock, is never one of them. A step back shifts the whole block one week.
    """
    d = monday(target_week)
    return [d - timedelta(days=7 * k + 7 * step_back) for k in (5, 4, 3, 2)]


def _weeks_ok(cd, weeks):
    return all(week_total(cd, w) is not None and not outage_days(cd, w)
               for w in weeks)


def yoy4_reference(cd, target_week):
    """The ranked reference: 4-week year-over-year ratio of sums (card v2).

    R = sum over the four reference weeks / sum over the same four weeks
    shifted back 364 days; mean = R x (target-aligned week 364 days back).
    Never a mean of weekly ratios. Policy (3): if any reference week, any
    year-earlier counterpart or the aligned week fails the outage check, the
    block steps back one week; if it still fails, NotPostable.

    Returns the point and its parts. The spread is added by `yoy4` (card v3).
    """
    d = monday(target_week)
    aligned = d - timedelta(days=364)
    if week_total(cd, aligned) is None or outage_days(cd, aligned):
        raise NotPostable(f"last year's aligned week {aligned} fails the outage "
                          "check; the round is not posted")
    for step in (0, 1):
        weeks = reference_weeks(d, step)
        earlier = [w - timedelta(days=364) for w in weeks]
        if _weeks_ok(cd, weeks) and _weeks_ok(cd, earlier):
            num = sum(week_total(cd, w) for w in weeks)
            den = sum(week_total(cd, w) for w in earlier)
            if den <= 0:
                raise NotPostable("year-earlier reference weeks sum to zero")
            ratio = num / den
            ly = week_total(cd, aligned)
            return {"method": "yoy4_ratio_of_sums",
                    "mean": round(ratio * ly, 2),
                    "ratio": round(ratio, 6),
                    "numerator": num, "denominator": den,
                    "last_year_week": aligned.isoformat(),
                    "last_year_count": ly,
                    "reference_weeks": [w.isoformat() for w in weeks],
                    "step_back": step}
    raise NotPostable(f"reference weeks for {d} fail the outage check even "
                      "after one step back; the round is not posted")


def persistence_settled(cd, target_week):
    """Shown, not ranked: the count of the settled week D-14..D-8."""
    d = monday(target_week)
    for step in (0, 1):
        w = d - timedelta(days=14 + 7 * step)
        if week_total(cd, w) is not None and not outage_days(cd, w):
            return {"method": "persistence_settled_week",
                    "mean": float(week_total(cd, w)),
                    "week": w.isoformat(), "step_back": step}
    raise NotPostable(f"no settled persistence week for {d}")


def past_errors(cd, target_week, reference):
    """(actual - reference mean) for the card v3 spread window, newest first.

    Every number is recomputed from `cd`, the one lock capture: a past week's
    reference by the same rule, its actual from the same body.
    """
    d = monday(target_week)
    errors, weeks = [], []
    for k in range(2, 2 + SPREAD_SEARCH_WEEKS):
        w = d - timedelta(days=7 * k)
        actual = week_total(cd, w)
        if actual is None or outage_days(cd, w):
            continue
        try:
            ref = reference(cd, w)
        except NotPostable:
            continue
        errors.append(actual - ref["mean"])
        weeks.append(w.isoformat())
        if len(errors) == SPREAD_WEEKS:
            return errors, weeks
    raise NotPostable(f"only {len(errors)} of {SPREAD_WEEKS} past target weeks "
                      f"qualify for the spread of {d} within "
                      f"{SPREAD_SEARCH_WEEKS} weeks; the round is not posted")


def rms(errors):
    return (sum(e * e for e in errors) / len(errors)) ** 0.5


def with_spread(cd, target_week, reference):
    """A reference plus its card v3 spread: {mean, sd, spread_weeks, ...}."""
    out = dict(reference(cd, target_week))
    errors, weeks = past_errors(cd, target_week, reference)
    # Floor after rounding: CRPS needs sd > 0, and a perfectly flat history
    # (only ever a fixture) must not round its floor away.
    out["sd"] = max(round(rms(errors), 2), 0.01)
    out["spread"] = "rms_13_settled_weeks"
    out["spread_weeks"] = weeks
    return out


def yoy4(cd, target_week):
    """The ranked zero point `yoy4` as the leaderboard reads it."""
    out = with_spread(cd, target_week, yoy4_reference)
    out["method"] = "yoy4"
    return out


def persistence_shown(cd, target_week):
    """Unranked persistence on D-14, spread the same way from its own errors."""
    return with_spread(cd, target_week, persistence_settled)


def weekly_series(cd):
    """[{date: Sunday, value}] for every complete week strictly older than the
    newest posted day. For display and history only; rounds read their
    reference from `yoy4_reference` on the lock snapshot, never from this."""
    if not cd:
        return []
    newest = max(cd)
    m = min(cd)
    m = m + timedelta(days=(7 - m.weekday()) % 7)
    out = []
    while m + timedelta(days=6) < newest:
        t = week_total(cd, m)
        if t is not None:
            out.append({"date": (m + timedelta(days=6)).isoformat(), "value": float(t)})
        m += timedelta(days=7)
    return out


# ---------------------------------------------------------------- archive

def archive_path(day):
    return os.path.join(ARCHIVE, f"{as_date(day).isoformat()}.json")


def load_fetches(path=None):
    try:
        with open(path or FETCHES) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_fetches(rows, path=None):
    path = path or FETCHES
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rows, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def capture(now=None, fetch=fetch_text):
    """Archive today's body, write-once per UTC day. Returns the fetches row.

    A day already held costs no request. The body is parsed *before* it is
    filed, so a malformed answer fails the run instead of entering the record;
    a body is filed exactly as served.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    day = now.date().isoformat()
    rows = load_fetches()
    if day in rows and os.path.exists(archive_path(day)):
        return rows[day]
    if os.path.exists(archive_path(day)):
        raise RuntimeError(f"seattle_cad: {provenance.rel(archive_path(day))} "
                           "exists without a fetches.json row; refusing to "
                           "overwrite a capture whose fetch time is unknown")
    body, url = fetch(day)
    daily = parse(body)
    block = provenance.record(NAME, url, body, fetched_at=_iso(now), ext="json",
                              day=day)
    rows[day] = {"fetched_at": block["fetched_at"], "url": url,
                 "sha256": block["sha256"], "bytes": block["bytes"],
                 "file": block["file"], "newest_day": newest_day(daily).isoformat()}
    unknown = unknown_types(daily)
    if unknown:
        rows[day]["unknown_types"] = unknown
    if UNDATED in daily:
        rows[day]["undated_events"] = sum(daily[UNDATED].values())
    _save_fetches(rows)
    return rows[day]


def read_capture(day, fetches=None):
    """(parsed daily, fetches row) for one archived day; checks the hash."""
    fetches = fetches if fetches is not None else load_fetches()
    row = fetches[day]
    with open(os.path.join(ROOT, row["file"]), "rb") as f:
        body = f.read()
    if provenance.digest(body) != row["sha256"]:
        raise RuntimeError(f"seattle_cad: {row['file']} does not match its "
                           "recorded sha256; refusing a changed archive")
    return parse(body), row


def snapshot_at_or_before(fetches, instant):
    """The newest capture day whose fetched_at is at or before `instant`."""
    days = [d for d, r in fetches.items() if _parse_iso(r["fetched_at"]) <= instant]
    return max(days, key=lambda d: fetches[d]["fetched_at"]) if days else None


def snapshots_between(fetches, start, end):
    """Capture days with start <= fetched_at <= end, earliest first."""
    days = [d for d, r in fetches.items()
            if start <= _parse_iso(r["fetched_at"]) <= end]
    return sorted(days, key=lambda d: fetches[d]["fetched_at"])


# ---------------------------------------------------------------- rounds

def lock_reference(target_week, fetches=None, reader=read_capture):
    """Both references from the snapshot archived at lock, with its citation."""
    fetches = fetches if fetches is not None else load_fetches()
    lock = lock_instant(target_week)
    day = snapshot_at_or_before(fetches, lock)
    if day is None:
        raise NotPostable(f"no capture at or before the lock of {target_week}")
    if lock - _parse_iso(fetches[day]["fetched_at"]) > MAX_LOCK_SNAPSHOT_AGE:
        raise NotPostable(f"the newest capture before the lock of {target_week} "
                          f"is {fetches[day]['fetched_at']}, more than "
                          f"{MAX_LOCK_SNAPSHOT_AGE} old: not the snapshot "
                          "archived at lock")
    daily, row = reader(day, fetches)
    cd = community_daily(daily)
    return {"snapshot": row["file"], "sha256": row["sha256"],
            "fetched_at": row["fetched_at"],
            "zero_point": "yoy4",
            "ranked": yoy4(cd, target_week),
            "shown": persistence_shown(cd, target_week)}


def resolve(target_week, now, fetches=None, reader=read_capture):
    """Card v2 resolution, missing-data policy (1)-(2) and (4).

    Walks the captures from resolution_instant to grace_end, earliest first,
    and resolves on the first whose seven target days all pass the outage
    check. Before resolution_instant: pending. Inside the grace window with no
    passing capture yet: pending. After grace_end with none: cancelled, never
    resolved from a later pull.
    """
    fetches = fetches if fetches is not None else load_fetches()
    start, end = resolution_instant(target_week), grace_end(target_week)
    now = now.astimezone(timezone.utc)
    if now < start:
        return {"status": "pending", "reason": f"resolves at {_iso(start)}"}
    tried = []
    for day in snapshots_between(fetches, start, min(now, end)):
        daily, row = reader(day, fetches)
        cd = community_daily(daily)
        bad = outage_days(cd, target_week)
        total = week_total(cd, target_week)
        if not bad and total is not None:
            out = {"status": "resolved", "value": float(total),
                   "snapshot": row["file"], "sha256": row["sha256"],
                   "fetched_at": row["fetched_at"]}
            in_week = {d.isoformat() for d in week_days(target_week)}
            week_rows = {d: per for d, per in daily.items() if d in in_week}
            unknown = unknown_types(week_rows)
            if unknown:
                out["unknown_types"] = unknown
                week_all = sum(sum(per.values()) for per in week_rows.values())
                if sum(unknown.values()) > 0.01 * week_all:
                    out["status"] = "review"
                    out["reason"] = ("unlisted call types exceed 1% of the "
                                     "week's events: maintainers review before "
                                     "it resolves (card v2 policy (4))")
            return out
        tried.append({"snapshot": row["file"],
                      "outage_days": [d.isoformat() for d in bad]})
    if now > end:
        return {"status": "cancelled", "tried": tried,
                "reason": "no capture passed the outage check by the grace "
                          f"deadline {_iso(end)}"}
    return {"status": "pending", "tried": tried,
            "reason": f"inside the grace window until {_iso(end)}"}


def pilot_growth(week_start, early_day, late_day, fetches=None, reader=read_capture):
    """Pilot gate: the week's count in two captures, and the relative growth.

    `settled` is True when growth from the early to the late capture is below
    PILOT_MAX_GROWTH. A missing capture raises KeyError: card v2 policy (5)
    counts it as no-go, which the caller records.
    """
    fetches = fetches if fetches is not None else load_fetches()
    a = week_total(community_daily(reader(early_day, fetches)[0]), week_start)
    b = week_total(community_daily(reader(late_day, fetches)[0]), week_start)
    if a is None or b is None or a <= 0:
        return {"week": as_date(week_start).isoformat(), "early": a, "late": b,
                "settled": False, "reason": "week incomplete in a capture"}
    g = (b - a) / a
    return {"week": as_date(week_start).isoformat(), "early": a, "late": b,
            "growth": round(g, 6), "settled": abs(g) < PILOT_MAX_GROWTH}


if __name__ == "__main__":                                   # pragma: no cover
    import sys
    if sys.argv[1:2] == ["capture"]:
        try:
            print(json.dumps(capture(), indent=2))
        except Exception as e:                               # noqa: BLE001
            # One line the run log can be read for; the workflow step is
            # continue-on-error, so this never stops the refresh.
            sys.exit(f"seattle_cad capture failed: {type(e).__name__}: {e}")
    elif sys.argv[1:2] == ["reference"] and len(sys.argv) == 3:
        print(json.dumps(lock_reference(sys.argv[2]), indent=2))
    else:
        sys.exit("usage: python -m ssa.adapters.seattle_cad capture | reference <monday>")
