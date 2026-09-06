"""The weekly batch calendar: one deadline a week, and the cutover that dates it."""
from datetime import datetime, timedelta, timezone

from ssa import batches


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_deadline_is_the_monday_noon_before_the_lock():
    # Wednesday 2026-09-16 14:00Z -> Monday 2026-09-14 12:00Z.
    d = batches.deadline_for("2026-09-16T14:00:00Z")
    assert iso(d) == "2026-09-14T12:00:00Z"
    # Sunday 2026-09-20 14:00Z -> the same Monday, six days earlier.
    assert iso(batches.deadline_for("2026-09-20T14:00:00Z")) == \
        "2026-09-14T12:00:00Z"
    print("ok test_deadline_is_the_monday_noon_before_the_lock")


def test_a_lock_on_the_deadline_falls_to_the_previous_batch():
    """Strictly before, or a round would be answered at the instant it locks."""
    same = batches.deadline_for("2026-09-14T12:00:00Z")
    assert iso(same) == "2026-09-07T12:00:00Z"
    # Two hours after the deadline still belongs to that deadline: the 10
    # Monday-locking rounds of season 0 sit here, at a 0.08-day horizon.
    assert iso(batches.deadline_for("2026-09-14T14:00:00Z")) == \
        "2026-09-14T12:00:00Z"
    print("ok test_a_lock_on_the_deadline_falls_to_the_previous_batch")


def test_every_deadline_is_strictly_before_its_lock():
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    for hours in range(0, 24 * 30):          # a month, hour by hour
        lock = start + timedelta(hours=hours)
        assert batches.deadline_for(lock) < lock
        assert (lock - batches.deadline_for(lock)) <= timedelta(days=7)
    print("ok test_every_deadline_is_strictly_before_its_lock")


def test_every_round_closes_at_its_own_lock():
    """The rule the whole participant surface rests on. `governed_by_batch`
    survives as a grouping question -- which week a round is listed under --
    and decides nothing about validity."""
    for lock in ("2026-08-28T14:00:00Z", "2026-09-16T14:00:00Z",
                 "2026-10-30T22:00:00Z"):
        assert iso(batches.effective_deadline(lock)) == lock
        assert batches.freeze_at(lock) == batches.effective_deadline(lock)
    assert not batches.governed_by_batch("2026-08-28T14:00:00Z")
    assert batches.governed_by_batch("2026-09-16T14:00:00Z")
    print("ok test_every_round_closes_at_its_own_lock")


def test_the_null_freezes_where_the_entrant_answered():
    """The whole point: same instant, so neither reads what the other cannot."""
    for lock in ("2026-09-16T14:00:00Z", "2026-08-28T14:00:00Z"):
        assert batches.freeze_at(lock) == batches.effective_deadline(lock)
    print("ok test_the_null_freezes_where_the_entrant_answered")


def test_the_horizon_is_the_distance_to_the_answer():
    """Deadline to release, which is what makes two rounds comparable. It is
    2.0 days for a round locking at release - 48h and larger for one that has
    to lock before the period it measures."""
    assert batches.horizon_days("2026-09-16T14:00:00Z",
                                "2026-09-18T14:00:00Z") == 2.0
    assert batches.horizon_days("2026-09-11T14:00:00Z",
                                "2026-09-22T14:00:00Z") == 11.0
    # Without a release there is no horizon to report, and none is invented.
    assert batches.horizon_days("2026-08-28T14:00:00Z") is None
    print("ok test_the_horizon_is_the_distance_to_the_answer")


def test_one_batch_id_per_week():
    ids = {batches.batch_of(t) for t in
           ("2026-09-14T14:00:00Z", "2026-09-16T14:00:00Z",
            "2026-09-20T14:00:00Z")}
    assert ids == {"batch-2026-09-14"}
    assert batches.batch_of("2026-09-21T14:00:00Z") == "batch-2026-09-21"
    print("ok test_one_batch_id_per_week")


