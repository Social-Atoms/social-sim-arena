"""Source health: telling a flake apart from an outage. No network.

Run: python tests/test_health.py
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa import health

NOW = datetime(2026, 8, 16, 12, tzinfo=timezone.utc)


def stamp(days_ago):
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def row(rows, name):
    return next(r for r in rows if r["source"] == name)


def test_a_healthy_source_is_ok_on_both_clocks():
    m = {"sb_approval": {"fetched_at": stamp(0.2), "changed_at": stamp(0.2)}}
    assert row(health.check(NOW, m), "sb_approval")["state"] == "ok"


def test_our_fetches_stopping_is_failing_not_stale():
    """Two different problems. `failing` is ours -- the request stopped
    working. `stale` is theirs -- they stopped publishing, or we are being
    served a cache."""
    m = {"sb_approval": {"fetched_at": stamp(5), "changed_at": stamp(5)}}
    assert row(health.check(NOW, m), "sb_approval")["state"] == "failing"


def test_a_source_that_answers_with_the_same_body_forever_is_stale():
    """The failure that hides. Every request succeeds, the site renders, and
    the numbers have not moved in a fortnight."""
    m = {"sb_approval": {"fetched_at": stamp(0.1), "changed_at": stamp(14)}}
    assert row(health.check(NOW, m), "sb_approval")["state"] == "stale"


def test_civiqs_uses_the_upstream_reading_clock_not_the_snapshot_filename():
    """A fresh HTTP-200 snapshot cannot wash a frozen model output green."""
    now = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory(prefix="ssa-civiqs-health-") as archive:
        key = os.path.join(archive, "approve_president_trump_2025")
        os.makedirs(key)
        with open(os.path.join(key, "2026-09-12.json"), "w") as handle:
            json.dump({"end_date": "2026-08-01"}, handle)
        got = row(health.check(
            now, manifest={}, budgets={"civiqs": (2, 4)},
            civiqs_archive=archive,
            civiqs_change_budgets={
                "approve_president_trump_2025": 4,
            }), "civiqs")
    assert got["fetched_days"] == 0.5
    assert got["changed_days"] == 42.5
    assert got["newest_snapshot"] == "2026-09-12"
    assert got["newest_reading"] == "2026-08-01"
    assert got["state"] == "stale"


def test_civiqs_fresh_weekly_key_cannot_hide_a_stale_daily_key():
    now = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)
    key_budgets = {
        "approve_president_trump_2025": 4,
        "describe_feeling_us": 10,
    }
    with tempfile.TemporaryDirectory(prefix="ssa-civiqs-scoped-") as archive:
        for key, reading in (
                ("approve_president_trump_2025", "2026-08-01"),
                ("describe_feeling_us", "2026-09-05")):
            directory = os.path.join(archive, key)
            os.makedirs(directory)
            with open(os.path.join(directory, "2026-09-12.json"), "w") as handle:
                json.dump({"end_date": reading}, handle)
        got = row(health.check(
            now, manifest={}, budgets={"civiqs": (2, 4)},
            civiqs_archive=archive,
            civiqs_change_budgets=key_budgets), "civiqs")
    assert got["state"] == "stale"
    assert got["stale_keys"] == ["approve_president_trump_2025"]
    assert got["failing_keys"] == [] and got["missing_keys"] == []
    assert got["changed_days"] == 42.5
    assert got["budget_change_days"] == 4


def test_civiqs_weekly_emotion_at_seven_days_is_not_stale():
    now = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory(prefix="ssa-civiqs-weekly-") as archive:
        key = os.path.join(archive, "describe_feeling_us")
        os.makedirs(key)
        with open(os.path.join(key, "2026-09-12.json"), "w") as handle:
            json.dump({"end_date": "2026-09-05"}, handle)
        got = row(health.check(
            now, manifest={}, budgets={"civiqs": (2, 4)},
            civiqs_archive=archive,
            civiqs_change_budgets={"describe_feeling_us": 10}), "civiqs")
    assert got["state"] == "ok"
    assert got["changed_days"] == 7.5
    assert got["budget_change_days"] == 10
    assert got["stale_keys"] == []


def test_the_budget_is_per_source_because_staleness_means_different_things():
    """Michigan publishes twice a month; a fortnight unchanged is normal there
    and an outage for a daily tracker. One global threshold is either useless
    or noisy."""
    m = {"umich": {"fetched_at": stamp(0.1), "changed_at": stamp(14)},
         "sb_approval": {"fetched_at": stamp(0.1), "changed_at": stamp(14)}}
    rows = health.check(NOW, m)
    assert row(rows, "umich")["state"] == "ok"
    assert row(rows, "sb_approval")["state"] == "stale"


def test_a_source_never_fetched_is_unknown_not_broken():
    rows = health.check(NOW, {})
    assert {r["state"] for r in rows if r["source"] != "civiqs"} == {"unknown"}
    assert health.problems(rows) == [], "nothing to report before the first run"


def test_problems_are_exactly_the_two_actionable_states():
    m = {"umich": {"fetched_at": stamp(0.1), "changed_at": stamp(0.1)},
         "sb_approval": {"fetched_at": stamp(9), "changed_at": stamp(9)},
         "sb_generic": {"fetched_at": stamp(0.1), "changed_at": stamp(20)}}
    got = {r["source"]: r["state"] for r in health.problems(health.check(NOW, m))}
    assert got == {"sb_approval": "failing", "sb_generic": "stale"}, got


def test_the_report_renders_every_row_including_the_unknown_ones():
    lines = health.report(health.check(NOW, {}))
    assert len(lines) >= 4
    assert any("never fetched" in x for x in lines)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
