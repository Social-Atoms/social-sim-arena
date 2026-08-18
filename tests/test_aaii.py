"""AAII sentiment: year-less dates, the sum-to-100 check, and the refusals.

Run: PYTHONPATH=. python tests/test_aaii.py

No network. The fixture below reproduces the real markup of
https://www.aaii.com/sentimentsurvey/sent_results as served on 2026-08-17 --
the same cell classes, alignment attributes, stray trailing spaces and header
row -- because every guarantee the adapter makes is about that exact shape.
The page's rows carry no year, so nearly every test here is about the same
two properties: the year is anchored on the response's own date (never the
local clock), and a row the anchoring cannot place is an error, not a guess.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa import series as series_registry                         # noqa: E402
from ssa.adapters import aaii                                     # noqa: E402

HEADER = """
<table width="600px" border="0" cellspacing="1" bgcolor="666666" align="center"
 class="bordered" style="font-size: 18px;">
  <tr align="center" style="font-weight: bold;">
    <td class="tableSubHd2" align="left"><font color="white">Reported Date</font></td>
    <td class="tableSubHd2"><font color="white">Bullish</font></td>
    <td class="tableSubHd2"><font color="white">Neutral</font></td>
    <td class="tableSubHd2"><font color="white">Bearish</font></td>
  </tr>
"""


def row(date_label, bull, neutral, bear):
    """One data row, byte-shaped like the live page: left-aligned month-day,
    three right-aligned percents, a trailing space inside some cells."""
    return f"""
    <tr align="center" bgcolor="ffffff">
      <td align="left" class="tableTxt">{date_label}</td>
      <td align="right" class="tableTxt">{bull}% </td>
      <td align="right" class="tableTxt">{neutral}%</td>
      <td align="right" class="tableTxt">{bear}% </td>
    </tr>
"""


def page(rows, header=HEADER):
    body = header + "".join(rows) + "</table>"
    return ("<!DOCTYPE html><html><head><style>th,td{padding:3px}</style>"
            f"</head><body><main class=\"sentimentsurvey\">{body}"
            "<p>&copy; 2026 American Association of Individual Investors</p>"
            "</main></body></html>")


ASOF = datetime.date(2026, 8, 17)

REAL = page([row("Aug 12", "34.7", "27.4", "37.9"),
             row("Aug 5", "37.0", "25.0", "38.0"),
             row("Jul 29", "31.0", "26.9", "42.1")])


def test_rows_parse_oldest_first_with_the_spread_precomputed():
    rows = aaii.parse(REAL, ASOF)
    assert [r["date"] for r in rows] == ["2026-07-29", "2026-08-05",
                                        "2026-08-12"]
    assert rows[-1] == {"date": "2026-08-12", "bullish": 34.7, "neutral": 27.4,
                        "bearish": 37.9, "spread": -3.2}
    # 34.7 - 37.9 is -3.1999999999999957 in binary floating point; the spread
    # must arrive rounded at a defined place, not with digits that would be
    # committed into a resolution as if they meant something.
    assert all(repr(r["spread"]) == repr(round(r["spread"], 2)) for r in rows)


def test_the_year_is_inferred_from_the_response_not_the_wall_clock():
    """The same bytes parsed against a different response date land in a
    different year -- which is exactly right, because the page carries no year
    and only the response knows when it was served. A vintage replayed from
    the archive must re-date to what it showed then, not to today."""
    rows_2026 = aaii.parse(REAL, datetime.date(2026, 8, 17))
    rows_2025 = aaii.parse(REAL, datetime.date(2025, 8, 17))
    assert rows_2026[-1]["date"] == "2026-08-12"
    assert rows_2025[-1]["date"] == "2025-08-12"


def test_december_seen_from_january_lands_in_the_previous_year():
    """The one place year inference can actually go wrong: the table spans
    New Year, so the rows below the January ones belong to the year before
    the anchor."""
    p = page([row("Jan 6", "30.0", "30.0", "40.0"),
              row("Dec 30", "31.0", "29.0", "40.0"),
              row("Dec 23", "32.0", "28.0", "40.0")])
    rows = aaii.parse(p, datetime.date(2027, 1, 8))
    assert [r["date"] for r in rows] == ["2026-12-23", "2026-12-30",
                                        "2027-01-06"]


def test_a_first_row_dated_after_the_anchor_backs_up_a_year():
    """A response served on Jan 1 for a table whose newest row is Dec 31:
    'Dec 31' this year would be in the future, so it is last year's."""
    p = page([row("Dec 31", "30.0", "30.0", "40.0"),
              row("Dec 24", "31.0", "29.0", "40.0")])
    rows = aaii.parse(p, datetime.date(2027, 1, 1))
    assert rows[-1]["date"] == "2026-12-31"