def test_the_real_season_splits_into_weekly_batches():
    """Against the frozen season file, not a fixture: the calendar has to hold
    for the rounds that actually exist."""
    import json
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "questions", "season0.json")) as fh:
        rounds = json.load(fh)
    rounds = rounds["rounds"] if isinstance(rounds, dict) else rounds
    horizons = []
    for r in rounds:
        lock = r["lock_at"]
        assert batches.effective_deadline(lock) == batches._parse(lock), \
            f"{r['round_id']}: does not close at its own lock"
        h = batches.horizon_days(lock, r["release_at"])
        assert h > 0, f"{r['round_id']}: closes after it releases"
        horizons.append(h)
    # 94 of 116 lock at release - 48h; the rest ask about a period and must
    # lock before it starts, which is a property of those questions.
    two_day = sum(1 for h in horizons if abs(h - 2.0) < 0.05)
    assert two_day > len(rounds) * 0.75, two_day
    assert max(horizons) < 40, max(horizons)
    governed = [r for r in rounds if batches.governed_by_batch(r["lock_at"])]
    print(f"ok test_the_real_season_splits_into_weekly_batches "
          f"({two_day}/{len(rounds)} at a 48-hour horizon, "
          f"{len(governed)} listed under a week)")


def test_every_entrant_is_called_in_the_same_window_before_the_close():
    """The window is 24 hours before the round's own close, for everyone.

    What the arena hands over is frozen at the window's opening, so when
    inside the window an entrant is reached does not change what it saw. The
    window bounds only what an entrant can look up for itself between the
    first call and the last retry, which is why it is a day rather than the
    three it used to be.
    """
    from ssa import harness, refresh
    lock = "2026-09-20T14:00:00Z"
    due = batches.effective_deadline(lock)        # the lock itself
    r = {"round_id": "test", "lock_at": lock}
    assert due == batches._parse(lock)

    inside = due - timedelta(hours=12)            # inside the 24h window
    early = due - timedelta(days=2)               # before it opens
    after = due + timedelta(hours=1)              # closed
    assert refresh.model_jobs_due(r, inside)
    assert not refresh.model_jobs_due(r, early), \
        "buying before the window opens would read a corpus nobody else had"
    assert not refresh.model_jobs_due(r, after), \
        "buying after the close would out-inform every external entrant"

    stamped = f"filed={(due - timedelta(hours=6)):%Y-%m-%dT%H:%MZ}, m"
    assert harness.filed_in_window(stamped, lock)
    stale = f"filed={(due - timedelta(days=9)):%Y-%m-%dT%H:%MZ}, m"
    assert not harness.filed_in_window(stale, lock)
    future = f"filed={(due + timedelta(minutes=1)):%Y-%m-%dT%H:%MZ}, m"
    assert not harness.filed_in_window(future, lock), \
        "a post-deadline timestamp must not count as in the pre-deadline window"

    # The web corpus must use the identical boundary. Before this regression,
    # a valid corpus gathered 2.5 days before the deadline was measured against
    # Sunday's later lock and rejected as eight days "too early".
    gathered = {"asked_at": iso(due - timedelta(hours=12))}
    assert harness._gathered_in_window(gathered, r)
    gathered_after = {"asked_at": iso(due + timedelta(minutes=1))}
    assert not harness._gathered_in_window(gathered_after, r)
    asof = due - timedelta(seconds=harness.FILE_WINDOW_SECONDS)
    assert refresh.information_asof(r) == iso(asof)
    assert asof <= due - timedelta(hours=12), \
        "the shared news corpus must already be complete when calls begin"
    print("ok test_every_entrant_is_called_in_the_same_window_before_the_close")


def test_public_open_status_closes_at_the_participant_deadline():
    """The site must not invite a submission its validator will reject."""
    from ssa import refresh
    r = {
        "round_id": "sunday-round",
        "lock_at": "2026-09-20T14:00:00Z",
        "release_at": "2026-09-22T14:00:00Z",
    }
    due = batches.effective_deadline(r["lock_at"])
    before = due - timedelta(minutes=1)
    after = due + timedelta(minutes=1)
    assert refresh.round_status(r, {}, before) == "open"
    assert refresh.round_status(r, {}, after) == "locked"
    assert after >= due, "the submission validator already considers this late"
    print("ok test_public_open_status_closes_at_the_participant_deadline")


