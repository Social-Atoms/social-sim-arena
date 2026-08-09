"""Tests for round resolution. Plain asserts, no pytest, no network.

Resolution is the scoring authority: a wrong entry fixes every entrant's score
for that round and looks finished while doing it. So these check the refusals
at least as hard as the successes.
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa import resolve


def T(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _round(rid="r1", series="s", lock="2026-08-12T14:00:00Z",
           release="2026-08-14T14:00:00Z"):
    return {"round_id": rid, "series": series, "lock_at": lock,
            "release_at": release}


def _series(dates_values, name="s"):
    return {name: [{"date": d, "value": v} for d, v in dates_values]}


def test_resolves_the_first_release_after_the_freeze():
    s = _series([("2026-06-01", 49.5), ("2026-07-01", 55.2), ("2026-08-13", 58.0)])
    res, why = resolve.resolve_round(_round(), s, T("2026-08-15T00:00:00Z"))
    assert why is None, why
    assert res["value"] == 58.0
    assert res["observed_date"] == "2026-08-13"


def test_refuses_before_the_release_time():
    s = _series([("2026-07-01", 55.2), ("2026-08-01", 58.0)])
    res, why = resolve.resolve_round(_round(), s, T("2026-08-13T00:00:00Z"))
    assert res is None and "has not passed" in why, why


def test_refuses_when_no_new_release_has_landed():
    """The clock passing does not mean the number exists yet."""
    s = _series([("2026-06-01", 49.5), ("2026-07-01", 55.2)])
    res, why = resolve.resolve_round(_round(), s, T("2026-08-20T00:00:00Z"))
    assert res is None and "no release has landed" in why, why


def test_monthly_label_dates_do_not_confuse_the_answer():
    """Michigan's August value is dated 2026-08-01 and published on the 14th,
    so it sorts before a 2026-08-12 lock while not existing at lock time.
    Resolving by date would answer the August round with September; resolving
    against the frozen snapshot answers it with August."""
    import ssa.refresh as refresh
    real = refresh.read_lock_snapshot
    # the snapshot taken while the round was open: July was the newest value
    refresh.read_lock_snapshot = lambda rid: {
        "history": [{"date": "2026-06-01", "value": 49.5},
                    {"date": "2026-07-01", "value": 55.2}]}
    try:
        s = _series([("2026-06-01", 49.5), ("2026-07-01", 55.2),
                     ("2026-08-01", 58.0), ("2026-09-01", 60.0)])
        res, why = resolve.resolve_round(_round(), s, T("2026-08-15T00:00:00Z"))
        assert why is None, why
        assert res["observed_date"] == "2026-08-01", res
        assert res["value"] == 58.0, "must be August, not September"
    finally:
        refresh.read_lock_snapshot = real


def test_refuses_an_unknown_series():
    res, why = resolve.resolve_round(_round(series="civiqs_net_approval"),
                                     _series([("2026-08-01", 1.0)]),
                                     T("2026-08-20T00:00:00Z"))
    assert res is None and "no series" in why, why


def test_refuses_when_there_was_no_pre_lock_history():
    """Without history there were no baselines, so there is nothing to score
    against and no freeze to define 'next'."""
    s = _series([("2026-08-20", 40.0)])
    res, why = resolve.resolve_round(_round(), s, T("2026-08-25T00:00:00Z"))
    assert res is None and "no frozen history" in why, why


def test_two_rounds_cannot_share_one_observation():
    """Michigan's preliminary and final both ask about one month while the
    series carries one point for it. Answering two questions with one number
    would be silently wrong, so the second is refused and named."""
    season = {"rounds": [
        _round("umich-prelim", lock="2026-08-12T14:00:00Z",
               release="2026-08-14T14:00:00Z"),
        _round("umich-final", lock="2026-08-26T14:00:00Z",
               release="2026-08-28T14:00:00Z"),
    ]}
    s = _series([("2026-07-01", 55.2), ("2026-08-01", 58.0)])
    # Both rounds locked before the August value existed, so both snapshots
    # end at July -- which is exactly why both would claim it.
    import ssa.refresh as refresh
    real = refresh.read_lock_snapshot
    refresh.read_lock_snapshot = lambda rid: {
        "history": [{"date": "2026-07-01", "value": 55.2}]}
    try:
        new, skipped = resolve.resolve_all(season, s, {}, T("2026-08-29T00:00:00Z"))
    finally:
        refresh.read_lock_snapshot = real
    assert len(new) == 1, new
    assert "umich-prelim" in new
    assert any(rid == "umich-final" and "cannot share one answer" in why
               for rid, why in skipped), skipped


def test_existing_resolutions_are_never_overwritten():
    season = {"rounds": [_round("r1")]}
    s = _series([("2026-07-01", 55.2), ("2026-08-01", 58.0)])
    prior = {"r1": {"value": 99.0, "observed_date": "2026-08-01", "series": "s"}}
    new, skipped = resolve.resolve_all(season, s, prior, T("2026-08-20T00:00:00Z"))
    assert new == {}, "a resolved round must not be recomputed"
    merged = resolve.write(prior, {"r1": {"value": 1.0}},
                           path="/tmp/_ssa_resolved_test.json")
    assert merged["r1"]["value"] == 99.0, "write must not clobber"
    os.remove("/tmp/_ssa_resolved_test.json")


def test_pre_lock_history_prefers_the_frozen_snapshot():
    """Resolution must read what the nulls actually saw, not recompute it from
    a series that has changed since."""
    import ssa.refresh as refresh
    real = refresh.read_lock_snapshot
    frozen = [{"date": "2026-08-11", "value": 1.0}]
    refresh.read_lock_snapshot = lambda rid: {"history": frozen}
    try:
        s = _series([("2026-08-11", 1.0), ("2026-08-12", 2.0)])
        assert resolve.pre_lock_history(_round(), s) == frozen
    finally:
        refresh.read_lock_snapshot = real
    # no snapshot -> date filter, the pre-snapshot fallback
    refresh.read_lock_snapshot = lambda rid: None
    try:
        s = _series([("2026-08-11", 1.0), ("2026-08-13", 3.0)])
        assert resolve.pre_lock_history(_round(), s) == [{"date": "2026-08-11", "value": 1.0}]
    finally:
        refresh.read_lock_snapshot = real


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all resolve tests passed")
