"""The crowd is an entrant that answers differently: at a round's deadline it files
the equal-weight pool of everyone else's forecast, and from then on it is scored,
counted and shown like any file. Retirement is read off the filings."""
import json
import os
import tempfile
from datetime import datetime, timezone

from ssa import refresh

ROUND = {"round_id": "t-num-1", "lock_at": "2026-09-01T12:00:00Z", "status": "open",
         "release_at": "2026-09-08T12:00:00Z", "target_type": "scalar"}
# Inside the filing window: six hours before the close, well clear of LOCK_MARGIN_SECONDS.
NOW = datetime(2026, 9, 1, 6, 0, tzinfo=timezone.utc)


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


def test_the_crowd_pools_the_entrants_while_the_window_is_open():
    def run(tmp):
        _file(tmp, "t-num-1", "a", {"topline": {"mean": 10.0, "sd": 1.0}})
        _file(tmp, "t-num-1", "b", {"topline": {"mean": 14.0, "sd": 1.0}})
        _file(tmp, "t-num-1", "persistence", {"topline": {"mean": 30.0, "sd": 1.5}})
        _file(tmp, "t-num-1", "mocky", {"topline": {"mean": 99.0, "sd": 1.0}, "notes": "MOCK placeholder"})
        assert refresh.file_crowd_forecasts([dict(ROUND)], NOW) == 1
        fc = refresh.read_forecast(os.path.join(tmp, "t-num-1", "crowd.json"))
        assert fc["entrant"] == "crowd"
        t = fc["topline"]
        # the pool of a and b only: the reference and the placeholder stay out
        assert abs(t["mean"] - 12.0) < 0.05, t
        assert "0.5" in t["quantiles"] and len(t["quantiles"]) == 39
        assert t["quantiles"]["0.025"] < 10.0 < t["quantiles"]["0.5"] < 14.0 < t["quantiles"]["0.975"]
        assert fc["notes"].startswith("filed=2026-09-01T06:00Z") and "pool of the 2 forecasts" in fc["notes"]
        # rewritten while the window is open: a new member moves the crowd
        assert refresh.file_crowd_forecasts([dict(ROUND)], NOW) == 0, "same pool, no rewrite"
        _file(tmp, "t-num-1", "c", {"topline": {"mean": 50.0, "sd": 1.0}})
        assert refresh.file_crowd_forecasts([dict(ROUND)], NOW) == 1
        moved = refresh.read_forecast(os.path.join(tmp, "t-num-1", "crowd.json"))
        assert moved["topline"]["mean"] > fc["topline"]["mean"], moved
        assert "pool of the 3 forecasts" in moved["notes"]
    _with_tree(run)


def test_a_house_test_entrant_is_not_pooled_into_the_crowd():
    """`test-jay` and `just4test` are ours, registered so the arena really calls
    an endpoint every round and we find out when that path breaks -- which is
    how the first external entrant burned two rounds unnoticed. The endpoint
    they point at answers a fixture, a constant. The crowd excluded only the
    reference forecasters and itself, so that constant would have been pooled
    into a scored, published baseline every round."""
    def run(tmp):
        _file(tmp, "t-num-1", "a", {"topline": {"mean": 10.0, "sd": 1.0}})
        _file(tmp, "t-num-1", "b", {"topline": {"mean": 14.0, "sd": 1.0}})
        _file(tmp, "t-num-1", "test-jay", {"topline": {"mean": 99.0, "sd": 1.0}})
        _file(tmp, "t-num-1", "just4test", {"topline": {"mean": 99.0, "sd": 1.0}})
        assert refresh.file_crowd_forecasts([dict(ROUND)], NOW) == 1
        fc = refresh.read_forecast(os.path.join(tmp, "t-num-1", "crowd.json"))
        assert abs(fc["topline"]["mean"] - 12.0) < 0.05, (
            "a house test entrant reached the crowd: " + repr(fc["topline"]))
        assert "pool of the 2 forecasts" in fc["notes"], fc["notes"]
    _with_tree(run)


def test_never_written_once_the_close_is_near_or_past():
    """The file lands in `forecasts/` as a commit, so it must not appear after the
    round it belongs to closed: tools/audit_landing.py would reject it as late."""
    def run(tmp):
        _file(tmp, "t-num-1", "a", {"topline": {"mean": 10.0, "sd": 1.0}})
        _file(tmp, "t-num-1", "b", {"topline": {"mean": 14.0, "sd": 1.0}})
        late = datetime(2026, 9, 1, 11, 45, tzinfo=timezone.utc)     # 15 min before the close
        assert refresh.file_crowd_forecasts([dict(ROUND)], late) == 0
        after = datetime(2026, 9, 1, 13, 0, tzinfo=timezone.utc)     # an hour after it
        assert refresh.file_crowd_forecasts([dict(ROUND, status="locked")], after) == 0
        assert not os.path.exists(os.path.join(tmp, "t-num-1", "crowd.json"))
        # the backfill path is for rounds that closed before the crowd existed
        assert refresh.file_crowd_forecasts([dict(ROUND, status="locked")], after, backfill=True) == 1
    _with_tree(run)


def test_no_crowd_for_a_pool_of_one():
    def run(tmp):
        _file(tmp, "t-num-2", "a", {"topline": {"mean": 10.0, "sd": 1.0}})
        _file(tmp, "t-num-2", "persistence", {"topline": {"mean": 30.0, "sd": 1.5}})
        assert refresh.file_crowd_forecasts([dict(ROUND, round_id="t-num-2")], NOW) == 0
        assert not os.path.exists(os.path.join(tmp, "t-num-2", "crowd.json"))
    _with_tree(run)


