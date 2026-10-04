"""The Season 0 main board compares every team on the same rounds (2026-10-01)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa import refresh  # noqa: E402


def _r(rid, lock, scores):
    return {"round_id": rid, "lock_at": lock, "scores": scores}


def test_only_rounds_from_the_common_start_count_across_shapes():
    rounds = [
        _r("old", "2026-09-20T14:00:00Z", {"a": {"skill": 9.0}}),
        _r("num", "2026-09-30T14:00:00Z", {"a": {"crps": 1, "skill": 0.2}, "b": {"skill": -0.2}}),
        _r("profile", "2026-09-30T22:00:00Z", {"a": {"energy": 3, "skill": 0.4}}),
        _r("open", "2026-10-02T14:00:00Z", {}),
        _r("ranking", "2026-10-02T14:00:00Z", {"b": {"loss": 0.5, "skill": 0.1},
                                               "test-jay": {"skill": 5.0}}),
    ]
    board = refresh.build_main_board(rounds, "2026-09-30T00:00:00Z")
    assert board["scored_rounds"] == 3 and "old" not in board["round_ids"], board
    rows = {e["entrant"]: e for e in board["entries"]}
    assert set(rows) == {"a", "b"}, "house test accounts are not ranked"
    assert rows["a"]["answered"] == "2/3" and rows["a"]["mean_skill"] == 0.3
    assert rows["b"]["answered"] == "2/3" and rows["b"]["mean_skill"] == -0.05
    assert [e["entrant"] for e in board["entries"]] == ["a", "b"]


def test_an_empty_window_is_an_empty_board_not_an_error():
    board = refresh.build_main_board([], "2026-09-30T00:00:00Z")
    assert board["entries"] == [] and board["scored_rounds"] == 0


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