def test_a_row_that_does_not_step_back_a_week_is_an_error_not_a_year_jump():
    """A duplicated date cell would satisfy 'strictly decreasing' by borrowing
    last year's date -- a silent ~365-day jump. The weekly-gap bound is what
    turns that into a loud failure."""
    p = page([row("Aug 12", "34.7", "27.4", "37.9"),
              row("Aug 12", "37.0", "25.0", "38.0")])
    try:
        aaii.parse(p, ASOF)
        assert False, "a duplicated date was accepted as last year's row"
    except RuntimeError as e:
        assert "weekly sequence" in str(e), e


def test_shares_that_do_not_sum_to_100_stop_the_run():
    """The survey is a three-way choice, so the shares are exhaustive. A regex
    that slips one cell sideways reads a percentage into the wrong column and
    the sum breaks immediately -- this is the row checking itself, the same
    property the Penta adapter gets from previous + delta == current."""
    p = page([row("Aug 12", "34.7", "27.4", "17.9")])   # bear misread by 20
    try:
        aaii.parse(p, ASOF)
        assert False, "a row summing to 80 was published"
    except RuntimeError as e:
        assert "not 100" in str(e), e


def test_a_reordered_header_is_fatal_because_the_sum_cannot_catch_a_swap():
    """Bull and bear exchanged still sum to 100 while flipping the spread's
    sign. The header-order check is the only guard against that, which is why
    a missing or reordered header refuses the whole page."""
    swapped = HEADER.replace("Bullish", "TEMP").replace(
        "Bearish", "Bullish").replace("TEMP", "Bearish")
    p = page([row("Aug 12", "37.9", "27.4", "34.7")], header=swapped)
    try:
        aaii.parse(p, ASOF)
        assert False, "a reordered header was read as if nothing changed"
    except RuntimeError as e:
        assert "header" in str(e), e


def test_a_page_with_no_rows_raises_rather_than_returning_empty():
    try:
        aaii.parse(page([]), ASOF)
        assert False, "an empty table was published as an empty series"
    except RuntimeError as e:
        assert "zero rows" in str(e), e


def test_a_page_weeks_behind_its_own_response_date_is_refused():
    """The Michigan incident, prevented here: a frozen page served with a
    fresh response date would anchor baselines on a level the series left
    weeks ago, and resolve rounds against numbers public before the lock."""
    try:
        aaii.parse(REAL, datetime.date(2026, 10, 1))
        assert False, "a six-week-stale page was served as current"
    except RuntimeError as e:
        assert "frozen page" in str(e), e


def test_a_month_that_is_not_a_month_is_a_format_change():
    p = page([row("Foo 33", "34.7", "27.4", "37.9")])
    try:
        aaii.parse(p, ASOF)
        assert False, "a non-month date label was guessed at"
    except RuntimeError as e:
        assert "not a month" in str(e), e


def test_asof_comes_from_the_date_header():
    assert aaii.parse_asof("Mon, 17 Aug 2026 07:44:23 GMT") == ASOF


