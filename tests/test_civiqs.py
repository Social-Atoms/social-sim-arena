"""Civiqs adapter: parsing, units, filters, and the archive the arena resolves against.

Run: PYTHONPATH=. python tests/test_civiqs.py

No network. The fixture below is a real slice of the payload served by
https://civiqs.com/results/approve_president_trump_2025 on 2026-08-12 -- real
epoch timestamps, real fractions, real run id -- trimmed to twelve days so the
expected numbers can be checked by eye against the dashboard.
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa import personas                                          # noqa: E402
from ssa import series as series_registry                         # noqa: E402
from ssa.adapters import civiqs                                   # noqa: E402

TRACKER = "approve_president_trump_2025"

# 2026-07-31 .. 2026-08-11, as published. Epoch milliseconds at UTC midnight.
_APPROVE = [(1785456000000, 0.359), (1785542400000, 0.359), (1785628800000, 0.359),
            (1785715200000, 0.359), (1785801600000, 0.358), (1785888000000, 0.356),
            (1785974400000, 0.355), (1786060800000, 0.354), (1786147200000, 0.353),
            (1786233600000, 0.352), (1786320000000, 0.350), (1786406400000, 0.351)]
_DISAPPROVE = [(t, round(0.949 - v, 3)) for t, v in _APPROVE]
_NEITHER = [(t, 0.053) for t, _ in _APPROVE]


def payload_json(end_date="2026-08-11", run_id="08c88a88", approve=None,
                 filtered=False, days=None):
    """The loader object, shaped exactly like the real one."""
    approve = approve or _APPROVE
    disapprove, neither = _DISAPPROVE, _NEITHER
    if days:                       # a shorter history, on every choice at once
        approve, disapprove, neither = (x[-days:] for x in
                                        (approve, disapprove, neither))
    unfiltered = {"ci_high": {str(t): {"Approve": v + 0.02} for t, v in _APPROVE}}
    return {
        "run_id": run_id,
        "sample_size": 123029,
        "question_body": ("Do you approve or disapprove of the way Donald Trump "
                          "is handling his job as president?"),
        "job_finish_time": end_date + "T01:43:17.853502",
        "end_date": end_date,
        "job_description": {
            "name": TRACKER,
            "population_model": "registered voters",
            # A brace and an escaped quote inside a string value: the payload
            # really does carry these, and a scanner that does not track string
            # state truncates the object here.
            "display_text": 'Donald Trump: Job Approval {"second term"}',
            "predictor_list": ["gender_2", "race_4", "age_4", "education_3",
                               "party_3"],
            "display_net": {"minuend": "Approve", "subtrahend": "Disapprove",
                            "label": "Net Approve"},
        },
        "demographics": [
            {"predictor": "party_3", "demographic": "party", "label": "party",
             "values": ["Democrat", "Republican", "Independent"]},
            {"predictor": "age_4", "demographic": "age", "label": "age",
             "values": ["18-34", "35-49", "50-64", "65+"]},
            {"predictor": "home_state", "demographic": "home_state",
             "label": "state", "values": ["Texas", "Ohio"]},
        ],
        "topline": {
            "unfiltered_topline": unfiltered,
            # Equal to the unfiltered copy unless a filter actually applied.
            "filtered_topline": {"ci_high": {"changed": True}} if filtered
                                else unfiltered,
            "line_chart_data": [
                {"key": "Approve",
                 "values": [{"date": t, "value": v} for t, v in approve]},
                {"key": "Disapprove",
                 "values": [{"date": t, "value": v} for t, v in disapprove]},
                {"key": "Neither approve nor disapprove",
                 "values": [{"date": t, "value": v} for t, v in neither]},
            ],
        },
    }


def page(obj=None, route=civiqs.QUESTION_ROUTE):
    """The payload as it arrives: inline in a much larger HTML document."""
    body = json.dumps(obj if obj is not None else payload_json())
    return ('<!DOCTYPE html><html><head><style>.a{color:red}</style></head>'
            '<body><div id="root"></div><script>window.__remixContext={'
            '"state":{"loaderData":{"routes/_app":{"nav":{"open":false}},'
            f'"{route}":{body}'
            '}}};</script></body></html>')


def index_page():
    obj = {"form": {}, "results": [
        {"end_date": "2026-08-11", "job_description": {
            "name": TRACKER, "display_text": "Donald Trump: Job Approval, "
            "Second Term", "population_model": "registered voters",
            "predictor_list": ["party_3"]}},
        {"end_date": "2026-08-10", "job_description": {
            "name": "track_country", "display_text": "Right Track/Wrong Track",
            "population_model": "registered voters", "predictor_list": []}},
    ]}
    return page(obj, route=civiqs.INDEX_ROUTE)


# --- a scratch archive, so tests never write into the committed one ---------

class Archive:
    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="ssa-civiqs-")
        self.saved = civiqs.ARCHIVE
        civiqs.ARCHIVE = self.dir
        civiqs._payloads.clear()
        self.fetched = []

    def serve(self, *pages):
        """Answer the next N requests with these pages, counting each one."""
        queue = list(pages)

        def fake_get(url):
            self.fetched.append(url)
            return queue.pop(0) if len(queue) > 1 else queue[0]
        civiqs._get = fake_get

    def close(self):
        civiqs.ARCHIVE = self.saved
        civiqs._payloads.clear()
        shutil.rmtree(self.dir, ignore_errors=True)


_REAL_GET = civiqs._get


def with_archive(fn):
    def run():
        a = Archive()
        try:
            fn(a)
        finally:
            civiqs._get = _REAL_GET
            a.close()
    run.__name__ = fn.__name__
    return run


def at(day, hour=12):
    return datetime.fromisoformat(day).replace(hour=hour, tzinfo=timezone.utc)


# --- parsing ----------------------------------------------------------------

def test_payload_is_brace_scanned_out_of_the_page():
    p = civiqs.parse_payload(page())
    assert p["run_id"] == "08c88a88"
    assert p["end_date"] == "2026-08-11"
    # The scanner has to survive a brace and an escaped quote inside a string,
    # and must not stop at the first closing brace it meets.
    assert p["job_description"]["display_text"].endswith('{"second term"}')
    assert civiqs.choices(p) == ["Approve", "Disapprove",
                                 "Neither approve nor disapprove"]


def test_values_are_fractions_upstream_and_points_downstream():
    """0.351 is 35.1 percent. A series left in fractions is off by two orders
    of magnitude in every CRPS while still drawing a plausible chart."""
    pts = civiqs.to_points(civiqs.parse_payload(page()), "Approve")
    assert pts[-1] == {"date": "2026-08-11", "value": 35.1}
    assert pts[0] == {"date": "2026-07-31", "value": 35.9}
    # Rounded at a defined place: 0.354 * 100 is 35.400000000000006 in binary
    # floating point, and that would be committed into a resolution verbatim.
    assert all(repr(p["value"]) == repr(round(p["value"], 2)) for p in pts)


def test_dates_are_epoch_milliseconds_at_utc_midnight():
    assert civiqs._iso(1786406400000) == "2026-08-11"
    assert civiqs._iso(1785456000000) == "2026-07-31"
    pts = civiqs.to_points(civiqs.parse_payload(page()), "Approve")
    assert [p["date"] for p in pts[:3]] == ["2026-07-31", "2026-08-01",
                                            "2026-08-02"]
    assert pts == sorted(pts, key=lambda p: p["date"]), "oldest first"


def test_net_is_approve_minus_disapprove_in_points():
    p = civiqs.parse_payload(page())
    net = civiqs.to_net(p)
    assert net[-1] == {"date": "2026-08-11", "value": round(35.1 - 59.8, 2)}
    assert len(net) == len(_APPROVE)


def test_choices_and_series_that_do_not_exist_raise():
    p = civiqs.parse_payload(page())
    for fn, arg in ((civiqs.to_points, "Somewhat approve"),):
        try:
            fn(p, arg)
            assert False, "a missing choice must raise"
        except KeyError as e:
            assert "Approve" in str(e), "the error names what is available"


def test_index_page_lists_the_catalogue():
    rows = civiqs.parse_index(index_page())
    assert [r["name"] for r in rows] == [TRACKER, "track_country"]
    assert rows[0]["population_model"] == "registered voters"


# --- refusing to invent data ------------------------------------------------

def test_a_payload_with_no_series_raises_rather_than_returning_empty():
    obj = payload_json()
    obj["topline"]["line_chart_data"] = []
    try:
        civiqs.parse_payload(page(obj))
        assert False, "an empty payload must raise"
    except RuntimeError as e:
        assert "line_chart_data" in str(e)


def test_an_unrecognised_tracker_is_named_as_such():
    """Civiqs serves its index page for an unknown slug rather than a 404, so
    the failure looks like a parse error unless it is checked for."""
    try:
        civiqs.parse_payload(index_page(), url="https://civiqs.com/results/nope")
        assert False, "the index page is not a tracker payload"
    except RuntimeError as e:
        assert "not recognised" in str(e), str(e)


def test_a_page_that_changed_shape_raises():
    try:
        civiqs.parse_payload("<html><body>nothing here</body></html>")
        assert False, "a structureless page must raise"
    except RuntimeError as e:
        assert "structure changed" in str(e)


@with_archive
def test_no_archive_and_no_network_is_an_error_not_an_empty_series(a):
    try:
        civiqs.as_displayed(TRACKER, fetch=False)
        assert False, "an absent archive must raise"
    except RuntimeError as e:
        assert "refusing to publish an empty series" in str(e)


# --- subgroup filters -------------------------------------------------------

def test_subgroup_filter_keys_on_the_label_not_the_predictor():
    """`?party=Democrat` filters; `?party_3=Democrat` is silently ignored and
    returns the national series under a subgroup's name."""
    url = civiqs.tracker_url(TRACKER, {"party": "Democrat"})
    assert url.endswith("?party=Democrat"), url
    assert "party_3" not in url
    p = civiqs.parse_payload(page())
    assert civiqs.demographics(p)["party"] == ["Democrat", "Republican",
                                               "Independent"]
    assert "party_3" not in civiqs.demographics(p), \
        "the predictor id is not a filter key"