def test_a_round_older_than_the_calendar_behaves_like_every_other():
    """There is one rule now, so an old round needs no exception: it closes at
    its own lock, exactly as a new one does."""
    from ssa import harness, refresh
    lock = "2026-08-28T14:00:00Z"
    assert batches.effective_deadline(lock) == batches._parse(lock)
    r = {"round_id": "old", "lock_at": lock}
    inside = batches._parse(lock) - timedelta(hours=6)
    assert refresh.model_jobs_due(r, inside)
    stamped = f"filed={(batches._parse(lock) - timedelta(hours=6)):%Y-%m-%dT%H:%MZ}, m"
    assert harness.filed_in_window(stamped, lock)
    print("ok test_a_round_older_than_the_calendar_behaves_like_every_other")


def test_every_round_type_resolves_against_the_same_instant_it_froze():
    """The twin of the freeze test, on the resolution side.

    `test_every_round_type_freezes_at_the_same_instant` pins where each round
    type stops *reading*. This pins where each one decides an outcome is new.
    They have to be the same instant: a round that freezes Monday and refuses
    any observation older than Wednesday's lock will refuse to resolve against
    Tuesday's release -- the exact release its entrants were asked to forecast,
    and one none of them could see when they answered.

    This is a regression test with a date on it. `profile_round.frozen_history`
    moved to `batches.freeze_at`; `profile_round.resolution` kept
    `lock_at[:10]`, and `cell_outcome` raised "nothing has published since the
    round froze" about a value published two days after the round froze.
    `civiqs-profile-2026-w38` and `-w39` are both in that shape.
    """
    from ssa import profile_round, ranking_round

    lock = "2026-09-16T14:00:00Z"                  # Wednesday
    freeze = batches.freeze_at(lock)
    assert freeze == batches._parse(lock), \
        "freeze and close are one instant now; nothing may sit between them"
    r = {"round_id": "profile-fixture", "lock_at": lock,
         "release_at": "2026-09-18T14:00:00Z",
         "cells": ["civiqs_net_approval_dem", "civiqs_net_approval_rep"]}

    # Published on the day the round closed: the first observation entrants
    # could not see, and the one they were asked to forecast.
    between = {"date": "2026-09-16", "value": -8.0}
    series = {c: [{"date": "2026-09-07", "value": -9.0}, between]
              for c in r["cells"]}
    out = profile_round.resolution(r, series, cells=r["cells"])
    assert out["values"]["civiqs_net_approval_dem"] == -8.0, \
        "the release entrants were asked to forecast was refused as stale"

    # And the refusal still fires for a value that predates the freeze.
    stale = {c: [{"date": "2026-09-07", "value": -9.0}] for c in r["cells"]}
    try:
        profile_round.resolution(r, stale, cells=r["cells"])
    except ValueError as e:
        assert "predates the freeze" in str(e), str(e)
    else:
        raise AssertionError("a pre-freeze value must not resolve the round")

    # The ranking guard reads the same boundary: a week that ends on the day
    # the round closed is the first one its entrants could not see, and is
    # exactly what the round asks about.
    rr = {"round_id": "ranking-fixture", "lock_at": lock}
    spec = {"kind": "wiki_top10", "length": 3, "loss": "rbo", "rbo_p": 0.9,
            "week_start": "2026-09-16", "week_end": "2026-09-22",
            "closed_set": False, "project": "en.wikipedia",
            "access": "all-access",
            "exclusions": "main_page_and_namespaces_v1"}
    assert spec["week_start"] >= freeze.strftime("%Y-%m-%d"), \
        "the measured week must begin no earlier than the close"
    got = ranking_round.resolution(rr, spec=spec, obs=[
        {"date": "2026-09-22", "items": ["a", "b", "c"]}])
    assert got["week_end"] == "2026-09-22", \
        "the guard refused a week that began when the round closed"
    print("ok test_every_round_type_resolves_against_the_same_instant_it_froze")


