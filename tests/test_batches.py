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


def test_the_cutover_is_dated_and_not_retroactive():
    """Rounds scored under the per-round rule keep it, or scores get rewritten."""
    assert not batches.governed_by_batch("2026-08-28T14:00:00Z")
    assert batches.governed_by_batch("2026-09-16T14:00:00Z")
    # A pre-cutover round's deadline is its own lock, unchanged.
    assert iso(batches.effective_deadline("2026-08-28T14:00:00Z")) == \
        "2026-08-28T14:00:00Z"
    print("ok test_the_cutover_is_dated_and_not_retroactive")


def test_the_null_freezes_where_the_entrant_answered():
    """The whole point: same instant, so neither reads what the other cannot."""
    for lock in ("2026-09-16T14:00:00Z", "2026-08-28T14:00:00Z"):
        assert batches.freeze_at(lock) == batches.effective_deadline(lock)
    print("ok test_the_null_freezes_where_the_entrant_answered")


def test_horizon_is_between_zero_and_seven_days():
    assert abs(batches.horizon_days("2026-09-16T14:00:00Z") - 2.083) < 0.01
    assert abs(batches.horizon_days("2026-09-20T14:00:00Z") - 6.083) < 0.01
    # Pre-cutover rounds have no batch horizon: deadline is the lock itself.
    assert batches.horizon_days("2026-08-28T14:00:00Z") == 0.0
    print("ok test_horizon_is_between_zero_and_seven_days")


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
    for r in rounds:
        lock = r["lock_at"]
        if batches.governed_by_batch(lock):
            assert batches.effective_deadline(lock) < batches._parse(lock), \
                f"{r['round_id']}: deadline is not before its lock"
        else:
            # Pre-cutover: the round is its own deadline, unchanged.
            assert batches.effective_deadline(lock) == batches._parse(lock), \
                f"{r['round_id']}: pre-cutover round moved off its own lock"
        assert 0.0 <= batches.horizon_days(lock) < 7.0, \
            f"{r['round_id']}: horizon outside one week"
    governed = [r for r in rounds if batches.governed_by_batch(r["lock_at"])]
    print(f"ok test_the_real_season_splits_into_weekly_batches "
          f"({len(governed)}/{len(rounds)} rounds under batch rules)")


def test_our_models_are_held_to_the_same_deadline_as_everyone_else():
    """The buy window and the filed stamp anchor on the deadline, not the lock.

    Anchored on the lock, a round locking Sunday would still be buyable all
    week after the Monday deadline closed every external entrant out -- our own
    models reading six days of news nobody else could use. Anchored on the
    deadline, the window shuts when theirs does.
    """
    from ssa import harness, refresh
    lock = "2026-09-20T14:00:00Z"                 # Sunday, horizon 6.1 days
    due = batches.effective_deadline(lock)        # Monday 2026-09-14 12:00Z
    r = {"round_id": "test", "lock_at": lock}

    inside = due - timedelta(days=2, hours=12)    # in the 3d..2d window
    after = due + timedelta(hours=1)              # deadline passed, lock has not
    assert refresh.model_jobs_due(r, inside)
    assert not refresh.model_jobs_due(r, after), \
        "buying after the deadline would out-inform every external entrant"

    stamped = f"filed={(due - timedelta(days=1)):%Y-%m-%dT%H:%MZ}, m"
    assert harness.filed_in_window(stamped, lock)
    stale = f"filed={(due - timedelta(days=9)):%Y-%m-%dT%H:%MZ}, m"
    assert not harness.filed_in_window(stale, lock)
    print("ok test_our_models_are_held_to_the_same_deadline_as_everyone_else")


def test_pre_cutover_rounds_keep_the_window_they_were_bought_in():
    """Anchoring moved; already-bought rounds must not notice."""
    from ssa import harness, refresh
    lock = "2026-08-28T14:00:00Z"
    assert batches.effective_deadline(lock) == batches._parse(lock)
    r = {"round_id": "old", "lock_at": lock}
    inside = batches._parse(lock) - timedelta(days=2, hours=12)
    assert refresh.model_jobs_due(r, inside)
    stamped = f"filed={(batches._parse(lock) - timedelta(days=2)):%Y-%m-%dT%H:%MZ}, m"
    assert harness.filed_in_window(stamped, lock)
    print("ok test_pre_cutover_rounds_keep_the_window_they_were_bought_in")


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


if __name__ == "__main__":
    test_deadline_is_the_monday_noon_before_the_lock()
    test_a_lock_on_the_deadline_falls_to_the_previous_batch()
    test_every_deadline_is_strictly_before_its_lock()
    test_the_cutover_is_dated_and_not_retroactive()
    test_the_null_freezes_where_the_entrant_answered()
    test_horizon_is_between_zero_and_seven_days()
    test_one_batch_id_per_week()
    test_the_real_season_splits_into_weekly_batches()
    test_our_models_are_held_to_the_same_deadline_as_everyone_else()
    test_pre_cutover_rounds_keep_the_window_they_were_bought_in()
    test_the_validators_copy_of_the_calendar_never_drifts()
    print("11 passed")
