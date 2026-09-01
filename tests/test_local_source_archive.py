"""Residential source courier contracts. No network and no repository writes.

Run: PYTHONPATH=. python tests/test_local_source_archive.py
"""
import importlib.util
import os
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = importlib.util.spec_from_file_location(
    "local_source_archive", os.path.join(ROOT, "tools", "local_source_archive.py"))
courier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(courier)


ROUND = {
    "round_id": "wiki-test-2026-09-06",
    "tracker": "wikipedia",
    "target_type": "ranking_list",
    "ranking": {
        "kind": "wiki_top10",
        "length": 10,
        "loss": "rbo",
        "rbo_p": 0.9,
        "week_start": "2026-08-31",
        "week_end": "2026-09-06",
        "project": "en.wikipedia",
        "access": "all-access",
        "exclusions": "main_page_and_namespaces_v1",
    },
}


def test_wikipedia_courier_matches_the_rounds_seven_week_horizon():
    spec, days = courier.wikipedia_days(ROUND)
    assert spec["kind"] == "wiki_top10"
    assert len(days) == 7 * (courier.ranking_round.HISTORY_WEEKS + 1), len(days)
    assert min(days) == date(2026, 7, 20), min(days)
    assert max(days) == date(2026, 9, 6), max(days)
    assert courier.wikipedia_days({"tracker": "wikipedia"}) is None


def test_courier_deduplicates_days_and_never_asks_for_a_settling_day():
    calls = []
    saved = courier.wikipedia_adapter.top_snapshot

    def top_snapshot(day, project, access, fetch, now):
        assert fetch is True
        calls.append((day, project, access, now))
        return {"Article": 100}

    courier.wikipedia_adapter.top_snapshot = top_snapshot
    try:
        # Sep 2 and later are inside the two-day finality lag. Repeating the
        # round proves the union is keyed by source and day, not by round.
        now = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
        ok, failed = courier.archive_wikipedia(
            now=now, season={"rounds": [ROUND, dict(ROUND)]})
    finally:
        courier.wikipedia_adapter.top_snapshot = saved
    assert failed == [], failed
    assert ok == len(calls) == 44, (ok, len(calls))
    assert len({c[0] for c in calls}) == len(calls)
    assert max(c[0] for c in calls) == date(2026, 9, 1)
    assert all(c[1:3] == ("en.wikipedia", "all-access") for c in calls)


def test_one_wikipedia_failure_is_named_without_skipping_the_other_days():
    calls = []
    saved = courier.wikipedia_adapter.top_snapshot

    def top_snapshot(day, *args, **kwargs):
        calls.append(day)
        if day == date(2026, 8, 4):
            raise RuntimeError("upstream refused this day")
        return {"Article": 100}

    courier.wikipedia_adapter.top_snapshot = top_snapshot
    try:
        ok, failed = courier.archive_wikipedia(
            now=datetime(2026, 9, 20, tzinfo=timezone.utc),
            season={"rounds": [ROUND]})
    finally:
        courier.wikipedia_adapter.top_snapshot = saved
    assert len(calls) == 49 and ok == 48
    assert len(failed) == 1
    assert "2026-08-04" in failed[0] and "upstream refused" in failed[0]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print("ok", test.__name__)
    print(f"{len(tests)} passed")