def test_filter_values_are_url_encoded():
    """`65+` has to reach the server as `65%2B`; a bare plus is read as a space
    and the filter misses, which returns the national series."""
    url = civiqs.tracker_url(TRACKER, {"age": "65+"})
    assert url.endswith("?age=65%2B"), url
    url = civiqs.tracker_url(TRACKER, {"race": "Black or African-American"})
    assert "Black%20or%20African-American" in url


def test_filters_are_sorted_so_one_pair_is_one_archive_directory():
    a = civiqs.tracker_url(TRACKER, {"party": "Democrat", "age": "65+"})
    b = civiqs.tracker_url(TRACKER, {"age": "65+", "party": "Democrat"})
    assert a == b
    assert civiqs.archive_key(TRACKER, {"party": "Democrat", "age": "65+"}) == \
        civiqs.archive_key(TRACKER, {"age": "65+", "party": "Democrat"})
    assert "/" not in civiqs.archive_key(TRACKER, {"state": "New York"})


def test_an_applied_filter_is_detectable_and_an_ignored_one_is_fatal():
    assert civiqs.filters_applied(civiqs.parse_payload(page())) is False
    assert civiqs.filters_applied(
        civiqs.parse_payload(page(payload_json(filtered=True)))) is True


@with_archive
def test_a_filter_civiqs_ignored_never_reaches_the_archive(a):
    a.serve(page())                       # filtered_topline == unfiltered
    try:
        civiqs.snapshot(TRACKER, {"party_3": "Democrat"}, now=at("2026-08-12"))
        assert False, "an ignored filter must raise"
    except RuntimeError as e:
        assert "ignored the subgroup filter" in str(e)
        assert "party" in str(e), "the error lists the labels that do work"
    assert civiqs.archive_days(civiqs.archive_key(TRACKER,
                                                  {"party_3": "Democrat"})) == []


