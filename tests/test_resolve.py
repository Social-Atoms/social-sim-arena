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


def test_preliminary_and_final_resolve_to_their_own_values():
    """Michigan revises one monthly row: the preliminary lands mid-month and
    the final at month end. Keyed on date alone the final would never resolve;
    keyed on (date, value) each round gets the value it asked about."""
    import ssa.refresh as refresh
    real = refresh.read_lock_snapshot
    prelim_round = _round("umich-prelim", lock="2026-08-12T14:00:00Z",
                          release="2026-08-14T14:00:00Z")
    final_round = _round("umich-final", lock="2026-08-26T14:00:00Z",
                         release="2026-08-28T14:00:00Z")
    snaps = {
        # locked before August existed
        "umich-prelim": {"history": [{"date": "2026-07-01", "value": 55.2}]},
        # locked after the preliminary landed, so it holds the preliminary
        "umich-final": {"history": [{"date": "2026-07-01", "value": 55.2},
                                    {"date": "2026-08-01", "value": 58.0}]},
    }
    refresh.read_lock_snapshot = lambda rid: snaps.get(rid)
    try:
        # on the 15th the row carries the preliminary
        s = _series([("2026-07-01", 55.2), ("2026-08-01", 58.0)])
        res, why = resolve.resolve_round(prelim_round, s, T("2026-08-15T00:00:00Z"))
        assert why is None and res["value"] == 58.0, (res, why)

        # by the 29th it has been revised to the final
        s2 = _series([("2026-07-01", 55.2), ("2026-08-01", 59.4)])
        res2, why2 = resolve.resolve_round(final_round, s2, T("2026-08-29T00:00:00Z"))
        assert why2 is None, why2
        assert res2["value"] == 59.4, res2
        assert res2["observed_date"] == "2026-08-01"
    finally:
        refresh.read_lock_snapshot = real


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


def test_an_empty_history_never_overwrites_a_real_snapshot():
    """A missing series gives hist == [], which is a broken caller, not a
    tracker whose history vanished. Writing it would erase the only record of
    what the round's nulls saw, and the round could never be resolved."""
    import json as _json
    import tempfile
    import ssa.refresh as refresh

    real_locks = refresh.LOCKS
    tmp = tempfile.mkdtemp()
    refresh.LOCKS = tmp
    try:
        r = _round("r-empty", lock="2099-01-01T00:00:00Z")
        now = T("2026-08-09T00:00:00Z")
        assert refresh.update_lock_snapshot(r, [{"date": "2026-08-01", "value": 5.0}], now)
        assert not refresh.update_lock_snapshot(r, [], now), "empty must be refused"
        with open(refresh.lock_snapshot_path("r-empty")) as f:
            assert _json.load(f)["history"] == [{"date": "2026-08-01", "value": 5.0}]

        # with nothing to lose, an empty first write is still allowed
        r2 = _round("r-fresh", lock="2099-01-01T00:00:00Z")
        assert refresh.update_lock_snapshot(r2, [], now)

        # and the lock itself still wins over everything
        r3 = _round("r-locked", lock="2020-01-01T00:00:00Z")
        assert not refresh.update_lock_snapshot(r3, [{"date": "2026-08-01", "value": 5.0}], now)
    finally:
        refresh.LOCKS = real_locks


