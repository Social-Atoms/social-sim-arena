"""BLS CPI: rent of primary residence, US city average, seasonally adjusted.

  POST https://api.bls.gov/publicAPI/v1/timeseries/data/
       {"seriesid": ["CUSR0000SEHA"], "startyear": "<Y-3>", "endyear": "<Y>"}

The keyless BLS Public Data API, version 1: no key, no account, 25 queries a
day, up to 10 years per query. One query returns the monthly index levels of
CUSR0000SEHA (CPI-U, rent of primary residence, US city average, SA). The
arena scores the one-month change, 100 x (L_M / L_{M-1} - 1), computed from
the two index levels **in the same response** and never rounded. The rules
are the approved Data Card `bls-cpi-rent@v2`
(docs/data-cards/bls-cpi-rent.json, sha256
9a5c42d2fc293ea433cd78590908b95dc017a7b76817ff6fbd2bdfa7b390d4e7); this
module implements them and nothing else.

**Why both levels from one response.** Every February BLS re-estimates five
years of seasonal factors, so the M-1 level known at the lock and the M-1
level in the release that prints M can differ; a change built across two
vintages would mix two seasonal factors.

**The archive is a stamped capture, write-once per capture**, under
`sources/bls_cpi/<YYYY-MM-DDTHHMMZ>.json`: the body exactly as served, plus
`<stamp>.meta.json` beside it (fetch instant, URL, the request JSON, sha256,
size, the newest period inside). One sidecar per capture rather than one
`fetches.json`, because two workflows write here -- the six-hourly refresh
and the release-day capture -- and a file both of them rewrite is a rebase
conflict that would cost the refresh its data commit.

**When it asks.** At most one capture per UTC day, except on a CPI release
day: from the release instant (08:30 America/New_York) on, a capture is taken
until one holds a newer month than the day's pre-release capture. The
6-hourly refresh cannot be at 08:45 ET, so `.github/workflows/bls-cpi.yml`
runs the same command at 12:45 and 13:45 UTC (08:45 ET in daylight and in
standard time); on any other day the command finds the day's capture held and
sends nothing. The card's point: a daily capture alone may predate the
release on release day (the SCE archive did, sce-capture-bug.md). MAX_PER_DAY
keeps even a misbehaving schedule far below the API's 25.

**Resolution** (`resolution`): the first archived capture at or after the
release instant whose newest CUSR0000SEHA period is M, the change computed
from L_M and L_{M-1} in that same body. A month BLS did not publish (October
2025, footnote code X, value "-") has no level, so neither it nor the month
after it has a change: such a round is cancelled, never built from a two-month
change. A release delayed past release + 45 days cancels the round.

**The reference** is `zero_point` mean6 (baselines.mean_k, k = 6, rms_12
spread), computed by refresh from the history frozen at lock; `lock_capture`
is the card's freshness rule on top: a capture from the 24 hours before the
lock must carry M-1, or the round posts no reference.

**Access.** www.bls.gov and download.bls.gov answered 403 to an honest client
from the research sandbox (card evidence); the API answered 200. Whether it
answers GitHub's runners is unmeasured: a 403 or 429 is raised, never retried,
and means this source needs the local courier (lesson 3). Rights: a work of
the US government (17 U.S.C. section 105), inventory row `bls_cpi`. Cite
"Source: U.S. Bureau of Labor Statistics".
"""
import json
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from .. import capture as archive_store
from .. import provenance

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NAME = "bls_cpi"
ARCHIVE = os.path.join(ROOT, "sources", NAME)
SEASON = os.path.join(ROOT, "questions", "season0.json")

URL = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
SERIES_ID = "CUSR0000SEHA"
TRACKER = "bls_cpi"
NY = ZoneInfo("America/New_York")
RELEASE_LOCAL = (8, 30)
TIMEOUT = archive_store.TIMEOUT

# Request years: Y-3 to Y. The mean6 reference with its rms_12 spread reaches
# back at most 8 + 14 months plus one level for the first change, about two
# years; three give a year's slack across the 2025 gap.
YEARS_BACK = 3
MAX_PER_DAY = 6
# How far the newest month may trail the day the series is served: a month's
# CPI lands mid-following-month, so 2 is ordinary life; 4 means the archive or
# the API has stopped moving.
MAX_STALE_MONTHS = 4
# Card, missing-data rules (1) and (4).
DELAY_LIMIT_DAYS = 45
LOCK_FRESH_HOURS = 24
# Index levels: about 450 in 2026; a value far outside this is a misread.
MIN_LEVEL, MAX_LEVEL = 50.0, 5000.0