# --- the archive ------------------------------------------------------------

@with_archive
def test_a_fetch_is_archived_and_the_day_costs_no_further_requests(a):
    a.serve(page())
    snap = civiqs.snapshot(TRACKER, now=at("2026-08-12", 9))
    assert len(a.fetched) == 1
    assert snap["end_date"] == "2026-08-11"
    assert snap["run_id"] == "08c88a88"
    assert snap["unit"] == "percentage points"
    assert snap["points"][-1] == ["2026-08-11", 35.1, 59.8, 5.3]

    on_disk = json.load(open(os.path.join(
        civiqs.archive_dir(civiqs.archive_key(TRACKER)), "2026-08-12.json")))
    assert on_disk["fetched_at"] == "2026-08-12T09:00:00Z"
    assert on_disk["points"] == snap["points"]

    # The other three refreshes of the day read the file. This is what keeps a
    # six-hourly cron to one ~2 MB request per tracker per day.
    for hour in (12, 18, 23):
        civiqs.snapshot(TRACKER, now=at("2026-08-12", hour))
    assert len(a.fetched) == 1, a.fetched


@with_archive
def test_the_first_snapshot_is_full_and_later_ones_are_tails(a):
    a.serve(page())
    first = civiqs.snapshot(TRACKER, now=at("2026-08-12"))
    assert first["full_history"] is True
    assert len(first["points"]) == len(_APPROVE)

    saved, civiqs.SNAPSHOT_POINTS = civiqs.SNAPSHOT_POINTS, 3
    try:
        later = civiqs.snapshot(TRACKER, now=at("2026-08-13"))
    finally:
        civiqs.SNAPSHOT_POINTS = saved
    assert later["full_history"] is False
    assert len(later["points"]) == 3, "later snapshots keep a bounded tail"
    # The full first snapshot is the backfill base for every day before the
    # archive began, so a later write must never demote it.
    assert civiqs.read_archive(civiqs.archive_key(TRACKER),
                               at("2026-08-12").date())["full_history"] is True


