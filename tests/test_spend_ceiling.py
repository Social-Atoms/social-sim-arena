"""The per-run spend ceiling buys the nearest locks first and never deadlocks.

The gate it replaced withheld the whole queue whenever the estimate crossed
the ceiling, then met the same queue six hours later -- these tests pin the
two properties that matter: money is actually spent up to the ceiling, and
the jobs that wait are always the ones with the most time left.
"""
import sys

sys.path.insert(0, ".")

from ssa.refresh import affordable                              # noqa: E402


def _job(lock, cost, rid="r", entrant="m"):
    return ({"round_id": rid, "lock_at": lock}, entrant, "/tmp/x.json", cost)


def test_under_ceiling_buys_everything():
    jobs = [_job("2026-09-01T00:00:00Z", 1.0), _job("2026-09-02T00:00:00Z", 2.0)]
    buy, tail, spent = affordable(jobs, 10.0)
    assert len(buy) == 2 and not tail and abs(spent - 3.0) < 1e-9


def test_over_ceiling_spends_the_ceiling_not_nothing():
    jobs = [_job(f"2026-09-0{d}T00:00:00Z", 4.0) for d in range(1, 6)]
    buy, tail, spent = affordable(jobs, 10.0)
    assert len(buy) == 2 and len(tail) == 3     # 4 + 4 fits, a third does not
    assert abs(spent - 8.0) < 1e-9
    assert spent <= 10.0


def test_nearest_lock_wins_regardless_of_input_order():
    late = _job("2026-09-09T00:00:00Z", 6.0, rid="late")
    soon = _job("2026-09-01T00:00:00Z", 6.0, rid="soon")
    buy, tail, _ = affordable([late, soon], 6.0)
    assert [j[0]["round_id"] for j in buy] == ["soon"]
    assert [j[0]["round_id"] for j in tail] == ["late"]


def test_a_backlog_drains_across_runs():
    jobs = [_job(f"2026-09-{d:02d}T00:00:00Z", 3.0, rid=f"r{d}") for d in range(1, 8)]
    remaining, runs = list(jobs), 0
    while remaining:
        buy, remaining, _ = affordable(remaining, 10.0)
        assert buy, "a run under a positive ceiling must always buy something"
        runs += 1
    assert runs == 3                            # 3 + 3 + 1, never a deadlock


def test_one_oversized_job_cannot_block_the_queue_forever():
    # A single job pricier than the ceiling is withheld, but everything
    # cheaper still gets bought -- the whale waits, the school swims.
    whale = _job("2026-09-01T00:00:00Z", 50.0, rid="whale")
    fish = _job("2026-09-02T00:00:00Z", 1.0, rid="fish")
    buy, tail, spent = affordable([whale, fish], 10.0)
    assert [j[0]["round_id"] for j in buy] == ["fish"]
    assert [j[0]["round_id"] for j in tail] == ["whale"]


def test_model_jobs_wait_for_their_window():
    from datetime import datetime, timezone
    from ssa.refresh import model_jobs_due
    now = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    far = {"lock_at": "2026-08-30T14:00:00Z"}     # 12 days out: wait
    due = {"lock_at": "2026-08-19T02:00:00Z"}     # 14 hours out: buy
    shut = {"lock_at": "2026-08-18T12:10:00Z"}    # inside the 30-min margin
    assert not model_jobs_due(far, now)
    assert model_jobs_due(due, now)
    assert not model_jobs_due(shut, now)


def _forecast_file(tmpdir, notes):
    import json as _json
    import os as _os
    path = _os.path.join(tmpdir, "m.json")
    with open(path, "w") as f:
        _json.dump({"round_id": "r", "entrant": "m",
                    "topline": {"mean": 41.0, "sd": 1.5}, "notes": notes}, f)
    return path


def test_one_number_one_forecast():
    """A forecast stamped inside the buy window is final; an unstamped
    pre-window draft is replaced only while the window proper is open; a
    missing file stays buyable to the end as failure insurance."""
    import tempfile
    from datetime import datetime, timezone
    from ssa.refresh import job_still_due
    r = {"round_id": "r", "lock_at": "2026-09-11T14:00:00Z"}
    # The window is the 24 hours before the close; the buy-by that decides
    # whether an unstamped draft is replaced sits halfway through it.
    in_window = datetime(2026, 9, 10, 20, 0, tzinfo=timezone.utc)  # 18h left
    tail = datetime(2026, 9, 11, 8, 0, tzinfo=timezone.utc)        # 6h left
    with tempfile.TemporaryDirectory() as d:
        missing = d + "/nothing.json"
        assert job_still_due(r, missing, in_window)
        assert job_still_due(r, missing, tail), "insurance tail must still buy"
        final = _forecast_file(d, "filed=2026-09-10T18:04Z, x, harness v1; in=ab")
        assert not job_still_due(r, final, in_window), "stamped file reopened"
        assert not job_still_due(r, final, tail)
    with tempfile.TemporaryDirectory() as d:
        draft = _forecast_file(d, "x, harness v1, context=web; in=ab")  # pre-stamp era
        assert job_still_due(r, draft, in_window), "draft not replaced in window"
        assert not job_still_due(r, draft, tail), "draft rewritten after buy-by"


def test_the_stamp_reads_back_and_mocks_never_carry_one():
    from ssa import harness
    lock = "2026-09-11T14:00:00Z"
    assert harness.filed_in_window("filed=2026-09-11T02:00Z, x; in=ab", lock)
    assert not harness.filed_in_window("filed=2026-08-18T07:00Z, x; in=ab", lock)
    assert not harness.filed_in_window("MOCK: no API key configured; in=ab", lock)
    assert not harness.filed_in_window(None, lock)
    stamped = harness.filed_stamp()
    assert harness.filed_in_window(f"filed={stamped}, x; in=ab",
                                   stamped[:10] + "T23:59:00Z")


def test_only_mode_leaves_the_base_roster_home():
    import os as _os
    from ssa.refresh import elicitation_only, elicitation_variants, season_roster
    assert elicitation_only("only:web,web+superfc")
    assert not elicitation_only("web,web+superfc")
    assert elicitation_variants("only:web,web+superfc") == ("web", "web+superfc")
    saved = _os.environ.get("SSA_ELICITATION")
    _os.environ["SSA_ELICITATION"] = "only:web,web+superfc"
    try:
        roster = season_roster()
        assert roster, "only-mode filed nothing at all"
        assert all("-web" in entrant for entrant, _m, _v in
                   [(e[0], e[1], e[2]) for e in roster]), \
            [e[0] for e in roster if "-web" not in e[0]]
    finally:
        if saved is None:
            _os.environ.pop("SSA_ELICITATION", None)
        else:
            _os.environ["SSA_ELICITATION"] = saved


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            n += 1
    print(f"{n} spend-ceiling tests passed")