# CPI release dates by reference month, America/New_York, 08:30. From the card
# (bls-cpi-rent@v2, Lock): 2026-12-10 agrees across third-party calendars and
# the owner doc but is unverified against bls.gov, which answered 403; the
# 2027 dates are the card's estimates until a person copies them from
# https://www.bls.gov/schedule/news_release/cpi.htm (card task G4). A round in
# the season with tracker bls_cpi adds its own release day to these.
# Copied from https://www.bls.gov/schedule/news_release/cpi.htm and
# https://www.bls.gov/schedule/2027/home.htm on 2026-11-09 (both 200 to an honest UA).
RELEASES = {
    "2026-11": "2026-12-10",
    "2026-12": "2027-01-13",
    "2027-01": "2027-02-11",
    "2027-02": "2027-03-10",
    "2027-03": "2027-04-13",
    "2027-04": "2027-05-12",
    "2027-05": "2027-06-10",
    "2027-06": "2027-07-14",
    "2027-07": "2027-08-11",
    "2027-08": "2027-09-14",
    "2027-09": "2027-10-14",
    "2027-10": "2027-11-10",
    "2027-11": "2027-12-10",
}

_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{4}Z$")
_PERIOD = re.compile(r"^M(0[1-9]|1[0-2])$")


# --- fetch and parse -----------------------------------------------------------

def request_body(today):
    y = int(str(today)[:4])
    return {"seriesid": [SERIES_ID], "startyear": str(y - YEARS_BACK),
            "endyear": str(y)}


def fetch_text(today, post=requests.post):
    """One honest POST through capture.get. Returns (Fetched, request JSON)."""
    payload = request_body(today)

    def via_post(url, headers=None, timeout=None, allow_redirects=False):
        h = dict(headers or {})
        h["Content-Type"] = "application/json"
        return post(url, data=json.dumps(payload), headers=h, timeout=timeout,
                    allow_redirects=allow_redirects)

    got = archive_store.get(URL, name=NAME, accept="application/json",
                            timeout=TIMEOUT, get=via_post)
    return got, payload


def parse(body):
    """Response bytes -> {"levels": {"YYYY-MM": float or None}, "newest": ..,
    "missing": {"YYYY-MM": footnote text}} for CUSR0000SEHA.

    Raises on anything but REQUEST_SUCCEEDED, a response without the series,
    a period or level it cannot read, or a duplicate month. A value of "-" is
    BLS saying the month was not published (October 2025): kept as None with
    its footnote, never as a number.
    """
    try:
        doc = json.loads(body)
    except ValueError:
        raise RuntimeError(f"{NAME}: the API answered something that is not "
                           f"JSON (starts {bytes(body[:40])!r})")
    if doc.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"{NAME}: API status {doc.get('status')!r}: "
                           f"{'; '.join(doc.get('message') or [])[:300]}")
    series = [s for s in (doc.get("Results") or {}).get("series") or []
              if s.get("seriesID") == SERIES_ID]
    if len(series) != 1:
        raise RuntimeError(f"{NAME}: the response carries {len(series)} "
                           f"{SERIES_ID} series, not one")
    levels, missing = {}, {}
    for p in series[0].get("data") or []:
        period = p.get("period", "")
        if period == "M13":
            continue                                  # annual average
        if not _PERIOD.match(period):
            raise RuntimeError(f"{NAME}: unreadable period {period!r}")
        month = f"{int(p['year']):04d}-{period[1:]}"
        if month in levels:
            raise RuntimeError(f"{NAME}: two values for {month}")
        raw = str(p.get("value", "")).strip()
        if raw == "-":
            levels[month] = None
            missing[month] = "; ".join(
                f.get("text", "") for f in p.get("footnotes") or []
                if f and f.get("text"))
            continue
        try:
            v = float(raw)
        except ValueError:
            raise RuntimeError(f"{NAME}: {month} holds {raw!r}, not an index "
                               "level")
        if not MIN_LEVEL < v < MAX_LEVEL:
            raise RuntimeError(f"{NAME}: {month} level {v} is not a CPI index")
        levels[month] = v
    published = sorted(m for m, v in levels.items() if v is not None)
    if len(published) < 12:
        raise RuntimeError(f"{NAME}: only {len(published)} published months; "
                           "refusing a truncated response")
    return {"levels": dict(sorted(levels.items())), "newest": published[-1],
            "missing": missing}


def _month_before(month):
    y, m = int(month[:4]), int(month[5:7])
    i = y * 12 + (m - 1) - 1
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def change(levels, month):
    """100 x (L_M / L_{M-1} - 1) from one response's levels, or None when
    either level is missing (the card cancels such a round)."""
    a, b = levels.get(month), levels.get(_month_before(month))
    if a is None or b is None:
        return None
    return 100.0 * (a / b - 1.0)


