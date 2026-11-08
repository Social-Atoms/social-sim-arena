"""The ranked zero point: a new key that must not move a single existing score.

`zero_point` lets a round name which reference is its skill denominator
(ssa/baselines.py). Every round in the season predates it and is ranked on
persistence; these tests hold that to the byte, on the committed season and the
committed published payload, against a copy of the formula as it stood.
"""
import copy
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import baselines, refresh, scoring                  # noqa: E402

PASSED = []


def ok(fn):
    PASSED.append(fn.__name__)
    print("ok", fn.__name__)


def _published():
    with open(os.path.join(ROOT, "site", "data.json")) as f:
        rounds = json.load(f).get("rounds", [])
    resolved = {r["round_id"]: r["resolution"] for r in rounds
                if r.get("status") == "resolved"
                and isinstance(r.get("resolution"), dict)
                and "value" in r["resolution"]}
    return rounds, resolved


def _leaderboard_before(rounds, resolved):
    """build_leaderboard exactly as it was before `zero_point` (a5f54815e)."""
    entries = {}
    for r in rounds:
        res = resolved.get(r["round_id"])
        if not res or not r.get("baselines"):
            continue
        outcome = res["value"]
        per_crps = scoring.crps_normal(r["baselines"]["persistence"]["mean"],
                                       r["baselines"]["persistence"]["sd"], outcome)
        rdir = os.path.join(refresh.FORECASTS, r["round_id"])
        if not os.path.isdir(rdir):
            continue
        scores = {}
        for fn in sorted(os.listdir(rdir)):
            if fn[:-5] in refresh.HOUSE_TEST_IDS:
                continue
            if not fn.endswith(".json"):
                continue
            with open(os.path.join(rdir, fn)) as f:
                fc = json.load(f)
            if not refresh.scoreable_forecast(fc):
                continue
            c = scoring.crps_forecast(fc["topline"], outcome)
            e = entries.setdefault(fc["entrant"], {"crps": [], "skill": []})
            e["crps"].append(c)
            e["skill"].append(scoring.skill(c, per_crps))
            scores[fc["entrant"]] = {"crps": round(c, 4),
                                     "skill": round(scoring.skill(c, per_crps), 4)}
        r["scores"] = scores
        r["persistence_crps"] = round(per_crps, 4)
    board = []
    for name, e in entries.items():
        board.append({"entrant": name, "rounds": len(e["crps"]),
                      "mean_crps": round(sum(e["crps"]) / len(e["crps"]), 3),
                      "mean_skill": round(sum(e["skill"]) / len(e["skill"]), 3)})
    board.sort(key=lambda x: -x["mean_skill"])
    return board


def test_no_season_round_names_a_zero_point():
    with open(os.path.join(ROOT, "questions", "season0.json")) as f:
        rounds = json.load(f)["rounds"]
    assert len(rounds) > 50, "season looks empty; this test would pass vacuously"
    assert not [r["round_id"] for r in rounds if "zero_point" in r]
    assert {baselines.zero_point_name(r) for r in rounds} == {"persistence"}
    ok(test_no_season_round_names_a_zero_point)


def test_every_published_round_scores_exactly_as_before():
    rounds, resolved = _published()
    scored = [r for r in rounds if r.get("baselines") and r["round_id"] in resolved]
    assert len(scored) > 30, f"only {len(scored)} scored rounds; vacuous"
    before, after = copy.deepcopy(rounds), copy.deepcopy(rounds)
    board_before = _leaderboard_before(before, resolved)
    board_after = refresh.build_leaderboard(after, resolved)
    dump = lambda x: json.dumps(x, sort_keys=True, separators=(",", ":"))  # noqa: E731
    assert dump(board_after) == dump(board_before)
    assert dump(after) == dump(before), "a round's payload moved"
    assert not any("zero_point" in r or "zero_point_crps" in r for r in after)
    ok(test_every_published_round_scores_exactly_as_before)


def test_the_payload_of_an_existing_round_has_no_new_key():
    """build_rounds copies `zero_point` only when the definition sets it."""
    rounds, _ = _published()
    assert not [r["round_id"] for r in rounds if "zero_point" in r]
    src = open(os.path.join(ROOT, "ssa", "refresh.py")).read()
    assert 'if "zero_point" in r:' in src
    ok(test_the_payload_of_an_existing_round_has_no_new_key)


def _round(zero_point=None, mean=100.0, sd=10.0):
    r = {"round_id": "t-zp", "baselines": {
        "persistence": {"mean": 90.0, "sd": 1.5, "method": "persistence"},
        "yoy4": {"mean": mean, "sd": sd, "method": "yoy4"}}}
    if zero_point:
        r["zero_point"] = zero_point
    return r


def test_a_yoy4_round_is_ranked_on_yoy4_and_still_shows_persistence():
    tmp = os.path.join(refresh.FORECASTS, "t-zp")
    assert not os.path.exists(tmp)
    os.makedirs(tmp)
    try:
        fc = {"entrant": "someone", "round_id": "t-zp",
              "topline": {"mean": 104.0, "sd": 8.0}}
        with open(os.path.join(tmp, "someone.json"), "w") as f:
            json.dump(fc, f)
        r = _round("yoy4")
        refresh.build_leaderboard([r], {"t-zp": {"value": 105.0}})
        ref = scoring.crps_normal(100.0, 10.0, 105.0)
        own = scoring.crps_forecast(fc["topline"], 105.0)
        assert r["zero_point"] == "yoy4"
        assert r["zero_point_crps"] == round(ref, 4)
        assert r["scores"]["someone"]["skill"] == round(scoring.skill(own, ref), 4)
        assert r["persistence_crps"] == round(scoring.crps_normal(90.0, 1.5, 105.0), 4)
    finally:
        for fn in os.listdir(tmp):
            os.remove(os.path.join(tmp, fn))
        os.rmdir(tmp)
    ok(test_a_yoy4_round_is_ranked_on_yoy4_and_still_shows_persistence)


def test_a_named_zero_point_must_exist_and_be_known():
    r = _round("yoy4")
    del r["baselines"]["yoy4"]
    try:
        baselines.zero_point(r)
        raise AssertionError("fell back to another null")
    except KeyError:
        pass
    try:
        baselines.zero_point(_round("climatology"))
        raise AssertionError("an unknown zero point was accepted")
    except ValueError:
        pass
    assert baselines.zero_point(_round())["method"] == "persistence"
    ok(test_a_named_zero_point_must_exist_and_be_known)


def test_the_agent_envelope_names_the_zero_point_only_when_set():
    from ssa import agent_api
    plain = agent_api._context(_round(), [])
    assert plain == {"persistence": 90.0}
    named = agent_api._context(_round("yoy4"), [])
    assert named["zero_point"] == {"name": "yoy4", "mean": 100.0, "sd": 10.0}
    assert named["persistence"] == 90.0
    ok(test_the_agent_envelope_names_the_zero_point_only_when_set)


def test_the_questionnaire_reference_is_the_zero_point():
    from ssa import questionnaire_api
    assert questionnaire_api._latest_public_reference(_round())["mean"] == 90.0
    assert questionnaire_api._latest_public_reference(_round("yoy4"))["mean"] == 100.0
    ok(test_the_questionnaire_reference_is_the_zero_point)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"{len(PASSED)} passed")
