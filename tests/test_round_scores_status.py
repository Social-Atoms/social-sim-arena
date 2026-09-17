"""A scored profile or ranking round is a resolved round.

Run: PYTHONPATH=. python tests/test_round_scores_status.py

`refresh.round_status` reads `resolutions/resolved.json`, which only number
rounds write to. A profile or ranking round is scored from its archived series
inside the board builders, so until `attach_round_scores` flips it, the round's
own row said `awaiting_resolution` beside a board that had already scored it;
the question page then showed a scored question as waiting, with the number
template's empty Error / CRPS columns. Seen live on trends-basket-2026-09-05
and wiki-top10-2026-09-06.
"""
from ssa import refresh


def rounds():
    return [
        {"round_id": "p", "status": "awaiting_resolution",
         "target_type": "profile_energy"},
        {"round_id": "k", "status": "awaiting_resolution",
         "target_type": "ranking_list"},
        {"round_id": "unscored", "status": "awaiting_resolution",
         "target_type": "ranking_list"},
        {"round_id": "n", "status": "resolved", "target_type": "continuous_normal",
         "resolution": {"value": 1.5}},
    ]


PROFILE = {"rounds": [{
    "round_id": "p", "outcome": {"a": 1.0, "b": 2.0},
    "resolution": {"method": "cells as of release", "release_date": "2026-09-05"},
    "entries": [{"entrant": "x", "energy": 1.2464, "skill": 0.1,
                 "level": 0.5, "structure": 0.7}],
}]}
RANKING = {"rounds": [{
    "round_id": "k", "outcome": ["A", "B", "C"],
    "resolution": {"week_start": "2026-08-31", "week_end": "2026-09-06"},
    "entries": [{"entrant": "x", "loss": 0.806, "skill": -0.2,
                 "exact_positions": 1}],
}]}


def test_scored_list_rounds_become_resolved_with_their_outcome():
    rs = rounds()
    refresh.attach_round_scores(rs, PROFILE, RANKING)
    by = {r["round_id"]: r for r in rs}
    assert by["p"]["status"] == "resolved"
    assert by["p"]["resolution"]["outcome"] == {"a": 1.0, "b": 2.0}
    assert by["p"]["resolution"]["method"] == "cells as of release"
    assert by["p"]["scores"] == {"x": {"energy": 1.2464, "skill": 0.1}}
    assert by["k"]["status"] == "resolved"
    assert by["k"]["resolution"]["outcome"] == ["A", "B", "C"]
    # The key the ranking chart already reads.
    assert by["k"]["resolution"]["items"] == ["A", "B", "C"]
    assert by["k"]["scores"] == {"x": {"loss": 0.806, "skill": -0.2}}
    print("ok test_scored_list_rounds_become_resolved_with_their_outcome")


def test_unscored_and_number_rounds_are_left_alone():
    rs = rounds()
    refresh.attach_round_scores(rs, PROFILE, RANKING)
    by = {r["round_id"]: r for r in rs}
    assert by["unscored"]["status"] == "awaiting_resolution"
    assert "resolution" not in by["unscored"]
    assert by["n"]["resolution"] == {"value": 1.5}
    assert "scores" not in by["n"]
    print("ok test_unscored_and_number_rounds_are_left_alone")


def test_resolved_round_count_includes_list_rounds():
    rs = rounds()
    refresh.attach_round_scores(rs, PROFILE, RANKING)
    assert sum(1 for r in rs if r["status"] == "resolved") == 3
    print("ok test_resolved_round_count_includes_list_rounds")


if __name__ == "__main__":
    test_scored_list_rounds_become_resolved_with_their_outcome()
    test_unscored_and_number_rounds_are_left_alone()
    test_resolved_round_count_includes_list_rounds()
