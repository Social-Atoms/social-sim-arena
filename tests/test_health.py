"""Source health: telling a flake apart from an outage. No network.

Run: python tests/test_health.py
"""
import os
import sys
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
