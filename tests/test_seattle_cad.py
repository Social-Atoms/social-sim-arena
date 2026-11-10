"""Seattle community-initiated police calls: parse, reference, resolution.

Fixtures only; nothing here touches the network. The rules under test are the
approved card `seattle-community-calls@v2` (docs/data-cards/).
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import provenance                                   # noqa: E402
from ssa.adapters import seattle_cad as sc                   # noqa: E402

PASSED = []
D = date(2026, 12, 14)          # the card's first scored target week


def ok(fn):
    PASSED.append(fn.__name__)
    print("ok", fn.__name__)


def body(rows):
    return json.dumps([{"d": f"{d}T00:00:00.000", "call_type": t, "n": str(n)}
                       for d, t, n in rows]).encode()


def flat_daily(start, end, per_day=700, onview=200):
    """A grouped-shape dict with constant counts: 911 carries the community
    total (minus a fixed phone share), ONVIEW is excluded noise."""
    out, d = {}, start
    while d <= end:
        out[d.isoformat()] = {"911": per_day - 300, "TELEPHONE OTHER, NOT 911": 300,
                              "ONVIEW": onview, "ALARM CALL (NOT POLICE ALARM)": 5}
        d += timedelta(days=1)
    return out


def set_week(daily, week_start, per_day):
    for d in sc.week_days(week_start):
        daily[d.isoformat()]["911"] = per_day - 300


# ------------------------------------------------------------------ parse

def test_parse_reads_the_grouped_shape_and_sums_only_community_types():
    b = body([("2026-10-26", "911", 400), ("2026-10-26", "ONVIEW", 233),
              ("2026-10-26", "TELEPHONE OTHER, NOT 911", 174),
              ("2026-10-26", "TEXT MESSAGE", 1),
              ("2026-10-26", "ALARM CALL (NOT POLICE ALARM)", 8),
              ("2026-10-26", "IN PERSON COMPLAINT", 2)])
    daily = sc.parse(b)
    assert sc.community_daily(daily) == {date(2026, 10, 26): 400 + 174 + 1 + 2}
    assert sc.unknown_types(daily) == {}
    ok(test_parse_reads_the_grouped_shape_and_sums_only_community_types)


def test_parse_fails_loud():
    bad = [b"", b"{}", b"[]", b"not json",
           body([("2026-10-26", "911", 1), ("2026-10-26", "911", 2)]),
           json.dumps([{"d": "2026-10-26T00:00:00.000", "call_type": "911", "n": "-1"}]).encode(),
           json.dumps([{"d": "2026-10-26T00:00:00.000", "call_type": "911", "n": "1.5"}]).encode(),
           json.dumps([{"d": "2026-10-26T00:00:00.000", "call_type": "911", "n": "1",
                        "extra": 1}]).encode()]
    for b in bad:
        try:
            sc.parse(b)
        except ValueError:
            continue
        raise AssertionError(f"parse accepted {b[:60]!r}")
    big = body([("2026-10-26", f"T{i}", 1) for i in range(sc.ROW_LIMIT)])
    try:
        sc.parse(big)
        raise AssertionError("a body at the $limit was accepted")
    except sc.Truncated:
        pass
    ok(test_parse_fails_loud)


def test_an_undated_group_is_kept_apart_and_never_counted():
    rows = [{"d": "2026-10-26T00:00:00.000", "call_type": "911", "n": "10"},
            {"call_type": "911", "n": "4"}]
    daily = sc.parse(json.dumps(rows).encode())
    assert daily[sc.UNDATED] == {"911": 4}
    assert sc.community_daily(daily) == {date(2026, 10, 26): 10}
    assert sc.newest_day(daily) == date(2026, 10, 26)
    ok(test_an_undated_group_is_kept_apart_and_never_counted)


def test_an_unlisted_call_type_is_excluded_and_reported():
    daily = sc.parse(body([("2026-10-26", "911", 10), ("2026-10-26", "DRONE", 3)]))
    assert sc.community_daily(daily)[date(2026, 10, 26)] == 10
    assert sc.unknown_types(daily) == {"DRONE": 3}
    ok(test_an_unlisted_call_type_is_excluded_and_reported)


def test_the_request_is_one_grouped_keyless_query_with_an_honest_agent():
    url = sc.request_url("2026-11-08")
    assert url.startswith("https://cos-data.seattle.gov/resource/33kz-ixgy.json?")
    assert "token" not in url.lower()
    p = sc.query_params("2026-11-08")
    assert p["$where"].endswith(f"'{(date(2026, 11, 8) - timedelta(days=sc.WINDOW_DAYS)).isoformat()}T00:00:00'")
    assert "count(distinct cad_event_number)" in p["$select"]
    seen = {}

    class R:
        status_code = 200
        content = b"[]"

    def get(url, headers, timeout, allow_redirects):
        seen.update(headers=headers, redirects=allow_redirects)
        return R()
    sc.fetch_text("2026-11-08", get=get)
    assert "Mozilla" not in seen["headers"]["User-Agent"]
    assert seen["redirects"] is False

    class Blocked(R):
        status_code = 403
    calls = []

    def blocked(*a, **k):
        calls.append(1)
        return Blocked()
    try:
        sc.fetch_text("2026-11-08", get=blocked)
        raise AssertionError("a 403 was not raised")
    except Exception as e:                                   # noqa: BLE001
        assert "403" in str(e)
    assert len(calls) == 1, "an HTTP error must not be retried"
    ok(test_the_request_is_one_grouped_keyless_query_with_an_honest_agent)


def test_the_window_covers_the_oldest_outage_lookback():
    # step-back block's oldest year-earlier week, plus the 28-day lookback,
    # measured from a capture taken on the lock day (D-1, UTC D).
    oldest = sc.reference_weeks(D, 1)[0] - timedelta(days=364 + sc.OUTAGE_LOOKBACK_DAYS)
    assert D - timedelta(days=sc.WINDOW_DAYS) <= oldest
    ok(test_the_window_covers_the_oldest_outage_lookback)


# ------------------------------------------------------------------ clock

def test_the_clock_matches_the_card():
    assert sc.lock_instant(D) == datetime(2026, 12, 14, 7, 59, tzinfo=timezone.utc)
    assert sc.resolution_instant(D) == datetime(2026, 12, 29, 20, 0, tzinfo=timezone.utc)
    assert sc.grace_end(D) == datetime(2027, 1, 1, 20, 0, tzinfo=timezone.utc)
    # PDT: a week in June locks at 06:59Z.
    assert sc.lock_instant(date(2026, 6, 15)).hour == 6
    try:
        sc.lock_instant(date(2026, 12, 15))
        raise AssertionError("a non-Monday week was accepted")
    except ValueError:
        pass
    ok(test_the_clock_matches_the_card)


# ------------------------------------------------------------------ reference

def test_reference_is_a_ratio_of_sums_against_the_364_day_shift():
    daily = flat_daily(D - timedelta(days=440), D - timedelta(days=4))
    # This year's four reference weeks: 700, 710, 720, 730 a day.
    for w, v in zip(sc.reference_weeks(D), (700, 710, 720, 730)):
        set_week(daily, w, v)
    # A year earlier: 650 a day for all four; last year's aligned week 600.
    for w in sc.reference_weeks(D):
        set_week(daily, w - timedelta(days=364), 650)
    set_week(daily, D - timedelta(days=364), 600)
    ref = sc.yoy4_reference(sc.community_daily(daily), D)
    num = 7 * (700 + 710 + 720 + 730)
    den = 7 * 650 * 4
    assert ref["numerator"] == num and ref["denominator"] == den
    assert abs(ref["mean"] - round(num / den * 7 * 600, 2)) < 1e-9
    assert ref["reference_weeks"] == ["2026-11-09", "2026-11-16", "2026-11-23", "2026-11-30"]
    assert ref["last_year_week"] == "2025-12-15" and ref["step_back"] == 0
    assert abs(ref["ratio"] - num / den) < 1e-6
    ok(test_reference_is_a_ratio_of_sums_against_the_364_day_shift)


def test_ratio_of_sums_differs_from_mean_of_ratios():
    daily = flat_daily(D - timedelta(days=440), D - timedelta(days=4))
    weeks = sc.reference_weeks(D)
    for w, (now, then) in zip(weeks, ((700, 350), (700, 700), (700, 700), (700, 700))):
        set_week(daily, w, now)
        set_week(daily, w - timedelta(days=364), then)
    ref = sc.yoy4_reference(sc.community_daily(daily), D)
    ratio_of_sums = (4 * 700) / (350 + 3 * 700)
    mean_of_ratios = (2 + 1 + 1 + 1) / 4
    assert abs(ref["ratio"] - round(ratio_of_sums, 6)) < 1e-9
    assert abs(ratio_of_sums - mean_of_ratios) > 0.05
    ok(test_ratio_of_sums_differs_from_mean_of_ratios)


def test_the_lock_week_is_never_used():
    daily = flat_daily(D - timedelta(days=440), D - timedelta(days=1))
    cd = sc.community_daily(daily)
    base = sc.yoy4_reference(cd, D)
    shown = sc.persistence_settled(cd, D)
    # Change the week that ends at the lock (D-7..D-1), and the week before it
    # is untouched: the reference must not move.
    lock_week = D - timedelta(days=7)
    assert lock_week.isoformat() not in base["reference_weeks"]
    set_week(daily, lock_week, 2000)
    cd2 = sc.community_daily(daily)
    assert sc.yoy4_reference(cd2, D) == base
    assert sc.persistence_settled(cd2, D) == shown
    assert shown["week"] == (D - timedelta(days=14)).isoformat()
    # And with the lock week absent (the usual case: three days behind), the
    # reference is still computable.
    partial = {d: v for d, v in cd.items() if d < D - timedelta(days=4)}
    assert sc.yoy4_reference(partial, D) == base
    ok(test_the_lock_week_is_never_used)


def test_a_holiday_week_is_aligned_by_the_364_day_shift():
    # Thanksgiving 2026 is Thu Nov 26; 364 days earlier is Thu Nov 27 2025,
    # Thanksgiving 2025. A target week of Nov 23 2026 reads last year's
    # Thanksgiving week as its aligned week.
    tg = date(2026, 11, 23)
    daily = flat_daily(tg - timedelta(days=440), tg - timedelta(days=4))
    set_week(daily, tg - timedelta(days=364), 560)        # last year's dip
    ref = sc.yoy4_reference(sc.community_daily(daily), tg)
    assert ref["last_year_week"] == "2025-11-24"
    assert date(2025, 11, 27) in sc.week_days(date(2025, 11, 24))
    assert ref["ratio"] == 1.0 and ref["mean"] == 7 * 560
    # Persistence, shown and unranked, does not see the holiday at all.
    assert sc.persistence_settled(sc.community_daily(daily), tg)["mean"] == 7 * 700
    ok(test_a_holiday_week_is_aligned_by_the_364_day_shift)


def test_an_outage_in_the_reference_block_steps_back_once_then_refuses():
    daily = flat_daily(D - timedelta(days=440), D - timedelta(days=4))
    newest_ref = sc.reference_weeks(D)[-1]
    daily[(newest_ref + timedelta(days=2)).isoformat()]["911"] = 0
    daily[(newest_ref + timedelta(days=2)).isoformat()]["TELEPHONE OTHER, NOT 911"] = 0
    ref = sc.yoy4_reference(sc.community_daily(daily), D)
    assert ref["step_back"] == 1
    assert ref["reference_weeks"][0] == (D - timedelta(days=42)).isoformat()
    # An outage in a week shared by both blocks: not postable.
    shared = sc.reference_weeks(D)[1]
    daily[(shared + timedelta(days=1)).isoformat()]["911"] = 10
    daily[(shared + timedelta(days=1)).isoformat()]["TELEPHONE OTHER, NOT 911"] = 0
    try:
        sc.yoy4_reference(sc.community_daily(daily), D)
        raise AssertionError("a reference was posted on an outage")
    except sc.NotPostable:
        pass
    ok(test_an_outage_in_the_reference_block_steps_back_once_then_refuses)


def test_an_outage_in_last_years_aligned_week_is_not_postable():
    daily = flat_daily(D - timedelta(days=440), D - timedelta(days=4))
    del daily[(D - timedelta(days=364) + timedelta(days=3)).isoformat()]
    try:
        sc.yoy4_reference(sc.community_daily(daily), D)
        raise AssertionError("posted without last year's aligned week")
    except sc.NotPostable:
        pass
    ok(test_an_outage_in_last_years_aligned_week_is_not_postable)


def test_the_outage_threshold_is_forty_percent_of_the_28_day_median():
    daily = flat_daily(D - timedelta(days=60), D + timedelta(days=10))
    cd = sc.community_daily(daily)
    cd[D + timedelta(days=2)] = int(0.41 * 700)     # a real shock, kept
    assert sc.outage_days(cd, D) == []
    cd[D + timedelta(days=2)] = int(0.39 * 700)     # an outage
    assert sc.outage_days(cd, D) == [D + timedelta(days=2)]
    del cd[D + timedelta(days=5)]
    assert sc.outage_days(cd, D)[-1] == D + timedelta(days=5)
    ok(test_the_outage_threshold_is_forty_percent_of_the_28_day_median)


# ------------------------------------------------------------------ archive

class Sandbox:
    """Point the adapter and provenance at a temporary repository root."""

    def __enter__(self):
        self.dir = tempfile.mkdtemp()
        self.saved = (provenance.ROOT, provenance.ARCHIVE, provenance.MANIFEST,
                      sc.ROOT, sc.ARCHIVE, sc.FETCHES)
        provenance.ROOT = sc.ROOT = self.dir
        provenance.ARCHIVE = os.path.join(self.dir, "sources")
        provenance.MANIFEST = os.path.join(provenance.ARCHIVE, "manifest.json")
        sc.ARCHIVE = os.path.join(provenance.ARCHIVE, sc.NAME)
        sc.FETCHES = os.path.join(sc.ARCHIVE, "fetches.json")
        return self

    def __exit__(self, *exc):
        (provenance.ROOT, provenance.ARCHIVE, provenance.MANIFEST,
         sc.ROOT, sc.ARCHIVE, sc.FETCHES) = self.saved
        shutil.rmtree(self.dir)


def as_body(daily):
    return body([(d, t, n) for d in sorted(daily) for t, n in sorted(daily[d].items())])


def test_capture_is_write_once_per_utc_day_and_records_the_fetch_time():
    calls = []

    def fetch(day, value=700):
        calls.append(day)
        return as_body(flat_daily(date(2026, 11, 1), date(2026, 11, 5), value)), f"u/{day}"
    with Sandbox() as s:
        t1 = datetime(2026, 11, 30, 0, 17, tzinfo=timezone.utc)
        row = sc.capture(now=t1, fetch=fetch)
        assert row["fetched_at"] == "2026-11-30T00:17:00Z"
        assert row["file"] == "sources/seattle_cad/2026-11-30.json"
        first = open(os.path.join(s.dir, row["file"]), "rb").read()
        # A later run the same UTC day costs no request and changes nothing.
        sc.capture(now=t1 + timedelta(hours=6),
                   fetch=lambda day: fetch(day, value=900))
        assert calls == ["2026-11-30"]
        assert open(os.path.join(s.dir, row["file"]), "rb").read() == first
        assert sc.load_fetches()["2026-11-30"]["fetched_at"] == "2026-11-30T00:17:00Z"
        man = provenance.load_manifest()[sc.NAME]
        assert man["sha256"] == provenance.digest(first)
        # A malformed body is never filed.
        try:
            sc.capture(now=t1 + timedelta(days=1), fetch=lambda day: (b"[]", "u"))
            raise AssertionError("an empty body was filed")
        except ValueError:
            pass
        assert not os.path.exists(sc.archive_path("2026-12-01"))
        # A tampered file is refused on read.
        with open(os.path.join(s.dir, row["file"]), "ab") as f:
            f.write(b" ")
        try:
            sc.read_capture("2026-11-30")
            raise AssertionError("a changed archive was read")
        except RuntimeError:
            pass
    ok(test_capture_is_write_once_per_utc_day_and_records_the_fetch_time)


# ------------------------------------------------------------------ resolution

def fake_fetches(days_at):
    """{day: fetches row} with a fetched_at per day; reader maps day -> daily."""
    return {day: {"fetched_at": at, "file": f"sources/seattle_cad/{day}.json",
                  "sha256": "x" + day, "url": "u", "bytes": 1}
            for day, at in days_at.items()}


def test_resolution_takes_the_first_capture_at_or_after_tuesday_noon():
    good = flat_daily(D - timedelta(days=40), D + timedelta(days=12))
    later = flat_daily(D - timedelta(days=40), D + timedelta(days=14), per_day=800)
    early = {d: v for d, v in good.items() if d < (D + timedelta(days=4)).isoformat()}
    by_day = {"2026-12-29": early,   # 04:17Z Tue = Mon PT: before the instant
              "2026-12-30": good,    # 00:17Z Wed = Tue 16:17 PT: the one
              "2026-12-31": later}
    fx = fake_fetches({"2026-12-29": "2026-12-29T04:17:00Z",
                       "2026-12-30": "2026-12-30T00:17:00Z",
                       "2026-12-31": "2026-12-31T00:17:00Z"})

    def reader(day, fetches):
        return by_day[day], fetches[day]
    pend = sc.resolve(D, datetime(2026, 12, 29, 19, 0, tzinfo=timezone.utc), fx, reader)
    assert pend["status"] == "pending"
    res = sc.resolve(D, datetime(2027, 1, 5, tzinfo=timezone.utc), fx, reader)
    assert res["status"] == "resolved" and res["value"] == 7 * 700
    assert res["snapshot"].endswith("2026-12-30.json")
    ok(test_resolution_takes_the_first_capture_at_or_after_tuesday_noon)


def test_an_outage_waits_inside_the_grace_window_then_cancels():
    broken = flat_daily(D - timedelta(days=40), D + timedelta(days=12))
    broken[(D + timedelta(days=3)).isoformat()]["911"] = 0
    broken[(D + timedelta(days=3)).isoformat()]["TELEPHONE OTHER, NOT 911"] = 0
    fixed = flat_daily(D - timedelta(days=40), D + timedelta(days=13))
    fx = fake_fetches({"2026-12-30": "2026-12-30T00:17:00Z",
                       "2026-12-31": "2026-12-31T00:17:00Z"})
    by_day = {"2026-12-30": broken, "2026-12-31": fixed}

    def reader(day, fetches):
        return by_day[day], fetches[day]
    r = sc.resolve(D, datetime(2026, 12, 30, 12, tzinfo=timezone.utc), fx, reader)
    assert r["status"] == "pending" and r["tried"][0]["outage_days"] == ["2026-12-17"]
    r = sc.resolve(D, datetime(2027, 1, 2, tzinfo=timezone.utc), fx, reader)
    assert r["status"] == "resolved" and r["snapshot"].endswith("2026-12-31.json")
    by_day["2026-12-31"] = broken
    r = sc.resolve(D, datetime(2027, 1, 2, tzinfo=timezone.utc), fx, reader)
    assert r["status"] == "cancelled"
    # A capture after the grace deadline is never used.
    fx["2027-01-02"] = fake_fetches({"2027-01-02": "2027-01-02T00:17:00Z"})["2027-01-02"]
    by_day["2027-01-02"] = fixed
    r = sc.resolve(D, datetime(2027, 1, 3, tzinfo=timezone.utc), fx, reader)
    assert r["status"] == "cancelled"
    ok(test_an_outage_waits_inside_the_grace_window_then_cancels)


def test_unlisted_types_over_one_percent_send_the_round_to_review():
    daily = flat_daily(D - timedelta(days=40), D + timedelta(days=12))
    for d in sc.week_days(D):
        daily[d.isoformat()]["DRONE"] = 20       # 20 of ~905 a day > 1%
    fx = fake_fetches({"2026-12-30": "2026-12-30T00:17:00Z"})
    r = sc.resolve(D, datetime(2027, 1, 5, tzinfo=timezone.utc), fx,
                   lambda day, f: (daily, f[day]))
    assert r["status"] == "review" and r["unknown_types"] == {"DRONE": 140}
    assert r["value"] == 7 * 700
    ok(test_unlisted_types_over_one_percent_send_the_round_to_review)


def test_the_lock_reference_reads_a_fresh_capture_only():
    daily = flat_daily(D - timedelta(days=sc.WINDOW_DAYS), D - timedelta(days=4))
    rd = lambda day, f: (daily, f[day])                   # noqa: E731
    fresh = fake_fetches({"2026-12-13": "2026-12-13T00:17:00Z",
                          "2026-12-14": "2026-12-14T06:17:00Z",   # before 07:59Z
                          "2026-12-15": "2026-12-15T00:17:00Z"})  # after the lock
    ref = sc.lock_reference(D, fresh, rd)
    assert ref["fetched_at"] == "2026-12-14T06:17:00Z"
    assert ref["zero_point"] == "yoy4" and ref["ranked"]["method"] == "yoy4"
    assert ref["ranked"]["sd"] > 0 and ref["shown"]["sd"] > 0
    assert ref["shown"]["method"] == "persistence_settled_week"
    stale = fake_fetches({"2026-12-12": "2026-12-12T06:17:00Z"})
    try:
        sc.lock_reference(D, stale, rd)
        raise AssertionError("a two-day-old capture was used at lock")
    except sc.NotPostable:
        pass
    ok(test_the_lock_reference_reads_a_fresh_capture_only)


def test_the_spread_is_the_rms_of_the_same_rule_over_13_settled_weeks():
    daily = flat_daily(D - timedelta(days=sc.WINDOW_DAYS), D - timedelta(days=4))
    # Perturb the actuals of past target weeks D-14 .. D-98 by known amounts;
    # the year-earlier block stays flat, so each week's own reference moves
    # only through the perturbed weeks inside its reference block.
    for k in range(2, 15):
        set_week(daily, D - timedelta(days=7 * k), 700 + (k % 3) * 10)
    cd = sc.community_daily(daily)
    errs, weeks = sc.past_errors(cd, D, sc.yoy4_reference)
    assert len(errs) == 13 and weeks[0] == (D - timedelta(days=14)).isoformat()
    assert weeks[-1] == (D - timedelta(days=98)).isoformat()
    expect = []
    for k in range(2, 15):
        w = D - timedelta(days=7 * k)
        expect.append(sc.week_total(cd, w) - sc.yoy4_reference(cd, w)["mean"])
    assert errs == expect
    z = sc.yoy4(cd, D)
    assert z["sd"] == round(sc.rms(expect), 2) and z["spread"] == "rms_13_settled_weeks"
    # The lock week D-7 is not one of the thirteen.
    assert (D - timedelta(days=7)).isoformat() not in z["spread_weeks"]
    ok(test_the_spread_is_the_rms_of_the_same_rule_over_13_settled_weeks)


def test_the_spread_skips_a_broken_week_and_refuses_below_thirteen():
    daily = flat_daily(D - timedelta(days=sc.WINDOW_DAYS), D - timedelta(days=4))
    gap = D - timedelta(days=35)
    for d in sc.week_days(gap):
        daily[d.isoformat()]["911"] = 0
        daily[d.isoformat()]["TELEPHONE OTHER, NOT 911"] = 0
    errs, weeks = sc.past_errors(sc.community_daily(daily), D, sc.yoy4_reference)
    assert len(errs) == 13 and gap.isoformat() not in weeks
    # D-14 is skipped too: the broken week sits in both its reference block
    # and the stepped-back one, so its own reference is not postable.
    assert (D - timedelta(days=14)).isoformat() not in weeks
    assert weeks[-1] == (D - timedelta(days=112)).isoformat()
    short = flat_daily(D - timedelta(days=300), D - timedelta(days=4))
    try:
        sc.yoy4(sc.community_daily(short), D)
        raise AssertionError("posted a spread on fewer than 13 weeks")
    except sc.NotPostable:
        pass
    ok(test_the_spread_skips_a_broken_week_and_refuses_below_thirteen)


def test_the_window_covers_the_whole_spread_search():
    # past_errors searches k = 2 .. 1 + SPREAD_SEARCH_WEEKS; the oldest week
    # searched is D-189, and its own reference with a step-back plus the
    # outage lookback reaches D-623. A capture is taken at most a day after
    # the lock, so its window must start no later than that.
    last_searched = D - timedelta(days=7 * (1 + sc.SPREAD_SEARCH_WEEKS))
    assert last_searched == D - timedelta(days=189)
    need = sc.reference_weeks(last_searched, 1)[0] - timedelta(
        days=364 + sc.OUTAGE_LOOKBACK_DAYS)
    assert need == D - timedelta(days=623)
    assert D - timedelta(days=sc.WINDOW_DAYS) <= need
    ok(test_the_window_covers_the_whole_spread_search)


def test_pilot_growth_gate():
    week = date(2026, 11, 16)
    a = flat_daily(week - timedelta(days=10), week + timedelta(days=8), per_day=700)
    b = flat_daily(week - timedelta(days=10), week + timedelta(days=20), per_day=703)
    c = flat_daily(week - timedelta(days=10), week + timedelta(days=20), per_day=710)
    fx = fake_fetches({"2026-11-30": "2026-11-30T00:17:00Z",
                       "2026-12-11": "2026-12-11T00:17:00Z",
                       "2026-12-12": "2026-12-12T00:17:00Z"})
    by = {"2026-11-30": a, "2026-12-11": b, "2026-12-12": c}
    rd = lambda day, f: (by[day], f[day])                 # noqa: E731
    g = sc.pilot_growth(week, "2026-11-30", "2026-12-11", fx, rd)
    assert g["settled"] and abs(g["growth"] - 3 / 700) < 1e-6
    assert not sc.pilot_growth(week, "2026-11-30", "2026-12-12", fx, rd)["settled"]
    ok(test_pilot_growth_gate)


def test_weekly_series_drops_the_week_that_is_still_posting():
    daily = flat_daily(date(2026, 10, 5), date(2026, 11, 5))   # Thu 11-05 newest
    s = sc.weekly_series(sc.community_daily(daily))
    assert s[-1]["date"] == "2026-11-01" and s[0]["date"] == "2026-10-11"
    assert all(p["value"] == 4900.0 for p in s)
    ok(test_weekly_series_drops_the_week_that_is_still_posting)


def test_the_refresh_captures_daily_and_never_blocks_on_it():
    import yaml
    with open(os.path.join(ROOT, ".github", "workflows", "refresh.yml")) as f:
        wf = yaml.safe_load(f)
    steps = [st for job in wf["jobs"].values() for st in job.get("steps", [])]
    runs = [st.get("run", "") for st in steps]
    i = runs.index("python -m ssa.adapters.seattle_cad capture")
    assert steps[i].get("continue-on-error") is True
    assert i < runs.index("python -m ssa.refresh"), "capture after the refresh"
    ok(test_the_refresh_captures_daily_and_never_blocks_on_it)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"{len(PASSED)} passed")