@with_archive
def test_a_second_fetch_the_same_day_only_wins_with_a_newer_vintage(a):
    """Civiqs's nightly run lands around 01:43 UTC, so the 00:17 refresh
    archives yesterday's vintage and a later one should replace it -- but a
    re-fetch that brings nothing newer must leave the file alone."""
    a.serve(page(payload_json(end_date="2026-08-10", run_id="old")))
    civiqs.snapshot(TRACKER, now=at("2026-08-12", 0))
    assert civiqs.read_archive(civiqs.archive_key(TRACKER),
                               at("2026-08-12").date())["run_id"] == "old"

    a.serve(page(payload_json(end_date="2026-08-10", run_id="same-again")))
    civiqs.snapshot(TRACKER, now=at("2026-08-12", 6))
    assert civiqs.read_archive(civiqs.archive_key(TRACKER),
                               at("2026-08-12").date())["run_id"] == "old", \
        "no newer end_date means no rewrite"

    a.serve(page(payload_json(end_date="2026-08-11", run_id="tonight")))
    civiqs.snapshot(TRACKER, now=at("2026-08-12", 12))
    assert civiqs.read_archive(civiqs.archive_key(TRACKER),
                               at("2026-08-12").date())["run_id"] == "tonight"


@with_archive
def test_a_stale_snapshot_is_rechecked_once_per_cron_interval_not_per_process(a):
    """`ssa.refresh` and `ssa.resolve` run back to back in CI and do not share
    an in-process cache. Re-checking on the date alone made the 00:17 run fetch
    every tracker twice."""
    a.serve(page(payload_json(end_date="2026-08-10")))
    civiqs.snapshot(TRACKER, now=at("2026-08-12", 0))
    assert len(a.fetched) == 1
    civiqs._payloads.clear()                      # a separate process
    civiqs.snapshot(TRACKER, now=at("2026-08-12", 0))
    assert len(a.fetched) == 1, "the sibling process must not refetch"
    civiqs._payloads.clear()
    civiqs.snapshot(TRACKER, now=at("2026-08-12", 6))
    assert len(a.fetched) == 2, "the next cron run does re-check"


# --- what a registered series reads -----------------------------------------

