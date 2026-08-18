"""Wikipedia pageviews adapter: weekly aggregation, units, and the registry.

Run: python tests/test_wikipedia.py

No network. The fixtures are hand-built daily rows in the shape `parse`
emits -- 2023-01-02 is a Monday and 2023-01-08 a Sunday, so every expected
week boundary below can be checked against a calendar by eye.
"""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa import series as series_registry                         # noqa: E402
from ssa.adapters import wikipedia                                # noqa: E402


def days(start, views):
    """Daily rows: one per value, consecutive days from `start`."""
    d0 = date.fromisoformat(start)
    return [{"date": (d0 + timedelta(days=i)).isoformat(), "views": v}
            for i, v in enumerate(views)]


# Two complete weeks, Monday 2023-01-02 through Sunday 2023-01-15. The first
# week's values differ by day so a shifted boundary changes the sum; the
# second is flat so its expected total is legible.
WEEK1 = [1000, 2000, 3000, 4000, 5000, 6000, 7000]        # sums to 28000
WEEK2 = [10000] * 7                                        # sums to 70000
TWO_WEEKS = days("2023-01-02", WEEK1 + WEEK2)


# --- weekly aggregation ------------------------------------------------------

def test_weeks_run_monday_to_sunday_and_are_dated_by_the_sunday():
    assert date.fromisoformat("2023-01-02").weekday() == 0, "fixture sanity"
    out = wikipedia.weekly_series("X", daily=TWO_WEEKS)
    assert out == [{"date": "2023-01-08", "value": 28.0},
                   {"date": "2023-01-15", "value": 70.0}]
    assert all(date.fromisoformat(p["date"]).weekday() == 6 for p in out), \
        "every row is dated by the Sunday its week ends on"


def test_a_trailing_partial_week_is_dropped():
    """The API publishes day by day, so most fetches end mid-week. Emitting
    the fragment would publish a 'weekly total' of three days that jumps by
    the missing days' traffic when the week completes."""
    out = wikipedia.weekly_series(
        "X", daily=TWO_WEEKS + days("2023-01-16", [9000, 9000, 9000]))
    assert out == [{"date": "2023-01-08", "value": 28.0},
                   {"date": "2023-01-15", "value": 70.0}]


def test_a_leading_fragment_is_dropped_too():
    """The default history starts 2023-01-01, a Sunday: one orphan day of the
    week ending that date. Same rule, other end of the series."""
    out = wikipedia.weekly_series(
        "X", daily=days("2023-01-01", [5000]) + TWO_WEEKS)
    assert [p["date"] for p in out] == ["2023-01-08", "2023-01-15"]


def test_a_week_with_a_hole_in_it_is_dropped_not_summed_short():
    """A six-day sum published as a weekly total reads as a one-seventh
    traffic collapse that never happened."""
    holed = [r for r in TWO_WEEKS if r["date"] != "2023-01-04"]
    out = wikipedia.weekly_series("X", daily=holed)
    assert out == [{"date": "2023-01-15", "value": 70.0}], \
        "the incomplete week must vanish, not shrink"


# --- units -------------------------------------------------------------------

def test_values_are_thousands_of_views_to_one_decimal():
    out = wikipedia.weekly_series("X", daily=days("2023-01-02", [123456] * 7))
    assert out == [{"date": "2023-01-08", "value": 864.2}]   # 864192 views
    # Rounded, not truncated: 4379 views is 4.379 thousand, which must come
    # out as 4.4 -- truncation would give 4.3 and quietly bias every total
    # downward.
    out = wikipedia.weekly_series(
        "X", daily=days("2023-01-02", [500, 600, 700, 800, 900, 400, 479]))
    assert out == [{"date": "2023-01-08", "value": 4.4}]
    # And rounded at a defined place, so the committed number has no float
    # noise trailing it.
    for p in wikipedia.weekly_series("X", daily=TWO_WEEKS):
        assert repr(p["value"]) == repr(round(p["value"], 1))


# --- refusing to invent data -------------------------------------------------