def changes(parsed):
    """[{date: "YYYY-MM-01", value}] oldest first, every month with a change;
    a month without one (no level, or no level the month before) is skipped,
    not filled -- the mean6 reference then reaches past it (card, Baseline)."""
    out = []
    for month in parsed["levels"]:
        c = change(parsed["levels"], month)
        if c is not None:
            out.append({"date": month + "-01", "value": c})
    return out


# --- the archive ---------------------------------------------------------------

def _stamp(now):
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H%MZ")


def body_path(stamp):
    return os.path.join(ARCHIVE, f"{stamp}.json")


def meta_path(stamp):
    return os.path.join(ARCHIVE, f"{stamp}.meta.json")


def captures():
    """Every capture's meta row, oldest first by fetch instant."""
    if not os.path.isdir(ARCHIVE):
        return []
    rows = []
    for fn in os.listdir(ARCHIVE):
        if fn.endswith(".meta.json") and _STAMP.match(fn[:-10]):
            with open(os.path.join(ARCHIVE, fn)) as f:
                rows.append(json.load(f))
    return sorted(rows, key=lambda r: r["fetched_at"])


def read(row):
    """(body bytes, parsed) for one capture; refuses a changed file."""
    with open(os.path.join(ROOT, row["file"]), "rb") as f:
        body = f.read()
    if provenance.digest(body) != row["sha256"]:
        raise RuntimeError(f"{NAME}: {row['file']} does not match its recorded "
                           "sha256; refusing a changed archive")
    return body, parse(body)


def release_instants(season_path=None):
    """{UTC day: release instant} from RELEASES and any bls_cpi round."""
    days = set(RELEASES.values())
    try:
        with open(season_path or SEASON) as f:
            rounds = json.load(f).get("rounds", [])
    except (OSError, ValueError):
        rounds = []
    for r in rounds:
        if r.get("tracker") == TRACKER and r.get("release_at"):
            days.add(r["release_at"][:10])
    out = {}
    for d in days:
        local = datetime.combine(date.fromisoformat(d), time(*RELEASE_LOCAL), NY)
        out[d] = local.astimezone(timezone.utc)
    return out


def due(now, rows, releases):
    """(True, why) when a capture should be taken now, else (False, why)."""
    day = now.date().isoformat()
    today = [r for r in rows if r["fetched_at"][:10] == day]
    if not today:
        return True, "no capture yet today"
    if len(today) >= MAX_PER_DAY:
        return False, f"{len(today)} captures today, the daily cap"
    release = releases.get(day)
    if release is None:
        return False, "today's capture is held"
    if now < release:
        return False, f"release day; the release is at {archive_store.iso(release)}"
    before = [r for r in today if archive_store.parse_iso(r["fetched_at"]) < release]
    after = [r for r in today if archive_store.parse_iso(r["fetched_at"]) >= release]
    held = max((r["newest"] for r in before), default="")
    if any(r["newest"] > held for r in after):
        return False, "the release-day capture holding the new month is held"
    return True, "release day, no post-release capture with the new month yet"