@with_archive
def test_a_point_is_the_freshest_reading_available_on_its_day(a):
    """The rule the whole registration rests on: a point dated d carries the
    number the dashboard showed on d, taken from the earliest snapshot we hold
    that was taken on or after d."""
    a.serve(page())
    civiqs.snapshot(TRACKER, now=at("2026-08-12"))
    daily = civiqs.as_displayed(TRACKER, choice="Approve", fetch=False)

    # 2026-08-12 is the day we looked, and the dashboard was a day behind, so
    # its point is the 2026-08-11 estimate -- not a value for the 12th, which
    # Civiqs had not published.
    assert daily[-1] == {"date": "2026-08-12", "value": 35.1}
    # Days before the archive began are backfilled from that first snapshot,
    # which is the only record of them that exists.
    assert daily[0] == {"date": "2026-07-31", "value": 35.9}
    assert [p["date"] for p in daily][-3:] == ["2026-08-10", "2026-08-11",
                                               "2026-08-12"]


@with_archive
def test_an_archived_day_survives_civiqs_rewriting_its_history(a):
    """Civiqs republishes its entire daily history nightly. If a registered
    series read the live page, every point of a round's frozen history would
    have moved by resolution time and `ssa.resolve` would hand back a revised
    old point as this week's release."""
    a.serve(page())
    civiqs.snapshot(TRACKER, now=at("2026-08-12"))
    before = civiqs.as_displayed(TRACKER, choice="Approve", fetch=False)

    # Tomorrow, with every historical value revised by a point.
    revised = [(t, round(v + 0.01, 3)) for t, v in _APPROVE]
    revised.append((1786492800000, 0.363))            # 2026-08-12, newly added
    a.serve(page(payload_json(end_date="2026-08-12", run_id="revised",
                              approve=revised)))
    civiqs.snapshot(TRACKER, now=at("2026-08-13"))
    after = civiqs.as_displayed(TRACKER, choice="Approve", fetch=False)

    assert after[:len(before)] == before, \
        "already-archived days must not move when upstream revises them"
    assert after[-1] == {"date": "2026-08-13", "value": 36.3}


@with_archive
def test_weekday_sampling_picks_the_day_the_question_asks_about(a):
    a.serve(page())
    civiqs.snapshot(TRACKER, now=at("2026-08-12"))
    fri = civiqs.as_displayed(TRACKER, choice="Approve", weekday=4, fetch=False)
    assert [p["date"] for p in fri] == ["2026-07-31", "2026-08-07"]
    assert fri[-1]["value"] == 35.4
    assert [p["date"] for p in civiqs.as_displayed(
        TRACKER, choice="Approve", weekday=6, fetch=False)] == \
        ["2026-08-02", "2026-08-09"], "Sundays, for contrast"


@with_archive
def test_a_weekday_with_no_observations_raises_rather_than_returning_empty(a):
    """A Monday-and-Tuesday archive asked for Fridays. Publishing [] here would
    give a round a target with no history and no persistence null at all."""
    a.serve(page(payload_json(days=2)))
    civiqs.snapshot(TRACKER, now=at("2026-08-12"))
    assert len(civiqs.as_displayed(TRACKER, choice="Approve", fetch=False)) == 3
    try:
        civiqs.as_displayed(TRACKER, choice="Approve", weekday=4, fetch=False)
        assert False, "a weekday with no observations must raise"
    except RuntimeError as e:
        assert "zero points" in str(e)


@with_archive
def test_a_failed_fetch_serves_the_archive_but_an_empty_archive_raises(a):
    def boom(url):
        raise TimeoutError()
    civiqs._get = boom
    try:
        civiqs.as_displayed(TRACKER, choice="Approve")
        assert False, "no archive and no network must raise"
    except TimeoutError:
        pass
    a.serve(page())
    civiqs.snapshot(TRACKER, now=at("2026-08-12"))
    civiqs._get = boom
    # A refresh that dies here files no forecasts for any tracker, and rounds
    # lock on a hard deadline -- so a dead source degrades to a stale series.
    diagnostics = []
    assert len(civiqs.as_displayed(TRACKER, choice="Approve",
                                   now=at("2026-08-13"),
                                   diagnostics=diagnostics)) > 0
    assert diagnostics[0]["source"] == "civiqs"
    assert diagnostics[0]["scope"] == civiqs.archive_key(TRACKER)
    assert isinstance(diagnostics[0]["error"], TimeoutError)
    assert str(diagnostics[0]["error"]) == ""
    assert "1 snapshots" in diagnostics[0]["archive_evidence"]