def test_the_validators_copy_of_the_calendar_never_drifts():
    """`tools/validate_submission.py` imports nothing, so it carries its own
    copy. A copy that disagrees with the module is worse than no copy: CI would
    accept a submission the pipeline treats as late, or the reverse. Walk a
    year of hourly locks and require agreement to the second."""
    import importlib.util
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "_v", os.path.join(root, "tools", "validate_submission.py"))
    v = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(v)
    assert v.BATCH_WEEKDAY == batches.BATCH_WEEKDAY
    assert v.BATCH_HOUR_UTC == batches.BATCH_HOUR_UTC
    assert v.BATCH_FIRST_DEADLINE == batches.FIRST_DEADLINE
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    for hours in range(0, 24 * 365):
        lock = start + timedelta(hours=hours)
        assert v.effective_deadline(lock) == batches.effective_deadline(lock), \
            f"validator and ssa.batches disagree at {iso(lock)}"
    print("ok test_the_validators_copy_of_the_calendar_never_drifts")


def test_every_round_type_freezes_at_the_same_instant():
    """Scalar, profile and ranking nulls must freeze together, or the headline
    round type is scored against data its entrants never saw.

    This is a regression test with a date on it. `refresh.build_rounds` moved to
    the batch deadline; `profile_round.frozen_history` and
    `ranking_round.frozen_history` kept `lock_at[:10]` through that change,
    while both docstrings claimed to apply `build_rounds`' filter verbatim. The
    three are pinned here so the next person to move one has to move all of
    them.
    """
    from ssa import profile_round, ranking_round
    lock = "2026-09-20T14:00:00Z"
    freeze = batches.freeze_at(lock).strftime("%Y-%m-%d")
    assert freeze == "2026-09-20", freeze          # the round's own close

    r = {"round_id": "t", "lock_at": lock, "cells": ["a_cell", "b_cell"]}
    series = {c: [{"date": d, "value": 1.0} for d in
                  ("2026-09-13", "2026-09-19", "2026-09-21")]
              for c in ("a_cell", "b_cell")}
    got = profile_round.frozen_history(r, series, cells=("a_cell", "b_cell"))
    for cell, hist in got.items():
        assert [p["date"] for p in hist] == ["2026-09-13", "2026-09-19"], \
            f"{cell} did not freeze at the close: {hist}"

    obs = [{"date": d, "items": []} for d in
           ("2026-09-13", "2026-09-19", "2026-09-21")]
    kept = ranking_round.frozen_history({"lock_at": lock}, obs)
    assert [o["date"] for o in kept] == ["2026-09-13", "2026-09-19"], kept

    # There is one rule, so an older round needs no exception.
    old = "2026-08-28T14:00:00Z"
    assert batches.freeze_at(old).strftime("%Y-%m-%d") == "2026-08-28"
    print("ok test_every_round_type_freezes_at_the_same_instant")


if __name__ == "__main__":
    test_deadline_is_the_monday_noon_before_the_lock()
    test_a_lock_on_the_deadline_falls_to_the_previous_batch()
    test_every_deadline_is_strictly_before_its_lock()
    test_every_round_closes_at_its_own_lock()
    test_the_null_freezes_where_the_entrant_answered()
    test_the_horizon_is_the_distance_to_the_answer()
    test_one_batch_id_per_week()
    test_the_real_season_splits_into_weekly_batches()
    test_every_entrant_is_called_in_the_same_window_before_the_close()
    test_public_open_status_closes_at_the_participant_deadline()
    test_a_round_older_than_the_calendar_behaves_like_every_other()
    test_every_round_type_resolves_against_the_same_instant_it_froze()
    test_the_validators_copy_of_the_calendar_never_drifts()
    test_every_round_type_freezes_at_the_same_instant()
    print("14 passed")