def test_the_crowd_is_scored_like_any_file():
    def run(tmp):
        _file(tmp, "t-num-1", "a", {"topline": {"mean": 10.0, "sd": 1.0}})
        _file(tmp, "t-num-1", "b", {"topline": {"mean": 14.0, "sd": 1.0}})
        _file(tmp, "t-num-1", "persistence", {"topline": {"mean": 30.0, "sd": 1.5}})
        r = dict(ROUND, baselines={"persistence": {"mean": 30.0, "sd": 1.5}})
        refresh.file_crowd_forecasts([r], NOW)
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


REG = {"p1": {"type": "participant"}, "p2": {"type": "participant"},
       "vac-public-opinion": {"type": "participant"},
       "vac-population-profiles": {"type": "participant"},
       "persistence": {"type": "baseline"}}


def test_sub_crowd_membership_follows_registry_then_harness_ids():
    g = refresh.crowd_group
    assert g("p1", REG) == ("crowd-participants", "p1")
    # a team's accounts are one method, as on the pairwise board
    assert g("vac-public-opinion", REG) == ("crowd-participants", "vac-combined")
    assert g("grok-web", REG) == ("crowd-llm-retrieval", "grok")
    assert g("grok-web-superfc", REG) == ("crowd-llm-retrieval", "grok")
    assert g("gemini-pro-news", REG) == ("crowd-llm-retrieval", "gemini-pro")
    assert g("claude-sonnet", REG) == ("crowd-llm-closed", "claude-sonnet")
    for out in ("claude-sonnet-zeroshot", "claude-sonnet-persona", "persistence",
                "crowd", "test-jay", "ssa-testing-team", "example-entrant_web"):
        assert g(out, REG) == (None, None), out


def test_the_team_grouping_matches_the_pairwise_board():
    js = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "site", "pairwise.js")).read()
    for team, members in refresh.PARTICIPANT_METHODS.items():
        assert f"id: '{team}'" in js, team
        for m in members:
            assert f"'{m}'" in js, m


def _topline(mean, sd=1.0):
    return {"topline": {"mean": mean, "sd": sd}}


def test_one_vote_per_base_model():
    # grok answers twice (direct and superfc) at 10; two other models at 20.
    # Pooled per model first, grok weighs a third, not a half.
    members = [{"entrant": e, "round_id": "t", **_topline(m)} for e, m in
               (("grok-web", 10), ("grok-web-superfc", 10), ("gemini-pro-web", 20))]
    unit = {"grok-web": "grok", "grok-web-superfc": "grok", "gemini-pro-web": "gemini-pro"}
    try:
        refresh.subcrowd_answer(ROUND, members, unit.get)
    except ValueError:
        pass
    else:
        raise AssertionError("three files from two models are two votes, not a crowd")
    for e, u in (("claude-sonnet-web", "claude-sonnet"), ("deepseek-pro-web", "deepseek-pro")):
        members.append({"entrant": e, "round_id": "t", **_topline(20)})
        unit[e] = u
    answer, n = refresh.subcrowd_answer(ROUND, members, unit.get)
    assert n == 4
    assert abs(answer["topline"]["mean"] - (10 + 20 * 3) / 4) < 0.05
    flat, _ = refresh.crowd_answer(ROUND, members)
    assert abs(flat["topline"]["mean"] - (10 * 2 + 20 * 3) / 5) < 0.05


def test_insufficient_when_units_are_few_and_legacy_untouched():
    def run(tmp):
        rid = "t-num-1"
        for e, m in (("p1", 10), ("p2", 12), ("grok-web", 11), ("crowd", 11)):
            _file(tmp, rid, e, _topline(m))
        r = dict(ROUND, status="resolved", persistence_crps=1.0,
                 scores={"crowd": {"crps": 0.5, "skill": 0.5}})
        before = json.dumps(r, sort_keys=True)
        keep = refresh.load_entrants
        refresh.load_entrants = lambda: [dict(v, entrant_id=k) for k, v in REG.items()]
        try:
            summary = refresh.build_subcrowds([r], {rid: {"value": 11.0}}, {}, {})
        finally:
            refresh.load_entrants = keep
        c = r["crowds"]
        assert c["crowd-participants"]["status"] == "insufficient"
        assert c["crowd-participants"]["members"] == ["p1", "p2"]
        assert c["crowd-llm-retrieval"]["n_units"] == 1
        assert all(v.get("retrospective") for v in c.values())
        assert json.dumps({k: v for k, v in r.items() if k != "crowds"},
                          sort_keys=True) == before
        assert "crowds" not in r["scores"] and summary["min_units"] == 3
        assert not [f for f in os.listdir(os.path.join(tmp, rid)) if f.startswith("crowd-")]
    _with_tree(run)


def test_real_payload_keeps_every_legacy_field():
    import copy
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "site", "data.json")
    data = json.load(open(path))
    before = copy.deepcopy(data["rounds"])
    rounds = data["rounds"]
    resolved = {r["round_id"]: r["resolution"] for r in rounds
                if r.get("status") == "resolved" and isinstance(r.get("resolution"), dict)
                and "value" in r["resolution"]}
    summary = refresh.build_subcrowds(rounds, resolved, data.get("profile"),
                                      data.get("ranking"))
    for r, b in zip(rounds, before):
        assert {k: v for k, v in r.items() if k != "crowds"} == b, r["round_id"]
        assert "crowd-participants" not in (r.get("scores") or {})
    assert any(g["rounds_ok"] for g in summary["groups"])


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
