"""NYC 311 weekly service requests: parse, capture, reference, resolution, ingest.

Fixtures and the one committed real capture (sources/nyc311/2026-11-09.json);
nothing here touches the network. The rules under test are the approved card
`nyc-311-requests@v2` (docs/data-cards/nyc-311-requests.json).
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import baselines, capture, provenance               # noqa: E402
from ssa.adapters import nyc311 as ny                        # noqa: E402

PASSED = []
D = date(2026, 12, 14)          # the card's first scored target week
UTC = timezone.utc

# A day's split: the five key agencies plus two others, ~10,000 a day.
SPLIT = {"NYPD": 4300, "HPD": 2300, "DSNY": 900, "DOT": 700, "DEP": 550,
         "DPR": 350, "DOB": 300}
PER_DAY = sum(SPLIT.values())


def ok(fn):
    PASSED.append(fn.__name__)
    print("ok", fn.__name__)


def flat(start, end, scale=1.0):
    out, d = {}, start
    while d <= end:
        out[d.isoformat()] = {a: int(n * scale) for a, n in SPLIT.items()}
        d += timedelta(days=1)
    return out


def set_week(daily, week, per_day):
    for d in ny.week_days(week):
        per = daily[d.isoformat()]
        per["NYPD"] = per_day - (PER_DAY - SPLIT["NYPD"])


def as_body(daily):
    rows = [{"d": f"{d}T00:00:00.000", "agency": a, "n": str(n)}
            for d in sorted(daily) for a, n in sorted(daily[d].items())]
    return json.dumps(rows).encode()


class Resp:
    def __init__(self, status=200, content=b"[]", headers=None):
        self.status_code, self.content = status, content
        self.headers = headers or {}


FRESH = {"X-SODA2-Data-Out-Of-Date": "false",
         "X-SODA2-Truth-Last-Modified": "Sun, 08 Nov 2026 02:40:03 GMT",
         "Last-Modified": "Sun, 08 Nov 2026 02:40:03 GMT"}


class Sandbox:
    """Point the adapter and provenance at a temporary repository root."""

    def __enter__(self):
        self.dir = tempfile.mkdtemp()
        self.saved = (provenance.ROOT, provenance.ARCHIVE, provenance.MANIFEST,
                      ny.ROOT, ny.ARCHIVE, ny.FETCHES)
        provenance.ROOT = ny.ROOT = self.dir
        provenance.ARCHIVE = os.path.join(self.dir, "sources")
        provenance.MANIFEST = os.path.join(provenance.ARCHIVE, "manifest.json")
        ny.ARCHIVE = os.path.join(provenance.ARCHIVE, ny.NAME)
        ny.FETCHES = os.path.join(ny.ARCHIVE, "fetches.json")
        return self

    def __exit__(self, *exc):
        (provenance.ROOT, provenance.ARCHIVE, provenance.MANIFEST,
         ny.ROOT, ny.ARCHIVE, ny.FETCHES) = self.saved
        shutil.rmtree(self.dir)


def row(at, manual=False, out_of_date="false",
        truth="Tue, 29 Dec 2026 02:40:00 GMT", **kw):
    r = {"fetched_at": at, "file": f"sources/nyc311/{at[:10]}.json",
         "sha256": "x" + at, "url": "u", "bytes": 1}
    if manual:
        r.update(manual=True, fresh_by_content=True)
    else:
        r["headers"] = {"x-soda2-data-out-of-date": out_of_date}
        if truth:
            r["headers"]["x-soda2-truth-last-modified"] = truth
    r.update(kw)
    return r


def reader_for(by_day):
    def reader(day, fetches):
        return by_day[day], fetches[day]
    return reader


# ------------------------------------------------------------------ parse

def test_parse_reads_the_grouped_shape_and_counts_every_agency():
    b = json.dumps([{"d": "2026-10-26T00:00:00.000", "agency": "NYPD", "n": "4000"},
                    {"d": "2026-10-26T00:00:00.000", "agency": "HPD", "n": "2000"},
                    {"d": "2026-10-26T00:00:00.000", "agency": "NEWCO", "n": "7"},
                    {"d": "2026-10-26T00:00:00.000", "n": "3"}]).encode()
    daily = ny.parse(b)
    assert ny.totals(daily) == {date(2026, 10, 26): 6010}
    # A new agency code is counted (population A) and reported (policy (6)).
    assert ny.unknown_agencies(daily) == {"NEWCO": 7, "": 3}
    ok(test_parse_reads_the_grouped_shape_and_counts_every_agency)


def test_parse_fails_loud():
    bad = [b"", b"{}", b"[]", b"not json",
           json.dumps([{"d": "2026-10-26T00:00:00.000", "agency": "NYPD", "n": "1"},
                       {"d": "2026-10-26T00:00:00.000", "agency": "NYPD", "n": "2"}]).encode(),
           json.dumps([{"d": "2026-10-26T00:00:00.000", "agency": "NYPD", "n": "-1"}]).encode(),
           json.dumps([{"d": "2026-10-26T00:00:00.000", "agency": "NYPD", "n": "1.5"}]).encode(),
           json.dumps([{"d": "2026-10-26T00:00:00.000", "agency": "NYPD", "n": "1",
                        "complaint_type": "x"}]).encode()]
    for b in bad:
        try:
            ny.parse(b)
        except ValueError:
            continue
        raise AssertionError(f"parse accepted {b[:60]!r}")
    big = json.dumps([{"d": "2026-10-26T00:00:00.000", "agency": f"A{i}", "n": "1"}
                      for i in range(ny.ROW_LIMIT)]).encode()
    try:
        ny.parse(big)
        raise AssertionError("a body at the $limit was accepted")
    except ny.Truncated:
        pass
    ok(test_parse_fails_loud)


# ------------------------------------------------------------------ request

def test_the_request_is_the_cards_one_keyless_grouped_query():
    p = ny.query_params("2026-11-09")
    assert p == {"$select": "date_trunc_ymd(created_date) as d, agency, count(*) as n",
                 "$where": "created_date >= '2026-02-02T00:00:00'",
                 "$group": "d, agency", "$order": "d, agency", "$limit": "50000"}
    assert ny.WINDOW_DAYS == 280 and ny.TIMEOUT == 180
    url = ny.request_url("2026-11-09")
    assert url.startswith("https://data.cityofnewyork.us/resource/erm2-nwe9.json?")
    assert "token" not in url.lower()
    seen = {}

    def get(url, headers, timeout, allow_redirects):
        seen.update(headers=headers, timeout=timeout, redirects=allow_redirects)
        return Resp(headers=FRESH)
    got = ny.fetch_text("2026-11-09", get=get)
    assert "Mozilla" not in seen["headers"]["User-Agent"]
    assert seen["timeout"] == 180 and seen["redirects"] is False
    assert got.headers["x-soda2-data-out-of-date"] == "false"
    assert got.headers["x-soda2-truth-last-modified"] == FRESH["X-SODA2-Truth-Last-Modified"]
    assert got.headers["last-modified"] == FRESH["Last-Modified"]
    calls = []

    def blocked(*a, **k):
        calls.append(1)
        return Resp(status=403)
    try:
        ny.fetch_text("2026-11-09", get=blocked)
        raise AssertionError("a 403 was not raised")
    except Exception as e:                                   # noqa: BLE001
        assert "403" in str(e)
    assert len(calls) == 1, "an HTTP error must not be retried"
    ok(test_the_request_is_the_cards_one_keyless_grouped_query)


def test_a_stale_replica_is_refused_and_never_filed():
    body = as_body(flat(date(2026, 10, 1), date(2026, 11, 6)))
    stale = dict(FRESH, **{"X-SODA2-Data-Out-Of-Date": "true",
                           "X-SODA2-Truth-Last-Modified": "Thu, 05 Nov 2026 02:46:28 GMT"})
    calls = []

    def get(url, headers, timeout, allow_redirects):
        calls.append(url)
        return Resp(content=body, headers=stale)
    with Sandbox():
        t = datetime(2026, 11, 30, 0, 17, tzinfo=UTC)
        try:
            ny.capture(now=t, fetch=lambda day: ny.fetch_text(day, get=get))
            raise AssertionError("a stale body was filed")
        except capture.Stale:
            pass
        assert len(calls) == 1, "a stale answer is not retried in a loop"
        assert not os.path.exists(ny.archive_path("2026-11-30"))
        assert ny.load_fetches() == {}
        # The next scheduled run, answered fresh, files it with its headers.
        r = ny.capture(now=t + timedelta(hours=6), fetch=lambda day: ny.fetch_text(
            day, get=lambda *a, **k: Resp(content=body, headers=FRESH)))
        assert r["headers"]["x-soda2-data-out-of-date"] == "false"
        assert ny.is_fresh(r) and r["newest_full_day"] == "2026-11-06"
    ok(test_a_stale_replica_is_refused_and_never_filed)


def test_capture_is_write_once_per_utc_day():
    calls = []

    def fetch(day, scale=1.0):
        calls.append(day)
        return capture.Fetched(as_body(flat(date(2026, 11, 1), date(2026, 11, 5), scale)),
                               f"u/{day}", {"x-soda2-data-out-of-date": "false"})
    with Sandbox() as s:
        t1 = datetime(2026, 11, 30, 0, 17, tzinfo=UTC)
        r = ny.capture(now=t1, fetch=fetch)
        assert r["file"] == "sources/nyc311/2026-11-30.json"
        first = open(os.path.join(s.dir, r["file"]), "rb").read()
        ny.capture(now=t1 + timedelta(hours=6), fetch=lambda d: fetch(d, 2.0))
        assert calls == ["2026-11-30"]
        assert open(os.path.join(s.dir, r["file"]), "rb").read() == first
        assert provenance.load_manifest()[ny.NAME]["sha256"] == provenance.digest(first)
        with open(os.path.join(s.dir, r["file"]), "ab") as f:
            f.write(b" ")
        try:
            ny.read_capture("2026-11-30")
            raise AssertionError("a changed archive was read")
        except RuntimeError:
            pass
    ok(test_capture_is_write_once_per_utc_day)


# ------------------------------------------------------------------ clock

def test_the_clock_matches_the_card():
    assert ny.lock_instant(D) == datetime(2026, 12, 14, 4, 59, tzinfo=UTC)
    assert ny.resolution_instant(D) == datetime(2026, 12, 29, 17, 0, tzinfo=UTC)
    assert ny.truth_floor(D) == datetime(2026, 12, 28, 23, 0, tzinfo=UTC)
    assert ny.grace_end(D) == datetime(2027, 1, 1, 17, 0, tzinfo=UTC)
    assert ny.manual_capture_day(D) == date(2026, 12, 27)
    assert ny.manual_grace_end(D) == datetime(2026, 12, 30, 17, 0, tzinfo=UTC)
    # EDT: 03:59Z on the Monday.
    assert ny.lock_instant(date(2026, 7, 6)) == datetime(2026, 7, 6, 3, 59, tzinfo=UTC)
    try:
        ny.lock_instant(date(2026, 12, 15))
        raise AssertionError("a Tuesday week was accepted")
    except ValueError:
        pass
    ok(test_the_clock_matches_the_card)


# ------------------------------------------------------------------ outages

def test_the_outage_check_is_forty_percent_or_a_silent_key_agency():
    daily = flat(D - timedelta(days=40), D + timedelta(days=6))
    assert ny.outage_days(daily, D) == []
    low = dict(daily)
    low[D.isoformat()] = {a: int(n * 0.39) for a, n in SPLIT.items()}
    assert ny.outage_days(low, D) == [D]
    kept = dict(daily)
    kept[D.isoformat()] = {a: int(n * 0.41) for a, n in SPLIT.items()}
    assert ny.outage_days(kept, D) == [], "a real 41% day is behaviour"
    for agency in ny.KEY_AGENCIES:
        lost = dict(daily)
        per = dict(SPLIT)
        per.pop(agency)                     # no rows at all for one agency
        lost[(D + timedelta(days=3)).isoformat()] = per
        assert ny.outage_days(lost, D) == [D + timedelta(days=3)], agency
    other = dict(daily)
    per = dict(SPLIT)
    per.pop("DOB")
    other[D.isoformat()] = per
    assert ny.outage_days(other, D) == [], "only the five key agencies gate"
    gone = dict(daily)
    gone.pop((D + timedelta(days=6)).isoformat())
    assert ny.outage_days(gone, D) == [D + timedelta(days=6)]
    ok(test_the_outage_check_is_forty_percent_or_a_silent_key_agency)


# ------------------------------------------------------------------ the reference

def varied(start, end):
    daily = flat(start, end)
    m = start + timedelta(days=(7 - start.weekday()) % 7)
    j = 0
    while m + timedelta(days=6) <= end:
        set_week(daily, m, PER_DAY + (j * 137) % 900)
        m += timedelta(days=7)
        j += 1
    return daily


def test_mean4_is_the_four_settled_weeks_and_never_the_lock_week():
    daily = varied(D - timedelta(days=280), D - timedelta(days=2))
    tot = ny.totals(daily)
    ref = ny.mean4(daily, D)
    want = [D - timedelta(days=7 * k) for k in (5, 4, 3, 2)]
    assert ref["window"] == [w.isoformat() for w in want]
    assert ref["counts"] == [ny.week_total(tot, w) for w in want]
    assert ref["mean"] == round(sum(ref["counts"]) / 4, 2)
    assert ref["method"] == "mean4" and ref["step_back"] == 0
    # The week ending at the lock (D-7..D-1) cannot move it, nor the outcome.
    moved = dict(daily)
    for i in range(1, 8):
        moved[(D - timedelta(days=i)).isoformat()] = {a: n * 3 for a, n in SPLIT.items()}
    assert ny.mean4(moved, D) == ref
    ok(test_mean4_is_the_four_settled_weeks_and_never_the_lock_week)


def test_the_spread_is_the_rms_of_the_same_rule_over_13_settled_weeks():
    daily = varied(D - timedelta(days=280), D - timedelta(days=2))
    tot = ny.totals(daily)
    ref = ny.mean4(daily, D)
    errs = []
    for j in range(2, 15):                                  # W <= D-14
        w = D - timedelta(days=7 * j)
        m = round(sum(ny.week_total(tot, w - timedelta(days=7 * k))
                      for k in (2, 3, 4, 5)) / 4, 2)
        errs.append(ny.week_total(tot, w) - m)
    assert ref["sd"] == round(baselines.rms(errs), 2)
    assert ref["spread"] == "rms_13" and len(ref["spread_targets"]) == 13
    assert ref["spread_targets"][0] == (D - timedelta(days=14)).isoformat()
    ok(test_the_spread_is_the_rms_of_the_same_rule_over_13_settled_weeks)


def test_an_outage_in_the_block_steps_back_once_then_refuses():
    daily = varied(D - timedelta(days=280), D - timedelta(days=2))
    newest = D - timedelta(days=14)
    hit = dict(daily)
    hit[(newest + timedelta(days=2)).isoformat()] = {a: 0 for a in SPLIT}
    ref = ny.mean4(hit, D)
    assert ref["step_back"] == 1
    assert ref["window"] == [(D - timedelta(days=7 * k)).isoformat() for k in (6, 5, 4, 3)]
    # A failing week inside both blocks (D-21): not posted, never a gapped window.
    worse = dict(daily)
    worse[(D - timedelta(days=21)).isoformat()] = {a: 0 for a in SPLIT}
    try:
        ny.mean4(worse, D)
        raise AssertionError("a reference was posted through an outage")
    except ny.NotPostable:
        pass
    ok(test_an_outage_in_the_block_steps_back_once_then_refuses)


def test_the_spread_skips_broken_weeks_and_refuses_below_thirteen():
    daily = varied(D - timedelta(days=280), D - timedelta(days=2))
    hit = dict(daily)
    hit[(D - timedelta(days=7 * 6) + timedelta(days=1)).isoformat()] = {a: 0 for a in SPLIT}
    ref = ny.mean4(hit, D)
    assert (D - timedelta(days=42)).isoformat() not in ref["spread_targets"]
    assert len(ref["spread_targets"]) == 13
    short = {d: p for d, p in daily.items()
             if date.fromisoformat(d) >= D - timedelta(days=7 * 18)}
    try:
        ny.mean4(short, D)
        raise AssertionError("posted with fewer than 13 spread weeks")
    except ny.NotPostable:
        pass
    ok(test_the_spread_skips_broken_weeks_and_refuses_below_thirteen)


def test_the_window_covers_the_deepest_reach():
    # Oldest spread target D-14-7*25, its stepped-back block's oldest week,
    # and that week's 28-day outage lookback, from a capture on the lock day.
    oldest_target = D - timedelta(days=14 + 7 * (ny.MEAN4["spread"]["search"] - 1))
    oldest_week = oldest_target - timedelta(days=7 * (5 + ny.MEAN4["step_back"]))
    reach = oldest_week - timedelta(days=ny.OUTAGE_LOOKBACK_DAYS)
    assert (D - timedelta(days=1)) - timedelta(days=ny.WINDOW_DAYS) <= reach
    # And a capture exactly that long computes it, with every step-back used.
    daily = varied(D - timedelta(days=1 + ny.WINDOW_DAYS), D - timedelta(days=2))
    assert len(ny.mean4(daily, D)["spread_targets"]) == 13
    ok(test_the_window_covers_the_deepest_reach)


def test_the_real_capture_reproduces_the_cards_numbers():
    with open(os.path.join(ROOT, "sources", "nyc311", "fetches.json")) as f:
        fetches = json.load(f)
    r = fetches["2026-11-09"]
    body = open(os.path.join(ROOT, r["file"]), "rb").read()
    assert hashlib.sha256(body).hexdigest() == r["sha256"]
    assert ny.is_fresh(r), "the committed capture was served fresh"
    daily = ny.parse(body)
    tot = ny.totals(daily)
    # Card v2, scoring: the weeks 2026-09-07 to 10-26.
    want = [77025, 76778, 73002, 79179, 75346, 76445, 82210, 80677]
    got = [ny.week_total(tot, date(2026, 9, 7) + timedelta(days=7 * i)) for i in range(8)]
    assert got == want, got
    assert ny.newest_full_day(daily) == date(2026, 11, 6)
    assert ny.unknown_agencies(daily) == {}
    # The lock of 2026-11-08 23:59 ET falls 3 h after this capture.
    ref = ny.lock_reference("2026-11-09", fetches=fetches)
    assert ref["ranked"]["counts"] == want[-4:]
    assert ref["ranked"]["mean"] == sum(want[-4:]) / 4
    assert ref["ranked"]["sd"] > 0 and len(ref["ranked"]["spread_targets"]) == 13
    assert ref["shown"]["persistence_settled"]["mean"] == 80677
    # yoy4 needs a year-earlier block the card's 280-day window lacks.
    assert "not_postable" in ref["shown"]["yoy4"]
    ok(test_the_real_capture_reproduces_the_cards_numbers)


# ------------------------------------------------------------------ lock

def test_the_lock_reference_reads_the_last_fresh_capture_only():
    daily = varied(D - timedelta(days=281), D - timedelta(days=2))
    lock = "2026-12-14T04:59:00Z"
    f = {"2026-12-13": row("2026-12-13T18:17:00Z"),
         "2026-12-14": row("2026-12-14T00:17:00Z", out_of_date="true")}
    rd = reader_for({"2026-12-13": daily, "2026-12-14": daily})
    ref = ny.lock_reference(D, fetches=f, reader=rd)
    assert ref["fetched_at"] == "2026-12-13T18:17:00Z", "the flagged one is skipped"
    assert ref["mode"] == "auto" and ref["zero_point"] == "mean4"
    # Nothing fresh in the 24 h before the lock: not posted.
    old = {"2026-12-13": row("2026-12-13T00:17:00Z")}     # 28 h before
    for fx in (old, {"2026-12-14": row("2026-12-14T00:17:00Z", out_of_date="true")},
               {"2026-12-14": row("2026-12-14T00:17:00Z", out_of_date="")},
               {"2026-12-14": row("2026-12-14T05:17:00Z")}):
        try:
            ny.lock_reference(D, fetches=fx, reader=reader_for({k: daily for k in fx}))
            raise AssertionError(f"posted from {fx}")
        except ny.NotPostable:
            pass
    man = {"2026-12-13": row("2026-12-13T18:00:00Z", manual=True)}
    assert ny.lock_reference(D, fetches=man, reader=reader_for(
        {"2026-12-13": daily}))["mode"] == "manual"
    assert lock == capture.iso(ny.lock_instant(D))
    ok(test_the_lock_reference_reads_the_last_fresh_capture_only)


# ------------------------------------------------------------------ resolution

def test_resolution_takes_the_first_fresh_capture_after_tuesday_noon():
    good = varied(D - timedelta(days=60), D + timedelta(days=13))
    f = {"2026-12-29": row("2026-12-29T00:17:00Z"),          # before Tue 12:00 ET
         "2026-12-30": row("2026-12-30T00:17:00Z"),
         "2026-12-31": row("2026-12-31T00:17:00Z")}
    rd = reader_for({k: good for k in f})
    assert ny.resolve(D, datetime(2026, 12, 29, 16, 0, tzinfo=UTC),
                      fetches=f, reader=rd)["status"] == "pending"
    r = ny.resolve(D, datetime(2026, 12, 31, 12, 0, tzinfo=UTC), fetches=f, reader=rd)
    assert r["status"] == "resolved" and r["fetched_at"] == "2026-12-30T00:17:00Z"
    assert r["value"] == float(ny.week_total(ny.totals(good), D)) and r["mode"] == "auto"
    # A capture whose truth predates Monday 18:00 ET, a flagged one, and a
    # manual one are not the auto record.
    f2 = dict(f)
    f2["2026-12-30"] = row("2026-12-30T00:17:00Z", truth="Mon, 28 Dec 2026 02:40:00 GMT")
    f2["2026-12-30b"] = row("2026-12-30T01:17:00Z", out_of_date="true")
    f2["2026-12-30c"] = row("2026-12-30T02:17:00Z", manual=True)
    rd2 = reader_for({k: good for k in f2})
    r = ny.resolve(D, datetime(2026, 12, 31, 12, 0, tzinfo=UTC), fetches=f2, reader=rd2)
    assert r["fetched_at"] == "2026-12-31T00:17:00Z"
    ok(test_resolution_takes_the_first_fresh_capture_after_tuesday_noon)


def test_an_outage_waits_inside_the_grace_window_then_cancels():
    good = varied(D - timedelta(days=60), D + timedelta(days=13))
    broken = dict(good)
    broken[(D + timedelta(days=2)).isoformat()] = {a: 0 for a in SPLIT}
    f = {"2026-12-30": row("2026-12-30T00:17:00Z"),
         "2026-12-31": row("2026-12-31T00:17:00Z")}
    rd = reader_for({"2026-12-30": broken, "2026-12-31": good})
    r = ny.resolve(D, datetime(2026, 12, 31, 12, 0, tzinfo=UTC), fetches=f, reader=rd)
    assert r["status"] == "resolved" and r["fetched_at"] == "2026-12-31T00:17:00Z"
    rd = reader_for({"2026-12-30": broken, "2026-12-31": broken})
    assert ny.resolve(D, datetime(2026, 12, 31, 12, 0, tzinfo=UTC),
                      fetches=f, reader=rd)["status"] == "pending"
    late = dict(f, **{"2027-01-02": row("2027-01-02T00:17:00Z")})
    rd = reader_for({"2026-12-30": broken, "2026-12-31": broken, "2027-01-02": good})
    r = ny.resolve(D, datetime(2027, 1, 3, tzinfo=UTC), fetches=late, reader=rd)
    assert r["status"] == "cancelled", "never resolved from a later pull"
    ok(test_an_outage_waits_inside_the_grace_window_then_cancels)


def test_manual_mode_resolves_on_the_sunday_download_until_wednesday():
    good = varied(D - timedelta(days=60), D + timedelta(days=11))
    f = {"2026-12-27": row("2026-12-27T18:00:00Z", manual=True),
         "2026-12-28": row("2026-12-28T00:17:00Z")}            # auto: not this mode
    rd = reader_for({k: good for k in f})
    r = ny.resolve(D, datetime(2026, 12, 28, 12, 0, tzinfo=UTC), mode="manual",
                   fetches=f, reader=rd)
    assert r["status"] == "resolved" and r["mode"] == "manual"
    assert r["fetched_at"] == "2026-12-27T18:00:00Z"
    early = {"2026-12-26": row("2026-12-26T18:00:00Z", manual=True)}
    assert ny.resolve(D, datetime(2026, 12, 31, tzinfo=UTC), mode="manual",
                      fetches=early, reader=reader_for({"2026-12-26": good})
                      )["status"] == "cancelled"
    try:
        ny.resolve(D, datetime(2026, 12, 31, tzinfo=UTC), mode="both", fetches=f)
        raise AssertionError("an unknown mode was accepted")
    except ValueError:
        pass
    ok(test_manual_mode_resolves_on_the_sunday_download_until_wednesday)


def test_the_fillin_confirmation_compares_plus_8_and_plus_11():
    W = date(2026, 11, 23)                        # the card's confirmation week
    base = varied(W - timedelta(days=60), W + timedelta(days=10))
    grown = json.loads(json.dumps(base))
    grown[W.isoformat()]["NYPD"] += 300
    f = {"2026-12-09": row("2026-12-09T00:17:00Z", truth="Tue, 08 Dec 2026 02:40:00 GMT"),
         "2026-12-12": row("2026-12-12T00:17:00Z", truth="Fri, 11 Dec 2026 02:40:00 GMT")}
    res = ny.fillin_check(W, fetches=f, reader=reader_for({"2026-12-09": base,
                                                           "2026-12-12": grown}))
    assert res["early"] == "2026-12-09" and res["late"] == "2026-12-12"
    assert res["settled"] and 0 < res["growth"] < ny.FILLIN_MAX_GROWTH
    big = json.loads(json.dumps(base))
    big[W.isoformat()]["NYPD"] += 5000
    res = ny.fillin_check(W, fetches=f, reader=reader_for({"2026-12-09": base,
                                                           "2026-12-12": big}))
    assert not res["settled"]
    assert not ny.fillin_check(W, fetches={k: f[k] for k in ["2026-12-09"]},
                               reader=reader_for({"2026-12-09": base}))["settled"]
    ok(test_the_fillin_confirmation_compares_plus_8_and_plus_11)


# ------------------------------------------------------------------ manual ingest

def sunday_body(day, **kw):
    """The card's request for Sunday `day`: day-280 through Friday in full,
    Saturday a two-hour stub."""
    daily = varied(day - timedelta(days=ny.WINDOW_DAYS), day - timedelta(days=2))
    daily[(day - timedelta(days=1)).isoformat()] = {"NYPD": 300, "HPD": 200, "DOT": 57}
    return daily


def test_ingest_files_a_fresh_sunday_download_and_refuses_the_rest():
    sun = date(2026, 12, 13)
    with Sandbox() as s:
        def put(daily, name="dl.json"):
            p = os.path.join(s.dir, name)
            with open(p, "wb") as fh:
                fh.write(as_body(daily))
            return p
        now = datetime(2026, 12, 13, 18, 0, tzinfo=UTC)      # 13:00 ET Sunday
        # A stale replica three days behind: newest full day is Tuesday.
        stale = {d: p for d, p in sunday_body(sun).items()
                 if date.fromisoformat(d) <= sun - timedelta(days=5)}
        for bad, why in ((stale, "not fresh"),
                         ({d: p for d, p in sunday_body(sun).items()
                           if d != (sun - timedelta(days=100)).isoformat()}, "missing"),
                         (sunday_body(sun + timedelta(days=1)), "starts")):
            try:
                ny.ingest_manual(put(bad), capture_day=sun.isoformat(), now=now)
                raise AssertionError(f"filed a body that is {why}")
            except ValueError as e:
                assert why in str(e), e
        assert not os.path.exists(ny.archive_path(sun))
        out = ny.ingest_manual(put(sunday_body(sun)), now=now)
        assert out["created"] and out["archive"] == "sources/nyc311/2026-12-13.json"
        r = ny.load_fetches()["2026-12-13"]
        assert r["manual"] and r["fresh_by_content"] and ny.is_fresh(r)
        assert r["newest_full_day"] == "2026-12-11"
        assert r["url"] == ny.request_url(sun)
        # Same bytes again: a no-op. Different bytes: refused. Back-dated: refused.
        assert not ny.ingest_manual(put(sunday_body(sun)), now=now)["created"]
        other = sunday_body(sun)
        other[(sun - timedelta(days=9)).isoformat()]["DPR"] += 1
        try:
            ny.ingest_manual(put(other), capture_day=sun.isoformat(), now=now)
            raise AssertionError("a second, different file for the day was filed")
        except ValueError as e:
            assert "different bytes" in str(e), e
        prev = sun - timedelta(days=7)
        try:
            ny.ingest_manual(put(sunday_body(prev)), capture_day=prev.isoformat(),
                             now=now)
            raise AssertionError("a back-dated capture was filed")
        except ValueError as e:
            assert "predates" in str(e), e
        # The filed body sets that night's lock reference.
        ref = ny.lock_reference(D)
        assert ref["mode"] == "manual" and ref["snapshot"] == out["archive"]
    ok(test_ingest_files_a_fresh_sunday_download_and_refuses_the_rest)


def test_ingest_reports_an_outage_without_refusing_it():
    sun = date(2026, 12, 13)
    daily = sunday_body(sun)
    daily[(D - timedelta(days=14) + timedelta(days=3)).isoformat()] = {"DPR": 9000}
    rep = ny.ingest_check(as_body(daily), sun)
    assert rep["fresh_by_content"]
    # The Sunday file's D-14 reference week is also the week it resolves.
    assert rep["outage_days"] == {"reference week 2026-11-30": ["2026-12-03"],
                                  "resolution week 2026-11-30": ["2026-12-03"]}
    ok(test_ingest_reports_an_outage_without_refusing_it)


def test_the_ingest_tool_prints_the_url_for_the_capture_day():
    out = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "ingest_nyc311.py"),
                          "--url", "--capture-day", "2026-12-13"],
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out == ny.request_url("2026-12-13")
    ok(test_the_ingest_tool_prints_the_url_for_the_capture_day)


# ------------------------------------------------------------------ wiring

def test_the_refresh_captures_daily_and_never_blocks_on_it():
    import yaml
    with open(os.path.join(ROOT, ".github", "workflows", "refresh.yml")) as f:
        wf = yaml.safe_load(f)
    steps = [st for job in wf["jobs"].values() for st in job.get("steps", [])]
    runs = [st.get("run", "") for st in steps]
    i = runs.index("python -m ssa.adapters.nyc311 capture")
    assert steps[i].get("continue-on-error") is True
    assert i < runs.index("python -m ssa.refresh"), "capture after the refresh"
    ok(test_the_refresh_captures_daily_and_never_blocks_on_it)


def test_the_monday_retries_land_before_the_lock_and_only_capture():
    import yaml
    with open(os.path.join(ROOT, ".github", "workflows", "nyc311-capture.yml")) as f:
        wf = yaml.safe_load(f)
    on = wf.get("on", wf.get(True))
    (cron,) = [c["cron"] for c in on["schedule"]]
    minute, hours, _, _, dow = cron.split()
    assert dow == "1", "Mondays (UTC), the day of the lock capture"
    edt_lock = ny.lock_instant(date(2026, 7, 6))
    for h in hours.split(","):
        # 20 minutes of cron drift still lands before the EDT lock (03:59Z).
        at = datetime(2026, 7, 6, int(h), int(minute), tzinfo=UTC) + timedelta(minutes=20)
        assert at < edt_lock, cron
    steps = wf["jobs"]["capture"]["steps"]
    cap = [st for st in steps if st.get("run") == "python -m ssa.adapters.nyc311 capture"]
    assert cap and cap[0].get("continue-on-error") is True
    commit = [st["run"] for st in steps if "git add" in st.get("run", "")][0]
    assert "git add -A sources/nyc311 sources/manifest.json" in commit
    assert wf["concurrency"]["group"] == "refresh-data"
    ok(test_the_monday_retries_land_before_the_lock_and_only_capture)


def test_the_card_is_the_approved_revision_byte_for_byte():
    p = os.path.join(ROOT, "docs", "data-cards", "nyc-311-requests.json")
    raw = open(p, "rb").read()
    assert hashlib.sha256(raw).hexdigest() == ny.CARD_SHA256
    card = json.loads(raw)
    assert card["status"] == "approved" and card["revision"] == 2
    assert ny.CARD == f"{card['id']}@v{card['revision']}"
    ok(test_the_card_is_the_approved_revision_byte_for_byte)


def test_the_proposed_rounds_match_the_adapter():
    p = os.path.join(ROOT, "docs", "data-cards", "nyc-311-requests.rounds.json")
    doc = json.load(open(p))
    rounds = doc["rounds"]
    assert rounds[0]["lock_at"] == "2026-12-14T04:59:00Z", "first scored lock 12-13 ET"
    for r in rounds:
        week = date.fromisoformat(r["round_id"][len("nyc311-"):]) - timedelta(days=6)
        assert r["lock_at"] == capture.iso(ny.lock_instant(week))
        assert r["release_at"] == capture.iso(ny.resolution_instant(week))
        assert baselines.normalize_spec(r["zero_point"]) == \
            baselines.normalize_spec(ny.MEAN4)
        assert r["target_type"] == "continuous_normal"
        assert "erm2-nwe9" in r["question"] and "sources/nyc311/" in r["resolve"]
    assert "Christmas" in rounds[1]["question"]
    ok(test_the_proposed_rounds_match_the_adapter)


def test_mean_k_step_back_is_closed_and_absent_by_default():
    spec = {"name": "m", "family": "mean_k", "period": "week", "k": 2,
            "spread": {"rule": "rms", "n": 2}}
    assert "step_back" not in baselines.normalize_spec(spec)
    for bad in (dict(spec, step_back=-1), dict(spec, step_back=1, search=3)):
        try:
            baselines.normalize_spec(bad)
            raise AssertionError(f"accepted {bad}")
        except ValueError:
            pass
    assert baselines.normalize_spec(dict(spec, step_back=0, search=3))["search"] == 3
    ok(test_mean_k_step_back_is_closed_and_absent_by_default)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"{len(PASSED)} passed")