@with_archive
def test_a_malformed_live_civiqs_page_does_not_use_the_archive(a):
    a.serve(page())
    civiqs.snapshot(TRACKER, now=at("2026-08-12"))
    a.serve("<html><body>not a loader payload</body></html>")
    try:
        civiqs.as_displayed(TRACKER, choice="Approve",
                            now=at("2026-08-13"), diagnostics=[])
    except RuntimeError as e:
        assert "no Civiqs tracker payload" in str(e), e
    else:
        raise AssertionError("a malformed live page used the archive")


# --- the registry and the rounds that depend on it --------------------------

def test_civiqs_rounds_name_registered_series_with_matching_contracts():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "questions", "season0.json")) as f:
        season = json.load(f)
    civiqs_rounds = [r for r in season["rounds"] if r["tracker"] == "civiqs"]
    assert civiqs_rounds, "the season file still has Civiqs rounds"
    for r in civiqs_rounds:
        assert r["series"] in series_registry.SERIES, r["round_id"]
        spec = series_registry.SERIES[r["series"]]
        assert spec["source"] == "civiqs"
        if r["round_id"] == "civiqs-2026-w34-approval":
            # This already-published legacy round abbreviated the otherwise
            # identical registry unit before exact unit checks existed.
            assert r["unit"] == "net points"
            assert spec["unit"] == "net points (approve minus disapprove)"
        else:
            assert r["unit"] == spec["unit"], r["round_id"]
        # Every reviewed Civiqs round asks for a Friday value; the series has
        # to be sampled on the same day or the resolver answers another target.
        assert spec["civiqs"]["weekday"] == 4
        assert datetime.strptime(r["release_at"], "%Y-%m-%dT%H:%M:%SZ") \
            .weekday() == 4, r["round_id"]


@with_archive
def test_build_all_uses_the_adapter_and_accepts_an_injected_override(a):
    saved = series_registry.SERIES
    series_registry.SERIES = {k: v for k, v in saved.items()
                              if v["source"] == "civiqs"}
    try:
        given = [{"date": "2026-08-07", "value": -23.9}]
        out = series_registry.build_all(sources={"civiqs": {
            "civiqs_net_approval": given,
            "civiqs_net_approval_rep": [{"date": "2026-08-07", "value": 70.2}],
        }})
        assert out["civiqs_net_approval"] == given
        assert out["civiqs_net_approval"] is not given, "must not alias"
        assert len(a.fetched) == 0, "an injected series must not fetch"

        # An override that is empty is a caller bug, not a tracker that went
        # quiet, and publishing it would put a zero on the site.
        try:
            series_registry.build_all(sources={"civiqs": {
                "civiqs_net_approval": [],
                "civiqs_net_approval_rep": [{"date": "2026-08-07", "value": 1}],
            }})
            assert False, "an empty series must raise"
        except RuntimeError as e:
            assert "zero points" in str(e)
    finally:
        series_registry.SERIES = saved


def test_the_registered_series_carry_the_text_an_entrant_is_shown():
    for sid in ("civiqs_net_approval", "civiqs_net_approval_rep"):
        d = series_registry.describe(sid)
        assert "registered voters" in d["question"]
        assert "modeled" in d["methodology"] or "same modeled" in d["methodology"]
        assert d["unit"].startswith("net points")
    # The party cell has no survey instrument on purpose: the persona panel
    # cannot be cut by party, so a persona run would answer a national question
    # and report it as a subgroup.
    assert series_registry.survey("civiqs_net_approval_rep") is None
    s = series_registry.survey("civiqs_net_approval")
    assert s["aggregate"] in personas.AGGREGATORS
    assert s["population"] == "RV"
    assert "neither approve nor disapprove" in s["items"][0]["options"], \
        "dropping the third option redistributes 5 points into the net"


def test_net_approve_share_is_a_difference_over_everyone_asked():
    w = {"p1": 0.25, "p2": 0.25, "p3": 0.25, "p4": 0.25}
    ans = {"p1": {"approval": "approve"}, "p2": {"approval": "approve"},
           "p3": {"approval": "disapprove"},
           "p4": {"approval": "neither approve nor disapprove"}}
    got = personas.aggregate("net_approve_share", ans, w)
    assert abs(got - 25.0) < 1e-9, got     # (50 - 25), not (2/3 - 1/3)


