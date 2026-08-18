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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("6 spend-ceiling tests passed")


def test_model_jobs_wait_for_their_window():
    from datetime import datetime, timezone
    from ssa.refresh import model_jobs_due
    now = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    far = {"lock_at": "2026-08-30T14:00:00Z"}     # 12 days out: wait
    due = {"lock_at": "2026-08-20T14:00:00Z"}     # 2 days out: buy
    shut = {"lock_at": "2026-08-18T12:10:00Z"}    # inside the 30-min margin
    assert not model_jobs_due(far, now)
    assert model_jobs_due(due, now)
    assert not model_jobs_due(shut, now)