def test_empty_api_data_raises_rather_than_returning_empty():
    for payload in ({"items": []}, {}, None):
        try:
            wikipedia.parse(payload, "Donald_Trump")
            assert False, f"an empty payload must raise: {payload!r}"
        except RuntimeError as e:
            assert "Donald_Trump" in str(e), "the error names the article"
    try:
        wikipedia.weekly_series("X", daily=[])
        assert False, "no daily rows must raise"
    except RuntimeError as e:
        assert "empty series" in str(e)


def test_a_history_shorter_than_one_week_raises():
    """Six days aggregate to zero complete weeks, and [] here would give a
    round a target with no history and no persistence null at all."""
    try:
        wikipedia.weekly_series("X", daily=days("2023-01-02", [1000] * 6))
        assert False, "zero complete weeks must raise"
    except RuntimeError as e:
        assert "zero complete weeks" in str(e)


# --- the wire format ---------------------------------------------------------

def test_parse_reads_the_api_item_shape():
    payload = {"items": [
        {"project": "en.wikipedia", "article": "Donald_Trump",
         "granularity": "daily", "timestamp": "2023010300",
         "access": "all-access", "agent": "user", "views": 1534},
        {"project": "en.wikipedia", "article": "Donald_Trump",
         "granularity": "daily", "timestamp": "2023010200",
         "access": "all-access", "agent": "user", "views": 993},
    ]}
    assert wikipedia.parse(payload) == [
        {"date": "2023-01-02", "views": 993},
        {"date": "2023-01-03", "views": 1534}], "oldest first"


def test_the_url_carries_the_hour_suffix_and_the_user_filter():
    """The endpoint's timestamps are YYYYMMDDHH even at daily granularity;
    dropping the `00` is a 404. And `user` is the whole point of the source:
    all-agents would count every scraper that re-crawls the wiki."""
    u = wikipedia.url("Donald_Trump", date(2023, 1, 1), date(2023, 2, 1))
    assert u.endswith("/Donald_Trump/daily/2023010100/2023020100"), u
    assert "/en.wikipedia/all-access/user/" in u
    assert "%2F" in wikipedia.url("A/B", date(2023, 1, 1), date(2023, 1, 2)), \
        "a slash in a title must not become a path separator"


# --- the registry ------------------------------------------------------------

def test_build_all_aggregates_injected_daily_rows_and_never_fetches():
    saved_series = series_registry.SERIES
    saved_fetch = wikipedia.fetch_daily

    def boom(*a, **k):
        raise AssertionError("an injected article must not fetch")
    series_registry.SERIES = {k: v for k, v in saved_series.items()
                              if v["source"] == "wikipedia"}
    wikipedia.fetch_daily = boom
    try:
        out = series_registry.build_all(sources={"wikipedia": {
            "Donald_Trump": TWO_WEEKS + days("2023-01-16", [1, 2, 3]),
            "Taylor_Swift": days("2023-01-02", [50000] * 10),
        }})
        assert set(out) == {"wiki_views_trump", "wiki_views_taylor_swift"}
        for sid in out:
            assert date.fromisoformat(out[sid][-1]["date"]).weekday() == 6, \
                f"{sid} must end on a Sunday, trailing fragment dropped"
        assert out["wiki_views_trump"][-1] == {"date": "2023-01-15",
                                               "value": 70.0}
        assert out["wiki_views_taylor_swift"] == [{"date": "2023-01-08",
                                                   "value": 350.0}]
    finally:
        series_registry.SERIES = saved_series
        wikipedia.fetch_daily = saved_fetch


def test_the_registered_series_carry_text_and_no_survey_instrument():
    for sid in ("wiki_views_trump", "wiki_views_taylor_swift"):
        spec = series_registry.SERIES[sid]
        assert spec["source"] == "wikipedia", sid
        assert spec["wikipedia"]["article"] in ("Donald_Trump", "Taylor_Swift")
        d = series_registry.describe(sid)
        assert d["unit"] == "thousand pageviews (Mon-Sun week)", sid
        assert "human readers" in d["question"], sid
        assert "en.wikipedia" in d["methodology"], sid
        assert "user" in d["methodology"], "the agent filter is disclosed"
        # A persona panel cannot be polled for a pageview count; None is what
        # makes the persona arm refuse these by name rather than improvise.
        assert series_registry.survey(sid) is None, sid


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for t in TESTS:
        t()
        print("ok", t.__name__)
    print(f"\n{len(TESTS)} tests passed")
