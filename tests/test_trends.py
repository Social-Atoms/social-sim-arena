"""Google Trends adapter: parsing, the archive, and the frozen-week rule.

Run: PYTHONPATH=. python tests/test_trends.py

No network. The fixture rows are a real slice of the multiline payload served
for "Tesla" (geo US, window `today 12-m`) on 2026-08-17 -- real epoch
timestamps, real index values, real isPartial flag on the in-progress week --
trimmed to six weeks so the expected numbers can be checked by eye.
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa import series as series_registry                         # noqa: E402
from ssa.adapters import trends                                   # noqa: E402

# Week starts are epoch seconds at Sunday 00:00 UTC, seven days apart.
# 1783814400 = 2026-07-12. The last row is the in-progress week as served.
_ROWS = [(1783814400, 71, False), (1784419200, 70, False),
         (1785024000, 82, False), (1785628800, 76, False),
         (1786233600, 74, False), (1786838400, 75, True)]


def timeline_json(rows=None):
    rows = rows if rows is not None else _ROWS
    data = []
    for t, v, partial in rows:
        row = {"time": str(t), "formattedTime": "wk", "value": [v],
               "hasData": [True]}
        if partial:
            # Google writes the key only on the trailing in-progress row;
            # complete historical rows simply omit it.
            row["isPartial"] = True
        data.append(row)
    return {"default": {"timelineData": data, "averages": []}}


def multiline_body(rows=None):
    return ")]}',\n" + json.dumps(timeline_json(rows))


def explore_body(widgets=None):
    if widgets is None:
        widgets = [
            {"id": "TIMESERIES", "token": "ANI_tok",
             "request": {"time": "2025-08-17 2026-08-17", "resolution": "WEEK"}},
            {"id": "GEO_MAP", "token": "other", "request": {}},
        ]
    return ")]}'\n" + json.dumps({"widgets": widgets})


# --- a scratch archive and a fake wire, so tests never fetch or commit ------

class Wire:
    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="ssa-trends-")
        self.saved_archive = trends.ARCHIVE
        trends.ARCHIVE = self.dir
        self.fetched = []
        self.bodies = {"explore": explore_body(), "multiline": multiline_body()}

    def serve(self, multiline=None, explore=None):
        if multiline is not None:
            self.bodies["multiline"] = multiline
        if explore is not None:
            self.bodies["explore"] = explore

        def fake_get(session, url, params=None):
            self.fetched.append(url)
            if url == trends.EXPLORE:
                return self.bodies["explore"]
            if url == trends.MULTILINE:
                return self.bodies["multiline"]
            return ""                          # the homepage warmup
        trends._get = fake_get

    def cycles(self):
        """Completed request cycles, counted at the multiline hop."""
        return sum(1 for u in self.fetched if u == trends.MULTILINE)

    def close(self):
        trends.ARCHIVE = self.saved_archive
        shutil.rmtree(self.dir, ignore_errors=True)


_REAL_GET = trends._get


def with_wire(fn):
    def run():
        w = Wire()
        w.serve()
        try:
            fn(w)
        finally:
            trends._get = _REAL_GET
            w.close()
    run.__name__ = fn.__name__
    return run


def at(day, hour=12):
    return datetime.fromisoformat(day).replace(hour=hour, tzinfo=timezone.utc)


# --- parsing ----------------------------------------------------------------

def test_the_junk_prefix_is_stripped_in_both_spellings():
    """explore opens with `)]}'` and multiline with `)]}',`; both must go, and
    a body without either must pass through untouched."""
    assert trends.strip_junk(")]}'\n{\"a\":1}") == '{"a":1}'
    assert trends.strip_junk(")]}',\n{\"a\":1}") == '{"a":1}'
    assert trends.strip_junk('{"a":1}') == '{"a":1}'


def test_the_timeseries_widget_is_picked_and_its_absence_raises():
    w = trends.timeseries_widget(trends.parse_widgets(explore_body()))
    assert w["token"] == "ANI_tok"
    try:
        trends.timeseries_widget([{"id": "GEO_MAP", "token": "t", "request": {}}])
        assert False, "no TIMESERIES widget must raise"
    except RuntimeError as e:
        assert "GEO_MAP" in str(e), "the error names what was offered"


def test_rows_parse_to_sunday_to_saturday_weeks_oldest_first():
    rows = trends.parse_timeline(multiline_body())
    assert rows[0]["week_start"] == "2026-07-12"
    assert rows[0]["week_end"] == "2026-07-18"
    assert rows[0]["value"] == 71
    assert rows[-1] == {"week_start": "2026-08-16", "week_end": "2026-08-22",
                        "value": 75, "partial": True}
    assert [r["partial"] for r in rows] == [False] * 5 + [True], \
        "an omitted isPartial key reads as a complete week"


def test_non_weekly_rows_are_a_contract_change_not_a_resampling_job():
    daily = [(1786233600 + i * 86400, 50 + i, False) for i in range(4)]
    try:
        trends.parse_timeline(multiline_body(daily))
        assert False, "daily rows must raise"
    except RuntimeError as e:
        assert "weekly" in str(e)


def test_a_row_with_two_values_means_joint_normalization_and_raises():
    """A comparison request scales every keyword to the shared maximum, so a
    multi-value row is proof the request was not the one this adapter builds."""
    obj = timeline_json()
    for row in obj["default"]["timelineData"]:
        row["value"] = [50, 60]
    try:
        trends.parse_timeline(")]}',\n" + json.dumps(obj))
        assert False, "two values per row must raise"
    except RuntimeError as e:
        assert "one keyword" in str(e)


def test_an_empty_timeline_raises_rather_than_returning_empty():
    try:
        trends.parse_timeline(")]}',\n" + json.dumps(
            {"default": {"timelineData": []}}))
        assert False, "an empty timeline must raise"
    except RuntimeError as e:
        assert "empty" in str(e)


def test_a_response_that_is_not_json_names_the_contract():
    try:
        trends.parse_timeline("<html>captcha wall</html>")
        assert False, "a non-JSON body must raise"
    except RuntimeError as e:
        assert "contract changed" in str(e)


# --- the archive ------------------------------------------------------------

@with_wire
def test_a_fetch_is_archived_and_the_day_costs_no_further_requests(w):
    snap = trends.snapshot("Tesla", now=at("2026-08-17", 3))
    assert w.cycles() == 1
    assert snap["asof"] == "2026-08-15", \
        "asof is the newest complete week's end, from the rows, not the clock"
    assert snap["window"] == "today 12-m"
    assert snap["points"][-1] == ["2026-08-16", "2026-08-22", 75, True]

    on_disk = json.load(open(os.path.join(
        trends.archive_dir(trends.archive_key("Tesla")), "2026-08-17.json")))
    assert on_disk["fetched_at"] == "2026-08-17T03:00:00Z"
    assert on_disk["points"] == snap["points"]

    # The rest of the day reads the file: 429 budget is spent once per day.
    for hour in (9, 15, 21):
        trends.snapshot("Tesla", now=at("2026-08-17", hour))
    assert w.cycles() == 1, w.fetched


@with_wire
def test_the_archive_key_spells_out_query_and_geo(w):
    assert trends.archive_key("Tesla") == "Tesla.geo-US"
    assert trends.archive_key("iPhone", "US") == "iPhone.geo-US"
    assert "/" not in trends.archive_key("weird query/geo")


@with_wire
def test_the_partial_week_is_archived_but_never_enters_the_series(w):
    """The in-progress row moved five points between two probe fetches on one
    day; it is evidence worth keeping and a value worth refusing to score."""
    trends.snapshot("Tesla", now=at("2026-08-17"))
    got = trends.as_archived("Tesla", fetch=False)
    assert got[-1] == {"date": "2026-08-15", "value": 74}
    assert all(p["date"] != "2026-08-22" for p in got)
    # But the archive file itself holds the partial row, flagged.
    snap = trends.read_archive(trends.archive_key("Tesla"),
                               at("2026-08-17").date())
    assert snap["points"][-1][3] is True


@with_wire
def test_a_completed_week_is_frozen_by_the_earliest_snapshot_that_holds_it(w):
    """Sampling jitter and window renormalization both re-read old weeks.
    If a later snapshot could overwrite one, a round's frozen history would
    have moved by resolution time and `resolve.candidate` would hand back a
    re-read old week as the release -- the exact failure the Civiqs archive
    exists to prevent."""
    trends.snapshot("Tesla", now=at("2026-08-17"))
    before = trends.as_archived("Tesla", fetch=False)

    # A week later: the window slid one week (the oldest row fell off), every
    # historical value was re-sampled +2, the old partial week has completed
    # at 71 (not the 75 it showed mid-week), and a new partial week trails.
    later = [(t, v + 2, False) for t, v, _ in _ROWS[1:-1]]
    later.append((_ROWS[-1][0], 71, False))            # 2026-08-16, now final
    later.append((_ROWS[-1][0] + 604800, 40, True))    # the new partial week
    w.serve(multiline=multiline_body(later))
    trends.snapshot("Tesla", now=at("2026-08-24"))
    after = trends.as_archived("Tesla", fetch=False)

    assert after[:len(before)] == before, \
        "already-archived weeks must not move when upstream re-reads them"
    assert after[-1] == {"date": "2026-08-22", "value": 71}, \
        "the newly completed week enters at its first archived value"


@with_wire
def test_no_archive_and_no_network_raises_but_a_stale_archive_serves(w):
    def boom(session, url, params=None):
        raise RuntimeError("simulated outage")
    trends._get = boom
    try:
        trends.as_archived("Tesla")
        assert False, "no archive and no network must raise"
    except RuntimeError:
        pass
    w.serve()
    trends.snapshot("Tesla", now=at("2026-08-17"))
    trends._get = boom
    # A refresh that dies here files no forecasts for any tracker, and rounds
    # lock on a hard deadline -- so a dead source degrades to a stale series.
    got = trends.as_archived("Tesla", now=at("2026-08-18"))
    assert got[-1]["date"] == "2026-08-15"


@with_wire
def test_a_snapshot_with_no_complete_week_refuses_to_archive(w):
    all_partial = [(1786838400, 75, True)]
    w.serve(multiline=multiline_body(all_partial))
    try:
        trends.snapshot("Tesla", now=at("2026-08-17"))
        assert False, "a record that could never resolve must not be written"
    except RuntimeError as e:
        assert "complete week" in str(e)
    assert trends.archive_days(trends.archive_key("Tesla")) == []


# --- what the resolver would do with it -------------------------------------

@with_wire
def test_week_end_dating_answers_a_weekly_round_with_the_new_week(w):
    """The load-bearing dating decision, checked against the real resolver:
    a round locked mid-week resolves against the first week completed after
    the lock, because that week's Saturday date is new to the frozen history.
    Dated by week start, the same point would carry a date the lock snapshot
    had already frozen past."""
    from ssa import refresh, resolve
    trends.snapshot("Tesla", now=at("2026-08-17"))
    later = list(_ROWS[:-1]) + [(_ROWS[-1][0], 71, False),
                                (_ROWS[-1][0] + 604800, 40, True)]
    w.serve(multiline=multiline_body(later))
    trends.snapshot("Tesla", now=at("2026-08-24"))
    series = {"s": trends.as_archived("Tesla", fetch=False)}

    lock = "2026-08-19T22:00:00Z"
    hist = [p for p in series["s"] if p["date"] < lock[:10]]
    saved = refresh.read_lock_snapshot
    refresh.read_lock_snapshot = lambda rid: {"history": hist}
    try:
        point, why = resolve.candidate(
            {"round_id": "trends-test", "series": "s", "lock_at": lock}, series)
    finally:
        refresh.read_lock_snapshot = saved
    assert point == {"date": "2026-08-22", "value": 71}, (point, why)


# --- the registry ------------------------------------------------------------

def test_both_series_are_registered_without_an_instrument():
    for sid, query in (("trends_tesla", "Tesla"), ("trends_iphone", "iPhone")):
        spec = series_registry.SERIES[sid]
        assert spec["source"] == "trends", sid
        assert spec["trends"]["query"] == query
        assert spec["unit"] == "search interest index (0-100, 12-month window)"
        d = series_registry.describe(sid)
        assert "0-100" in d["question"] and "12-month" in d["question"]
        assert "behavioral" in d["methodology"]
        assert "no one was asked anything" in d["methodology"].lower()
        # A behavioral target has no instrument to give a persona, permanently:
        # the quantity only exists as an aggregate over behavior.
        assert series_registry.survey(sid) is None, sid


@with_wire
def test_build_all_uses_the_adapter_and_accepts_an_injected_override(w):
    saved = series_registry.SERIES
    series_registry.SERIES = {k: v for k, v in saved.items()
                              if v["source"] == "trends"}
    try:
        given = [{"date": "2026-08-15", "value": 74}]
        out = series_registry.build_all(sources={"trends": {
            "trends_tesla": given,
            "trends_iphone": [{"date": "2026-08-15", "value": 52}],
        }})
        assert out["trends_tesla"] == given
        assert out["trends_tesla"] is not given, "must not alias"
        assert w.cycles() == 0, "an injected series must not fetch"

        try:
            series_registry.build_all(sources={"trends": {
                "trends_tesla": [],
                "trends_iphone": [{"date": "2026-08-15", "value": 52}],
            }})
            assert False, "an empty series must raise"
        except RuntimeError as e:
            assert "zero points" in str(e)
    finally:
        series_registry.SERIES = saved


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for t in TESTS:
        t()
        print("ok", t.__name__)
    print(f"\n{len(TESTS)} tests passed")
