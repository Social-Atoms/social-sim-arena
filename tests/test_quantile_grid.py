"""The fixed 21-point quantile grid: one set of levels, one formula, both formats.

Rounds marked `"scoring": "grid21"` accept `quantile_grid` (21 values at
(i - 1/2)/21) beside mean/sd, and score every answer -- mean/sd converted
through the normal -- with S_21 = 2/21 sum pinball. Every other round keeps the
rule it was opened under.
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import jsonschema                                                  # noqa: E402

from ssa import agent_api, harness, refresh, scoring              # noqa: E402
from tools import generate_rounds                                  # noqa: E402
from tools import validate_submission as v                         # noqa: E402

GRID_ROUND = {"round_id": "t-grid-1", "lock_at": "2026-12-08T14:00:00Z",
              "release_at": "2026-12-10T14:00:00Z", "target_type": "continuous_normal",
              "scoring": "grid21", "question": "q", "unit": "points",
              "tracker": "t", "series": "s", "resolve": {}}
OLD_ROUND = dict(GRID_ROUND, round_id="t-old-1")
OLD_ROUND.pop("scoring")


def normal_grid(mean, sd):
    return [round(scoring.normal_quantile(mean, sd, lv), 6)
            for lv in scoring.GRID21_LEVELS]


def schema(name):
    with open(os.path.join(ROOT, "schema", name)) as f:
        return json.load(f)


def refused(fn, *a, **k):
    try:
        fn(*a, **k)
    except (ValueError, v.AnswerContractError):
        return True
    return False


def test_the_levels_are_the_21_midpoints_with_the_median_in_the_middle():
    lv = scoring.GRID21_LEVELS
    assert len(lv) == 21 == scoring.GRID21_K == v.GRID21_K
    assert scoring.GRID21 == v.GRID21 == "grid21"
    assert abs(lv[0] - 1 / 42) < 1e-12 and abs(lv[-1] - 41 / 42) < 1e-12
    assert lv[10] == 0.5
    assert all(abs((b - a) - 1 / 21) < 1e-12 for a, b in zip(lv, lv[1:]))


def test_the_grid_score_is_close_to_exact_crps_for_a_normal():
    # The offline scan's claim, spot-checked: within ~1% at typical outcomes.
    for y in (-1.5, -0.5, 0.0, 0.3, 1.0, 2.0):
        exact = scoring.crps_normal(0.0, 1.0, y)
        grid = scoring.crps_grid21({"mean": 0.0, "sd": 1.0}, y)
        assert abs(grid - exact) / exact < 0.012, (y, grid, exact)


def test_both_formats_are_scored_by_the_same_formula():
    mean, sd, y = 42.0, 1.7, 44.1
    as_moments = scoring.crps_for_round({"mean": mean, "sd": sd}, y, GRID_ROUND)
    as_grid = scoring.crps_for_round({"quantile_grid": normal_grid(mean, sd)}, y, GRID_ROUND)
    assert abs(as_moments - as_grid) < 1e-5


def test_a_round_opened_before_the_rule_keeps_its_exact_score():
    d = {"mean": 42.0, "sd": 1.7}
    assert scoring.round_rule(OLD_ROUND) == "exact"
    assert scoring.crps_for_round(d, 44.1, OLD_ROUND) == scoring.crps_normal(42.0, 1.7, 44.1)


def test_a_skewed_belief_is_better_said_as_a_grid_than_as_mean_and_sd():
    # What the format is for. Outcomes drawn from a lognormal(0, 1); its own
    # 21 quantiles beat the normal with the same mean and sd, on average.
    import math
    ln = lambda lv: math.exp(scoring.normal_quantile(0.0, 1.0, lv))   # noqa: E731
    grid = [ln(lv) for lv in scoring.GRID21_LEVELS]
    mean, var = math.exp(0.5), (math.e - 1) * math.e
    ys = [ln((k + 0.5) / 400) for k in range(400)]
    g = sum(scoring.crps_for_round({"quantile_grid": grid}, y, GRID_ROUND) for y in ys)
    n = sum(scoring.crps_for_round({"mean": mean, "sd": var ** 0.5}, y, GRID_ROUND) for y in ys)
    assert g < 0.9 * n, (g, n)


def test_the_validator_takes_a_grid_only_on_a_grid_round():
    ok = {"round_id": "t-grid-1", "entrant": "acme",
          "topline": {"quantile_grid": normal_grid(10, 1)}}
    v.validate_answer_contract(ok, GRID_ROUND)
    assert refused(v.validate_answer_contract, dict(ok, round_id="t-old-1"), OLD_ROUND)
    jsonschema.validate(ok, schema("forecast.schema.json"))


def test_the_validator_refuses_a_malformed_grid():
    g = normal_grid(10, 1)
    bad = [g[:20], g[::-1], g[:20] + [True], g[:20] + [float("nan")], "1,2,3"]
    for b in bad:
        fc = {"round_id": "t-grid-1", "entrant": "acme", "topline": {"quantile_grid": b}}
        assert refused(v.validate_answer_contract, fc, GRID_ROUND), b
    both = {"round_id": "t-grid-1", "entrant": "acme",
            "topline": {"quantile_grid": g, "mean": 10, "sd": 1}}
    assert refused(v.validate_answer_contract, both, GRID_ROUND)
    # The old entrant-chosen quantile map stays refused everywhere.
    old = {"round_id": "t-grid-1", "entrant": "acme",
           "topline": {"quantiles": {"0.01": 7, "0.5": 10, "0.99": 13}}}
    assert refused(v.validate_answer_contract, old, GRID_ROUND)


def test_a_profile_cell_may_be_a_grid_on_a_grid_round():
    r = dict(GRID_ROUND, target_type="profile_energy", cells=["a", "b"])
    fc = {"round_id": "t-grid-1", "entrant": "acme",
          "profile": {"a": {"quantile_grid": normal_grid(10, 1)}, "b": {"mean": 5, "sd": 1}}}
    v.validate_answer_contract(fc, r)
    assert refused(v.validate_answer_contract, fc, dict(r, scoring=None))
    samples = scoring.cell_samples([fc["profile"]["a"], fc["profile"]["b"]])
    assert abs(sum(s[0] for s in samples) / len(samples) - 10) < 0.1


def test_an_endpoint_may_reply_with_a_grid_only_when_the_round_says_so():
    g = normal_grid(10, 1)
    assert harness.answer({"quantile_grid": g}, grid_ok=True) == {"quantile_grid": g}
    assert refused(harness.answer, {"quantile_grid": g})
    assert refused(harness.answer, {"quantile_grid": g, "mean": 10, "sd": 1}, grid_ok=True)
    assert refused(harness.answer, {"quantile_grid": g[::-1]}, grid_ok=True)
    assert refused(harness.answer, {"quantile_grid": g[:20]}, grid_ok=True)
    assert harness.answer({"mean": 10, "sd": 1}, grid_ok=True) == {"mean": 10.0, "sd": 1.0}
    reply = json.dumps({"schema_version": agent_api.SCHEMA_VERSION,
                        "forecast": {"quantile_grid": g}})
    assert agent_api.parse_scalar(reply, grid_ok=True)["quantile_grid"] == g
    jsonschema.validate(json.loads(reply), schema("agent-api-response.schema.json"))
    both = {"schema_version": agent_api.SCHEMA_VERSION,
            "forecast": {"quantile_grid": g, "mean": 1, "sd": 1}}
    try:
        jsonschema.validate(both, schema("agent-api-response.schema.json"))
    except jsonschema.ValidationError:
        pass
    else:
        raise AssertionError("one format per answer")


def test_the_envelope_names_the_levels_on_a_grid_round_only():
    req = schema("agent-api-request.schema.json")
    r = dict(GRID_ROUND, baselines={"persistence": {"mean": 1.0, "sd": 2.0}})
    env = agent_api.build_envelope("acme", r, history=[{"date": "2026-11-01", "value": 1.0}])
    jsonschema.validate(env, req)
    assert env["round"]["quantile_grid"]["levels"] == scoring.GRID21_LEVELS
    old = agent_api.build_envelope("acme", dict(r, scoring=None),
                                   history=[{"date": "2026-11-01", "value": 1.0}])
    assert "quantile_grid" not in old["round"]


def test_the_leaderboard_scores_a_grid_round_on_the_grid_skill_included():
    keep = refresh.FORECASTS
    with tempfile.TemporaryDirectory() as tmp:
        refresh.FORECASTS = tmp
        try:
            os.makedirs(os.path.join(tmp, "t-grid-1"))
            files = {"a": {"mean": 10.0, "sd": 1.0},
                     "b": {"quantile_grid": normal_grid(10.0, 1.0)}}
            for e, t in files.items():
                with open(os.path.join(tmp, "t-grid-1", e + ".json"), "w") as f:
                    json.dump({"round_id": "t-grid-1", "entrant": e, "topline": t}, f)
            r = dict(GRID_ROUND, baselines={"persistence": {"mean": 13.0, "sd": 1.5}})
            refresh.build_leaderboard([r], {"t-grid-1": {"value": 11.0}})
            sa, sb = r["scores"]["a"], r["scores"]["b"]
            assert abs(sa["crps"] - sb["crps"]) < 1e-3, (sa, sb)
            per = scoring.crps_grid21({"mean": 13.0, "sd": 1.5}, 11.0)
            assert r["persistence_crps"] == round(per, 4)
            assert sa["crps"] == round(scoring.crps_grid21(files["a"], 11.0), 4)
            # The site still gets a mean and sd to draw.
            refresh.count_forecasts([r])
        finally:
            refresh.FORECASTS = keep


def test_the_crowd_pools_a_grid_member_like_any_other():
    xs = scoring.pool_samples([{"topline": {"quantile_grid": normal_grid(10, 1)}},
                               {"topline": {"mean": 14.0, "sd": 1.0}}])
    assert abs(sum(xs) / len(xs) - 12.0) < 0.05


def test_generated_rounds_carry_the_rule_from_the_first_grid_batch():
    before = {"lock_at": "2026-12-05T14:00:00Z", "target_type": "continuous_normal"}
    first = {"lock_at": "2026-12-08T14:00:00Z", "target_type": "continuous_normal"}
    rank = {"lock_at": "2026-12-08T14:00:00Z", "target_type": "ranking_list"}
    assert "scoring" not in generate_rounds.mark_scoring(before)
    assert generate_rounds.mark_scoring(first)["scoring"] == "grid21"
    assert "scoring" not in generate_rounds.mark_scoring(rank)


def test_no_published_round_changed_rule():
    with open(os.path.join(ROOT, "questions", "season0.json")) as f:
        rounds = json.load(f)["rounds"]
    assert not [r["round_id"] for r in rounds if r.get("scoring")], \
        "the grid applies from GRID21_FIRST_BATCH on; published rounds keep their rule"


if __name__ == "__main__":
    tests = [fn for k, fn in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