def test_transport_errors_retry_and_http_errors_do_not():
    """A dropped connection is retried with linear backoff because the refresh
    has a hard deadline at each round's lock. An HTTP error is Imperva saying
    no, and repeating the request will not change its mind."""
    import requests as _requests

    calls, sleeps = [], []
    saved_get, saved_sleep = aaii.requests.get, aaii.time.sleep
    aaii.time.sleep = sleeps.append

    def flaky(url, timeout=None, headers=None):
        calls.append(url)
        raise _requests.ConnectionError("dropped")

    aaii.requests.get = flaky
    try:
        try:
            aaii.fetch_text(retries=3)
            assert False, "a dead transport must raise"
        except RuntimeError as e:
            assert "3 attempts" in str(e), e
        assert len(calls) == 3, "every transport failure must be retried"
        assert sleeps == [aaii.BACKOFF * 1, aaii.BACKOFF * 2], \
            "linear backoff, and no sleep after the final failure"

        class Forbidden:
            status_code = 403
            text = "blocked"
            headers = {}

            def raise_for_status(self):
                raise _requests.HTTPError("403 Forbidden")

        calls.clear()
        aaii.requests.get = lambda url, timeout=None, headers=None: (
            calls.append(url) or Forbidden())
        try:
            aaii.fetch_text(retries=3)
            assert False, "an HTTP refusal must raise"
        except _requests.HTTPError:
            pass
        assert len(calls) == 1, "an HTTP error must not be retried"
    finally:
        aaii.requests.get, aaii.time.sleep = saved_get, saved_sleep


def test_an_empty_body_and_a_missing_date_header_both_refuse():
    class Resp:
        status_code = 200

        def __init__(self, text, headers):
            self.text, self.headers = text, headers

        def raise_for_status(self):
            pass

    saved = aaii.requests.get
    try:
        aaii.requests.get = lambda url, timeout=None, headers=None: Resp(
            "  \n", {"Date": "Mon, 17 Aug 2026 07:44:23 GMT"})
        try:
            aaii.fetch_text()
            assert False, "an empty body was accepted"
        except RuntimeError as e:
            assert "returned nothing" in str(e), e

        # No Date header means no anchor for the year-less rows. Falling back
        # to the local clock would re-date a replayed vintage to the day of
        # the replay, so the refusal is the correct behaviour.
        aaii.requests.get = lambda url, timeout=None, headers=None: Resp(
            REAL, {})
        try:
            aaii.fetch_text()
            assert False, "a response with no Date header was accepted"
        except RuntimeError as e:
            assert "Date header" in str(e), e
    finally:
        aaii.requests.get = saved


def test_fetch_and_parse_are_separate_as_every_adapter_must_be():
    """A vintage rebuilt from parsed rows is our reading of the page, not the
    page. tests/test_provenance.py asserts this of every upstream adapter."""
    assert callable(aaii.fetch_text) and callable(aaii.parse)


# --- the registry -----------------------------------------------------------

def test_the_series_is_registered_without_a_survey_instrument():
    spec = series_registry.SERIES["aaii_bull_bear_spread"]
    assert spec["source"] == "aaii"
    assert spec["value"] == "spread"
    assert "bullish minus bearish" in spec["unit"]
    d = series_registry.describe("aaii_bull_bear_spread")
    assert "AAII members" in d["question"]
    assert "mean-reverting" in d["methodology"]
    # No instrument, deliberately: the persona panel is a general-population
    # panel and AAII members are a self-selected investor population. A panel
    # answer scored against a member survey would book the population gap as
    # model error. survey() returning None is the refusal, by name.
    assert series_registry.survey("aaii_bull_bear_spread") is None


def test_build_all_uses_the_adapter_and_accepts_an_injected_override():
    saved = series_registry.SERIES
    series_registry.SERIES = {k: v for k, v in saved.items()
                              if v["source"] == "aaii"}
    fetched = []
    saved_fetch = aaii.fetch
    aaii.fetch = lambda *a, **k: fetched.append(1) or []
    try:
        given = aaii.parse(REAL, ASOF)
        out = series_registry.build_all(sources={"aaii": given})
        assert out["aaii_bull_bear_spread"] == [
            {"date": "2026-07-29", "value": -11.1},
            {"date": "2026-08-05", "value": -1.0},
            {"date": "2026-08-12", "value": -3.2}]
        assert fetched == [], "an injected source must not fetch"
    finally:
        series_registry.SERIES = saved
        aaii.fetch = saved_fetch


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"\n{len(tests)} tests passed")