def capture(now=None, fetch=fetch_text, releases=None, record=True):
    """Take a capture if one is due. Returns the meta row, or {"skipped": why}.

    The body is parsed before it is filed, so a malformed answer fails the run
    instead of entering the record. `record=False` leaves the provenance
    manifest alone (the release-day workflow restores it anyway, so its
    commit never touches a file the refresh also writes).
    """
    now = (now or archive_store.utcnow()).astimezone(timezone.utc)
    rows = captures()
    ok, why = due(now, rows, release_instants() if releases is None else releases)
    if not ok:
        return {"skipped": why}
    stamp = _stamp(now)
    if os.path.exists(body_path(stamp)) or os.path.exists(meta_path(stamp)):
        return {"skipped": f"{stamp} is already held"}
    got, payload = fetch(now.date().isoformat())
    body = got[0]
    parsed = parse(body)
    os.makedirs(ARCHIVE, exist_ok=True)
    if record:
        block = provenance.record(NAME, URL, body, fetched_at=archive_store.iso(now),
                                  ext="json", day=stamp)
        sha, size, rel = block["sha256"], block["bytes"], block["file"]
    else:
        path = body_path(stamp)
        with open(path + ".tmp", "wb") as f:
            f.write(body)
        os.replace(path + ".tmp", path)
        sha, size = provenance.digest(body), len(body)
        rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
    row = {"fetched_at": archive_store.iso(now), "url": URL, "request": payload,
           "sha256": sha, "bytes": size, "file": rel,
           "newest": parsed["newest"], "why": why}
    headers = getattr(got, "headers", None)
    if headers:
        row["headers"] = dict(headers)
    with open(meta_path(stamp) + ".tmp", "w") as f:
        json.dump(row, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(meta_path(stamp) + ".tmp", meta_path(stamp))
    return row


# --- the series, the lock rule, the resolution ----------------------------------

def _utc_today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _month_index(iso):
    return int(iso[:4]) * 12 + int(iso[5:7])


def history(today=None):
    """The one-month changes from the newest archived capture. Never the
    network: capturing is `capture`'s job, so a 403 on the runner can never
    take the refresh down with it. Raises on an empty archive or on a newest
    month more than MAX_STALE_MONTHS behind `today`."""
    rows = captures()
    if not rows:
        raise RuntimeError(f"{NAME}: nothing archived under "
                           f"{os.path.relpath(ARCHIVE, ROOT)}; run "
                           "`python -m ssa.adapters.bls_cpi capture`")
    _, parsed = read(rows[-1])
    out = changes(parsed)
    stale = _month_index(today or _utc_today()) - _month_index(out[-1]["date"])
    if stale > MAX_STALE_MONTHS:
        raise RuntimeError(
            f"{NAME}: the newest CPI rent month is {out[-1]['date'][:7]}, "
            f"{stale} months behind today -- over the {MAX_STALE_MONTHS} allowed")
    return out


def reference_month(release_at):
    """The month a release reports: CPI for M is released in M+1."""
    return _month_before(release_at[:7])


def lock_capture(lock_at, month, rows=None):
    """The capture the reference stands on, or a raised NotPostable reason.

    Card, missing-data rule (4): a capture from the 24 hours before the lock
    must carry M-1 as its newest month."""
    lock = archive_store.parse_iso(lock_at)
    start = lock - timedelta(hours=LOCK_FRESH_HOURS)
    rows = captures() if rows is None else rows
    near = [r for r in rows
            if start <= archive_store.parse_iso(r["fetched_at"]) <= lock]
    prev = _month_before(month)
    good = [r for r in near if r["newest"] == prev]
    if not good:
        from ..baselines import NotPostable
        raise NotPostable(
            f"no {NAME} capture between {archive_store.iso(start)} and "
            f"{lock_at} carries {prev} (captures in the window: "
            f"{[r['newest'] for r in near] or 'none'}); the round posts no "
            "reference")
    return good[-1]


def resolution(month, release_at, now=None, rows=None):
    """The card's resolution for reference month `month` ("YYYY-MM").

    Returns {"value", "month", "level", "level_prev", "file", "sha256",
    "fetched_at"} from the first capture at or after the release whose newest
    period is M. Raises ValueError -- the round waits, or names why it is
    cancelled -- otherwise.
    """
    now = (now or archive_store.utcnow()).astimezone(timezone.utc)
    release = archive_store.parse_iso(release_at)
    rows = captures() if rows is None else rows
    for row in rows:
        if archive_store.parse_iso(row["fetched_at"]) < release:
            continue
        if row["newest"] < month:
            continue
        if row["newest"] > month:
            raise ValueError(
                f"{row['file']} is the first capture after the release with a "
                f"month past {month}, and its newest is {row['newest']}: no "
                "archived capture holds the release that first printed it")
        _, parsed = read(row)
        value = change(parsed["levels"], month)
        if value is None:
            raise ValueError(
                f"{month}: {_month_before(month)} has no published level in "
                f"{row['file']}, so no one-month change exists; the round is "
                "cancelled (card, missing-data rule 3)")
        return {"value": value, "month": month,
                "level": parsed["levels"][month],
                "level_prev": parsed["levels"][_month_before(month)],
                "file": row["file"], "sha256": row["sha256"],
                "fetched_at": row["fetched_at"]}
    deadline = release + timedelta(days=DELAY_LIMIT_DAYS)
    if now > deadline:
        raise ValueError(
            f"no {NAME} capture carries {month} by {deadline:%Y-%m-%d}, release "
            f"+ {DELAY_LIMIT_DAYS} days: the round is cancelled (card, "
            "missing-data rules 1-2)")
    raise ValueError(f"no archived {NAME} capture at or after {release_at} "
                     f"carries {month} yet; the round waits")


if __name__ == "__main__":                                   # pragma: no cover
    import sys
    args = sys.argv[1:]
    if args[:1] == ["capture"]:
        try:
            print(json.dumps(capture(record="--no-manifest" not in args),
                             indent=2))
        except Exception as e:                               # noqa: BLE001
            # One line the run log can be read for; both workflow steps are
            # continue-on-error, so this never stops anything else.
            sys.exit(f"{NAME} capture failed: {type(e).__name__}: {e}")
    elif args[:1] == ["resolve"] and len(args) == 3:
        print(json.dumps(resolution(args[1], args[2]), indent=2))
    else:
        sys.exit("usage: python -m ssa.adapters.bls_cpi capture [--no-manifest]"
                 " | resolve <YYYY-MM> <release_at>")