def test_a_late_wave_resolves_its_own_round_and_moves_no_deadline():
    """The Economist/YouGov calendar (issue #64) promises a specific thing when
    a wave slips: the round resolves on that same wave, late, and the freeze
    does not follow it. Both halves are here because they are one promise:
    if the snapshot moved, the late wave would be inside the history it is
    supposed to be the answer to, and the round could never resolve at all.

    The round asks about the wave fielded to 2026-09-20, due to enter the
    sheet Tuesday the 22nd and resolve Wednesday the 23rd. It enters Thursday.
    """
    import json as _json
    import tempfile
    import ssa.refresh as refresh

    r = _round("yougov-2026-w39-approval", series="yougov_approval",
               lock="2026-09-21T14:00:00Z", release="2026-09-23T14:00:00Z")
    before = [{"date": "2026-09-06", "value": 47.0},
              {"date": "2026-09-13", "value": 48.0}]
    wave = {"date": "2026-09-20", "value": 45.0}
    after = {"date": "2026-09-27", "value": 46.0}

    real_locks = refresh.LOCKS
    refresh.LOCKS = tempfile.mkdtemp()
    try:
        # Friday, while the round is open: the snapshot is still being written.
        assert refresh.update_lock_snapshot(r, before, T("2026-09-18T04:00:00Z"))

        # Wednesday, the release: the wave has not entered, so there is nothing
        # to resolve against, and the refusal says so rather than reaching for
        # the previous wave.
        res, why = resolve.resolve_round(r, _series(
            [(p["date"], p["value"]) for p in before], "yougov_approval"),
            T("2026-09-23T14:00:00Z"))
        assert res is None and "no release has landed" in why, why

        # Thursday, the wave enters. The deadline passed on Monday, so the
        # refresh that picks it up must not write it into the frozen history.
        assert not refresh.update_lock_snapshot(
            r, before + [wave], T("2026-09-24T13:00:00Z"))
        with open(refresh.lock_snapshot_path(r["round_id"])) as f:
            assert _json.load(f)["history"] == before

        late = _series([(p["date"], p["value"]) for p in before + [wave]],
                       "yougov_approval")
        res, why = resolve.resolve_round(r, late, T("2026-09-24T13:00:00Z"))
        assert why is None, why
        assert (res["observed_date"], res["value"]) == ("2026-09-20", 45.0), res

        # And a week later, with the next wave in too, it is still the round's
        # own wave: late is late, not skipped.
        later = _series([(p["date"], p["value"]) for p in before + [wave, after]],
                        "yougov_approval")
        res, why = resolve.resolve_round(r, later, T("2026-10-01T13:00:00Z"))
        assert why is None, why
        assert (res["observed_date"], res["value"]) == ("2026-09-20", 45.0), res
    finally:
        refresh.LOCKS = real_locks


def test_a_monthly_row_is_not_available_on_the_day_it_is_labelled():
    """Michigan labels a row by the month it describes, not by when it was
    published: 2026-08-01 carries the August preliminary, released 2026-08-14.
    A date filter therefore reads it as predating a 2026-08-12 lock, which is
    why the frozen snapshot is the only safe source of pre-lock history.

    This is not hypothetical. The arena's first live resolution scored
    umich-2026-08-prelim against July's final (55.2, public since July) instead
    of August's preliminary (51.0), because a FRED fallback wrote a snapshot a
    month stale and 'the first release after the freeze' moved with it.
    """
    r = {"round_id": "m1", "series": "umich_sentiment",
         "lock_at": "2026-08-12T14:00:00Z", "release_at": "2026-08-14T14:00:00Z"}
    series = {"umich_sentiment": [
        {"date": "2026-06-01", "value": 49.5},
        {"date": "2026-07-01", "value": 55.2},
        {"date": "2026-08-01", "value": 51.0},        # released 2026-08-14
    ]}
    # A snapshot frozen at the lock holds finals through July -- the August row
    # did not exist yet -- so the next release is the August preliminary.
    good = [{"date": "2026-06-01", "value": 49.5}, {"date": "2026-07-01", "value": 55.2}]
    point, why = _with_snapshot(r, series, good)
    assert point == {"date": "2026-08-01", "value": 51.0}, (point, why)

    # A snapshot a month stale resolves to a number that was already public.
    stale = [{"date": "2026-06-01", "value": 49.5}]
    point, why = _with_snapshot(r, series, stale)
    assert point == {"date": "2026-07-01", "value": 55.2}, (point, why)


def _with_snapshot(r, series, history):
    from ssa import refresh, resolve as R
    saved = refresh.read_lock_snapshot
    refresh.read_lock_snapshot = lambda rid: {"history": history}
    try:
        return R.candidate(r, series)
    finally:
        refresh.read_lock_snapshot = saved


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all resolve tests passed")