# --- the reason the series is sampled weekly at all -------------------------

def _candidate(series, lock_at="2026-08-12T22:00:00Z"):
    """What `ssa.resolve` would answer a Friday round with. Reads the module;
    changes nothing in it."""
    from ssa import refresh, resolve
    r = {"round_id": "civiqs-2026-w33-approval", "series": "s",
         "lock_at": lock_at}
    hist = [p for p in series["s"] if p["date"] < lock_at[:10]]
    saved = refresh.read_lock_snapshot
    refresh.read_lock_snapshot = lambda rid: {"history": hist}
    try:
        return resolve.candidate(r, series)
    finally:
        refresh.read_lock_snapshot = saved


def test_a_friday_series_answers_a_friday_round_with_fridays_value():
    """`ssa.resolve` answers a round with the first observation the frozen
    history did not contain. On a daily series that is the day after the lock,
    so a Wednesday lock and a Friday release resolve against *Thursday* -- a
    wrong answer, which the resolver rates worse than a missing one. Sampling
    the series on the day the question names is what fixes it."""
    def day(d, v):
        return {"date": d, "value": v}
    daily = {"s": [day("2026-08-%02d" % i, -24.0 + i * 0.1)
                   for i in range(1, 15)]}
    point, why = _candidate(daily)
    assert point["date"] == "2026-08-12", (point, why)   # the day after lock

    fridays = {"s": [day(d, v) for d, v in (("2026-07-24", -22.5),
                                            ("2026-07-31", -23.1),
                                            ("2026-08-07", -23.9),
                                            ("2026-08-14", -24.4))]}
    point, why = _candidate(fridays)
    assert point == day("2026-08-14", -24.4), (point, why)

    # And before Friday lands, the round waits rather than answering early.
    point, why = _candidate({"s": fridays["s"][:-1]})
    assert point is None and "since the lock" in why


def test_a_revised_old_point_would_be_mistaken_for_the_release():
    """Why the archive exists, stated as a test. If the series carried Civiqs's
    live history, the nightly revision of the last pre-lock Friday would be the
    first (date, value) pair the freeze did not contain -- and the resolver
    would score the round against a July number."""
    def day(d, v):
        return {"date": d, "value": v}
    revised = {"s": [day("2026-07-24", -22.5), day("2026-07-31", -23.1),
                     day("2026-08-07", -23.7),            # was -23.9 at lock
                     day("2026-08-14", -24.4)]}
    lock_hist = [day("2026-07-24", -22.5), day("2026-07-31", -23.1),
                 day("2026-08-07", -23.9)]
    from ssa import refresh, resolve
    saved = refresh.read_lock_snapshot
    refresh.read_lock_snapshot = lambda rid: {"history": lock_hist}
    try:
        point, _ = resolve.candidate(
            {"round_id": "r", "series": "s", "lock_at": "2026-08-12T22:00:00Z"},
            revised)
    finally:
        refresh.read_lock_snapshot = saved
    assert point["date"] == "2026-08-07", \
        "this is the failure the archive prevents, demonstrated"


# --- nets with more than one option a side ----------------------------------

# The sentiment trackers are shaped differently from approval: `display_net`
# gives each side as a *list*, and on two of them each list holds two options.
# The numbers below are the real 2026-08-12 economy_us_now reading.
_ECON_DAYS = [1785974400000, 1786060800000, 1786147200000]
_ECON = {"Very good": 0.041, "Fairly good": 0.174,
         "Fairly bad": 0.297, "Very bad": 0.451, "Unsure": 0.037}


