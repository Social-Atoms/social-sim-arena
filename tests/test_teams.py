"""A GitHub owner with several accounts ranks as one team row (2026-09-28).

The overall score is per GitHub id. Per-round detail stays per account; a
second member answering the same round is scored on that round but counts
toward the team once, taking the member forecast submitted first.
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import refresh  # noqa: E402

ENTRANTS = [
    {"entrant_id": "vac-a", "type": "participant", "github": "owner", "organization": "Org", "name": "A"},
    {"entrant_id": "vac-b", "type": "participant", "github": "owner", "organization": "Org", "name": "B"},
    {"entrant_id": "solo", "type": "participant", "github": "other", "name": "Solo"},
    {"entrant_id": "test-jay", "type": "participant", "github": "owner", "name": "house test"},
    {"entrant_id": "model", "type": "llm", "name": "Model"},
]


def _round(tmp, rid, forecasts):
    d = os.path.join(tmp, "forecasts", rid)
    os.makedirs(d)
    for eid, (mean, filed) in forecasts.items():
        fc = {"entrant": eid, "round_id": rid, "topline": {"mean": mean, "sd": 1.0}}
        if filed:
            fc["notes"] = f"filed={filed}, ssa-agent"
        with open(os.path.join(d, eid + ".json"), "w") as fh:
            json.dump(fc, fh)
    return {"round_id": rid, "baselines": {"persistence": {"mean": 50.0, "sd": 1.0}}}


def _board(forecasts_by_round):
    teams = refresh.load_teams(ENTRANTS)
    with tempfile.TemporaryDirectory() as tmp:
        saved = refresh.FORECASTS, refresh.ROOT
        refresh.FORECASTS, refresh.ROOT = os.path.join(tmp, "forecasts"), tmp
        try:
            rounds = [_round(tmp, rid, f) for rid, f in forecasts_by_round.items()]
            resolved = {rid: {"value": 50.0} for rid in forecasts_by_round}
            board = refresh.build_leaderboard(rounds, resolved, teams)
        finally:
            refresh.FORECASTS, refresh.ROOT = saved
    return {row["entrant"]: row for row in board}, rounds


def test_only_multi_account_owners_become_teams():
    teams = refresh.load_teams(ENTRANTS)
    assert list(teams) == ["team-owner"], teams
    assert teams["team-owner"]["members"] == ["vac-a", "vac-b"], \
        "a house test account must not join a team"
    assert teams["team-owner"]["name"].startswith("Org"), teams


def test_team_members_rank_as_one_row_over_their_rounds():
    board, rounds = _board({
        "r1": {"vac-a": (50.0, None), "solo": (52.0, None)},
        "r2": {"vac-b": (51.0, None), "solo": (50.0, None)},
    })
    assert "vac-a" not in board and "vac-b" not in board, board
    assert board["team-owner"]["rounds"] == 2, board["team-owner"]
    assert board["solo"]["rounds"] == 2
    assert set(rounds[0]["scores"]) == {"vac-a", "solo"}, "per-round detail stays per account"


def test_a_round_two_members_answered_counts_the_first_filed_once():
    board, rounds = _board({
        "r1": {"vac-a": (60.0, "2026-09-27T12:00Z"), "vac-b": (50.0, "2026-09-27T09:00Z")},
    })
    team = board["team-owner"]
    assert team["rounds"] == 1, "one round must count once for the team"
    assert team["mean_crps"] == round(rounds[0]["scores"]["vac-b"]["crps"], 3), \
        "the earlier-filed member (vac-b) is the one that counts"
    assert set(rounds[0]["scores"]) == {"vac-a", "vac-b"}


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
