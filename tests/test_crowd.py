"""The crowd is an entrant that answers differently: at a round's deadline it files
the equal-weight pool of everyone else's forecast, and from then on it is scored,
counted and shown like any file. Retirement is read off the filings."""
import json
import os
import tempfile
from datetime import datetime, timezone

from ssa import refresh

ROUND = {"round_id": "t-num-1", "lock_at": "2026-09-01T12:00:00Z", "status": "locked",
         "release_at": "2026-09-08T12:00:00Z", "target_type": "scalar"}


def _file(root, rid, entrant, body):
    os.makedirs(os.path.join(root, rid), exist_ok=True)
    with open(os.path.join(root, rid, entrant + ".json"), "w") as f:
        json.dump({"round_id": rid, "entrant": entrant, **body}, f)


def _with_tree(fn):
    keep = refresh.FORECASTS
    with tempfile.TemporaryDirectory() as tmp:
        refresh.FORECASTS = tmp
        try:
            return fn(tmp)
        finally:
            refresh.FORECASTS = keep


def test_the_crowd_files_the_pool_of_the_entrants_once_filing_has_closed():
    def run(tmp):
        _file(tmp, "t-num-1", "a", {"topline": {"mean": 10.0, "sd": 1.0}})
        _file(tmp, "t-num-1", "b", {"topline": {"mean": 14.0, "sd": 1.0}})
        _file(tmp, "t-num-1", "persistence", {"topline": {"mean": 30.0, "sd": 1.5}})
        _file(tmp, "t-num-1", "mocky", {"topline": {"mean": 99.0, "sd": 1.0}, "notes": "MOCK placeholder"})
        assert refresh.file_crowd_forecasts([dict(ROUND)]) == 1
        fc = refresh.read_forecast(os.path.join(tmp, "t-num-1", "crowd.json"))
        assert fc["entrant"] == "crowd"
        t = fc["topline"]
        # the pool of a and b only: the reference and the placeholder stay out
        assert abs(t["mean"] - 12.0) < 0.05, t
        assert "0.5" in t["quantiles"] and len(t["quantiles"]) == 39
        assert t["quantiles"]["0.025"] < 10.0 < t["quantiles"]["0.5"] < 14.0 < t["quantiles"]["0.975"]
        assert fc["notes"].startswith("filed=2026-0") and "pool of the 2 forecasts" in fc["notes"]
        # filed once: a second pass with a new member does not move the crowd
        _file(tmp, "t-num-1", "c", {"topline": {"mean": 50.0, "sd": 1.0}})
        assert refresh.file_crowd_forecasts([dict(ROUND)]) == 0
        assert refresh.read_forecast(os.path.join(tmp, "t-num-1", "crowd.json")) == fc
    _with_tree(run)


def test_no_crowd_while_a_round_is_open_or_for_a_pool_of_one():
    def run(tmp):
        _file(tmp, "t-num-1", "a", {"topline": {"mean": 10.0, "sd": 1.0}})
        _file(tmp, "t-num-1", "b", {"topline": {"mean": 14.0, "sd": 1.0}})
        assert refresh.file_crowd_forecasts([dict(ROUND, status="open")]) == 0
        _file(tmp, "t-num-2", "a", {"topline": {"mean": 10.0, "sd": 1.0}})
        _file(tmp, "t-num-2", "persistence", {"topline": {"mean": 30.0, "sd": 1.5}})
        assert refresh.file_crowd_forecasts([dict(ROUND, round_id="t-num-2")]) == 0
        assert not os.path.exists(os.path.join(tmp, "t-num-2", "crowd.json"))
    _with_tree(run)


def test_the_crowd_is_scored_like_any_file():
    def run(tmp):
        _file(tmp, "t-num-1", "a", {"topline": {"mean": 10.0, "sd": 1.0}})
        _file(tmp, "t-num-1", "b", {"topline": {"mean": 14.0, "sd": 1.0}})
        _file(tmp, "t-num-1", "persistence", {"topline": {"mean": 30.0, "sd": 1.5}})
        r = dict(ROUND, baselines={"persistence": {"mean": 30.0, "sd": 1.5}})
        refresh.file_crowd_forecasts([r])
        board = refresh.build_leaderboard([r], {"t-num-1": {"value": 12.0}})
        rows = {e["entrant"]: e for e in board}
        assert set(rows) == {"a", "b", "persistence", "crowd"}
        assert rows["crowd"]["mean_skill"] > 0                   # sits between a and b, on the answer
        assert r["scores"]["crowd"]["crps"] < r["scores"]["a"]["crps"]
    _with_tree(run)


def test_retirement_follows_the_filings_and_the_registration():
    now = datetime(2026, 9, 11, tzinfo=timezone.utc)
    rounds = [
        {"forecasts": {"old": {"filed": "2026-08-18T03:57Z"}, "fresh": {"filed": "2026-08-30T03:57Z"},
                       "persistence": {"filed": "2026-08-18T03:57Z"}, "crowd": {"filed": "2026-08-18T03:57Z"}}},
        {"forecasts": {"fresh": {"filed": "2026-09-10T03:57Z"}}},
    ]
    entrants = [{"entrant_id": "gone", "status": "retired", "retired_at": "2026-09-05"},
                {"entrant_id": "fresh", "status": "active"}]
    out = refresh.retirement(rounds, entrants, now)
    assert out["old"] == {"retired_at": "2026-09-01", "last_filed": "2026-08-18T03:57Z", "by": "rule"}
    assert out["gone"]["by"] == "registration" and out["gone"]["retired_at"] == "2026-09-05"
    assert "fresh" not in out and "persistence" not in out and "crowd" not in out
    # fourteen days exactly is still in; the fifteenth day is out
    edge = [{"forecasts": {"e": {"filed": "2026-08-28T00:00Z"}}}]
    assert "e" not in refresh.retirement(edge, [], now)
    assert "e" in refresh.retirement(edge, [], datetime(2026, 9, 11, 0, 1, tzinfo=timezone.utc))


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