def econ_payload(net=None, missing_day=None):
    """A five-option tracker whose net puts two options on each side."""
    def line(key, v):
        vals = [{"date": t, "value": v} for t in _ECON_DAYS]
        if key == missing_day:
            vals = vals[:-1]
        return {"key": key, "values": vals}
    return {
        "run_id": "econ0001", "sample_size": 1193393,
        "question_body": ("How would you rate the condition of the national "
                          "economy right now?"),
        "job_finish_time": "2026-08-12T01:00:00.000000",
        "end_date": "2026-08-12",
        "job_description": {
            "name": "economy_us_now", "population_model": "registered voters",
            "display_text": "National Economy: Current Condition",
            "predictor_list": ["party_3"],
            "display_net": net if net is not None else {
                "label": "Net Good",
                "minuend": ["Very good", "Fairly good"],
                "subtrahend": ["Very bad", "Fairly bad"]},
        },
        "demographics": [],
        "topline": {"unfiltered_topline": {}, "filtered_topline": {},
                    "line_chart_data": [line(k, v) for k, v in _ECON.items()]},
    }


def test_a_net_can_have_two_options_on_each_side():
    """(4.1 + 17.4) - (45.1 + 29.7) = -53.3. The old code passed the side
    straight to to_points, which raises on a list, so four of the five
    sentiment trackers were simply unreadable rather than wrong."""
    p = econ_payload()
    pts = civiqs.to_net(p)
    assert len(pts) == 3, pts
    assert pts[0]["value"] == -53.3, pts[0]


def test_a_one_option_side_may_still_arrive_as_a_list():
    """economy_us_direction declares ["Getting better"] against
    ["Getting worse"] -- a list of one, not a bare string like approval."""
    p = econ_payload(net={"label": "Net Good", "minuend": ["Very good"],
                          "subtrahend": ["Very bad"]})
    assert civiqs.to_net(p)[0]["value"] == round(4.1 - 45.1, 2)


def test_a_side_is_dropped_when_one_of_its_options_is_missing_that_day():
    """A partially published day must not be reported as a smaller total: the
    net would move by the whole of the absent option and look like news."""
    p = econ_payload(missing_day="Fairly good")
    pts = civiqs.to_net(p)
    assert len(pts) == 2, "the incomplete day was kept"


def test_a_tracker_that_declares_no_net_gets_none_rather_than_a_guess():
    """describe_feeling_us offers ten emotions and publishes no net. Any net
    over it would be this repository's construction, so declared_net says so
    and the registry reads a single share instead."""
    assert civiqs.declared_net(econ_payload(net={})) is None
    assert civiqs.declared_net(econ_payload(net={"minuend": ["a"]})) is None, \
        "half a net is not a net"
    # The approval fixture declares one with bare strings, and it normalises.
    got = civiqs.declared_net(payload_json())
    assert got["minuend"] == ["Approve"] and got["subtrahend"] == ["Disapprove"]


def test_the_snapshot_records_which_options_were_added_and_subtracted():
    """A resolution names the exact quantity, and on a five-option tracker
    that is not recoverable from the choice list alone."""
    snap = civiqs.build_snapshot(
        econ_payload(), "economy_us_now", None,
        datetime(2026, 8, 12, 5, 0, tzinfo=timezone.utc), full=True)
    assert snap["display_net"]["minuend"] == ["Very good", "Fairly good"]
    assert snap["display_net"]["subtrahend"] == ["Very bad", "Fairly bad"]
    assert snap["choices"] == list(_ECON), snap["choices"]


def test_the_new_sentiment_series_are_registered_and_carry_no_instrument():
    """Registered so they can be scored; without a `survey` so the persona arm
    refuses them by name instead of guessing a five-option instrument that
    personas.AGGREGATORS has no aggregator for."""
    for sid in ("civiqs_net_econ_now", "civiqs_net_econ_direction",
                "civiqs_net_family_finances", "civiqs_net_inflation_concern",
                "civiqs_angry_share"):
        spec = series_registry.SERIES[sid]
        assert spec["source"] == "civiqs", sid
        assert series_registry.survey(sid) is None, \
            f"{sid} claims an instrument with no aggregator behind it"
        assert series_registry.describe(sid)["methodology"], sid
    # The share series reads one choice; the nets name both sides explicitly.
    assert series_registry.SERIES["civiqs_angry_share"]["civiqs"]["choice"] == "Angry"
    net = series_registry.SERIES["civiqs_net_inflation_concern"]["civiqs"]["net"]
    assert net["minuend"] == ["Very concerned", "Somewhat concerned"], net


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    for t in TESTS:
        t()
        print("ok", t.__name__)
    print(f"\n{len(TESTS)} tests passed")
