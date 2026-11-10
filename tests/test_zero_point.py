"""The ranked zero point: a new key that must not move a single existing score.

`zero_point` lets a round name which reference is its skill denominator
(ssa/baselines.py). Legacy rounds are ranked on persistence; these tests hold that to the byte
against the prior formula, while newly reviewed Urban rounds may declare their
own reference without changing any historical score.
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


def test_legacy_rounds_keep_persistence_and_new_references_are_valid():
    with open(os.path.join(ROOT, "questions", "season0.json")) as f:
        rounds = json.load(f)["rounds"]
    legacy = [r for r in rounds if r['lock_at'] < '2026-11-10T00:00:00Z']
    assert len(legacy) > 50, "legacy season looks empty"
    assert all('zero_point' not in r for r in legacy)
    assert {baselines.zero_point_name(r) for r in legacy} == {'persistence'}
    for r in rounds:
        if 'zero_point' in r:
            baselines.normalize_spec(r['zero_point'], r['round_id'])
    ok(test_legacy_rounds_keep_persistence_and_new_references_are_valid)


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
    assert not any("zero_point" in r or "zero_point_crps" in r for r in after
                   if r.get("lock_at", "") < "2026-11-10T00:00:00Z")
    ok(test_every_published_round_scores_exactly_as_before)


def test_the_payload_of_an_existing_round_has_no_new_key():
    """build_rounds copies `zero_point` only when the definition sets it."""
    rounds, _ = _published()
    assert not [r["round_id"] for r in rounds if "zero_point" in r
                and r.get("lock_at", "") < "2026-11-10T00:00:00Z"]
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


# ------------------------------------------------------------------ the family

# Seattle's reference code exactly as it shipped at f4045b7c4 (card v4),
# before yoy4 became a setting of baselines.yoy_k. Executed against the
# adapter's own week_total / outage_days so only the reference rule differs.
LEGACY_SEATTLE = r"""
def _weeks_ok(cd, weeks):
    return all(week_total(cd, w) is not None and not outage_days(cd, w)
               for w in weeks)


def yoy4_reference(cd, target_week):
    '''The ranked reference: 4-week year-over-year ratio of sums (card v2).

    R = sum over the four reference weeks / sum over the same four weeks
    shifted back 364 days; mean = R x (target-aligned week 364 days back).
    Never a mean of weekly ratios. Policy (3): if any reference week, any
    year-earlier counterpart or the aligned week fails the outage check, the
    block steps back one week; if it still fails, NotPostable.

    Returns the point and its parts. The spread is added by `yoy4` (card v3).
    '''
    d = monday(target_week)
    aligned = d - timedelta(days=364)
    if week_total(cd, aligned) is None or outage_days(cd, aligned):
        raise NotPostable(f"last year's aligned week {aligned} fails the outage "
                          "check; the round is not posted")
    for step in (0, 1):
        weeks = reference_weeks(d, step)
        earlier = [w - timedelta(days=364) for w in weeks]
        if _weeks_ok(cd, weeks) and _weeks_ok(cd, earlier):
            num = sum(week_total(cd, w) for w in weeks)
            den = sum(week_total(cd, w) for w in earlier)
            if den <= 0:
                raise NotPostable("year-earlier reference weeks sum to zero")
            ratio = num / den
            ly = week_total(cd, aligned)
            return {"method": "yoy4_ratio_of_sums",
                    "mean": round(ratio * ly, 2),
                    "ratio": round(ratio, 6),
                    "numerator": num, "denominator": den,
                    "last_year_week": aligned.isoformat(),
                    "last_year_count": ly,
                    "reference_weeks": [w.isoformat() for w in weeks],
                    "step_back": step}
    raise NotPostable(f"reference weeks for {d} fail the outage check even "
                      "after one step back; the round is not posted")


def persistence_settled(cd, target_week):
    '''Shown, not ranked: the count of the settled week D-14..D-8.'''
    d = monday(target_week)
    for step in (0, 1):
        w = d - timedelta(days=14 + 7 * step)
        if week_total(cd, w) is not None and not outage_days(cd, w):
            return {"method": "persistence_settled_week",
                    "mean": float(week_total(cd, w)),
                    "week": w.isoformat(), "step_back": step}
    raise NotPostable(f"no settled persistence week for {d}")


def past_errors(cd, target_week, reference):
    '''(actual - reference mean) for the card v3 spread window, newest first.

    Every number is recomputed from `cd`, the one lock capture: a past week's
    reference by the same rule, its actual from the same body.
    '''
    d = monday(target_week)
    errors, weeks = [], []
    for k in range(2, 2 + SPREAD_SEARCH_WEEKS):
        w = d - timedelta(days=7 * k)
        actual = week_total(cd, w)
        if actual is None or outage_days(cd, w):
            continue
        try:
            ref = reference(cd, w)
        except NotPostable:
            continue
        errors.append(actual - ref["mean"])
        weeks.append(w.isoformat())
        if len(errors) == SPREAD_WEEKS:
            return errors, weeks
    raise NotPostable(f"only {len(errors)} of {SPREAD_WEEKS} past target weeks "
                      f"qualify for the spread of {d} within "
                      f"{SPREAD_SEARCH_WEEKS} weeks; the round is not posted")


def rms(errors):
    return (sum(e * e for e in errors) / len(errors)) ** 0.5


def with_spread(cd, target_week, reference):
    '''A reference plus its card v3 spread: {mean, sd, spread_weeks, ...}.'''
    out = dict(reference(cd, target_week))
    errors, weeks = past_errors(cd, target_week, reference)
    # Floor after rounding: CRPS needs sd > 0, and a perfectly flat history
    # (only ever a fixture) must not round its floor away.
    out["sd"] = max(round(rms(errors), 2), 0.01)
    out["spread"] = "rms_13_settled_weeks"
    out["spread_weeks"] = weeks
    return out


def yoy4(cd, target_week):
    '''The ranked zero point `yoy4` as the leaderboard reads it.'''
    out = with_spread(cd, target_week, yoy4_reference)
    out["method"] = "yoy4"
    return out


def persistence_shown(cd, target_week):
    '''Unranked persistence on D-14, spread the same way from its own errors.'''
    return with_spread(cd, target_week, persistence_settled)
"""


def _legacy_seattle():
    from datetime import timedelta
    from ssa.adapters import seattle_cad as sc
    ns = {"timedelta": timedelta, "monday": sc.monday,
          "week_total": sc.week_total, "outage_days": sc.outage_days,
          "NotPostable": sc.NotPostable, "SPREAD_WEEKS": sc.SPREAD_WEEKS,
          "SPREAD_SEARCH_WEEKS": sc.SPREAD_SEARCH_WEEKS,
          "reference_weeks": sc.reference_weeks}
    exec(LEGACY_SEATTLE, ns)
    return ns


def _noisy_daily(start, end):
    """Deterministic counts that are not flat, so ratios, rounding and the
    spread all move; plus outages that force step-backs and skipped weeks."""
    from datetime import timedelta
    out, d, i = {}, start, 0
    while d <= end:
        n = 600 + (i * 37) % 97 + (60 if d.weekday() >= 5 else 0) + i // 9
        out[d.isoformat()] = {"911": n - 250, "TELEPHONE OTHER, NOT 911": 250,
                              "ONVIEW": 180}
        d += timedelta(days=1)
        i += 1
    return out


def test_seattle_yoy4_is_one_setting_of_yoy_k_byte_for_byte():
    from datetime import date, timedelta
    from ssa.adapters import seattle_cad as sc
    old = _legacy_seattle()
    dump = lambda x: json.dumps(x, sort_keys=False, separators=(",", ":"))  # noqa: E731
    D = date(2026, 12, 14)
    daily = _noisy_daily(D - timedelta(days=sc.WINDOW_DAYS), D - timedelta(days=4))
    # Outages: one in a recent reference block (step back), one in last year's
    # block, one in an older week (a skipped spread week).
    for back in (21, 364 + 28, 119, 364 + 140):
        day = (D - timedelta(days=back) + timedelta(days=2)).isoformat()
        daily[day]["911"] = 0
        daily[day]["TELEPHONE OTHER, NOT 911"] = 0
    cd = sc.community_daily(daily)
    compared = steps = refused = 0
    for j in range(0, 30):
        w = D - timedelta(days=7 * j)
        for new_fn, old_fn in ((sc.yoy4, old["yoy4"]),
                               (sc.yoy4_reference, old["yoy4_reference"]),
                               (sc.persistence_shown, old["persistence_shown"]),
                               (sc.persistence_settled, old["persistence_settled"])):
            try:
                want = old_fn(cd, w)
            except sc.NotPostable:
                refused += 1
                try:
                    new_fn(cd, w)
                    raise AssertionError(f"{new_fn.__name__}({w}) posted; "
                                         "the shipped rule refused")
                except sc.NotPostable:
                    continue
            got = new_fn(cd, w)
            assert dump(got) == dump(want), (new_fn.__name__, w, got, want)
            compared += 1
            steps += bool(want.get("step_back"))
        e_new = sc.past_errors(cd, w, sc.yoy4_reference) if j < 3 else None
        if e_new is not None:
            assert e_new == old["past_errors"](cd, w, old["yoy4_reference"])
    assert compared > 60 and steps > 0 and refused > 0, (compared, steps, refused)
    ok(test_seattle_yoy4_is_one_setting_of_yoy_k_byte_for_byte)


# ------------------------------------------------------------------ the urban cards' settings

NYC_MEAN4 = {"name": "mean4", "family": "mean_k", "period": "week", "k": 4,
             "lag": 1, "spread": {"rule": "rms", "n": 13, "search": 26}}
CPI_MEAN6 = {"name": "mean6", "family": "mean_k", "period": "month", "k": 6,
             "search": 8, "digits": 3,
             "spread": {"rule": "rms", "n": 12, "search": 14}}


def _weeks(D, n, f):
    from datetime import timedelta
    return {D - timedelta(days=7 * j): f(j) for j in range(1, n)}


def test_mean_k_skips_the_unsettled_week_and_spreads_on_its_own_errors():
    from datetime import date, timedelta
    D = date(2026, 12, 14)
    f = lambda j: 1000 + (j * 13) % 29                     # noqa: E731
    series = _weeks(D, 60, f)
    ref = baselines.reference(NYC_MEAN4, series, D)
    want = sum(f(j) for j in (2, 3, 4, 5)) / 4
    assert ref["mean"] == round(want, 2) and ref["method"] == "mean4"
    assert ref["window"] == [(D - timedelta(days=7 * j)).isoformat()
                             for j in (5, 4, 3, 2)]
    errs = []
    for j in range(2, 15):                                 # W <= D-14, 13 weeks
        m = round(sum(f(j + i) for i in (2, 3, 4, 5)) / 4, 2)
        errs.append(f(j) - m)
    assert ref["sd"] == round(baselines.rms(errs), 2)
    assert ref["spread"] == "rms_13" and len(ref["spread_targets"]) == 13
    assert ref["spread_targets"][0] == (D - timedelta(days=14)).isoformat()
    # The outcome and the unsettled week cannot move it: frozen at lock.
    moved = dict(series)
    moved[D] = 99999
    moved[D - timedelta(days=7)] = 99999
    assert baselines.reference(NYC_MEAN4, moved, D) == ref
    ok(test_mean_k_skips_the_unsettled_week_and_spreads_on_its_own_errors)


def test_mean_k_on_months_skips_a_missing_month_and_refuses_too_few():
    vals = {f"2026-{m:02d}": 0.3 + m / 100 for m in range(1, 10)}
    vals.update({f"2025-{m:02d}": 0.25 + m / 200 for m in range(1, 13)})
    vals.update({f"2024-{m:02d}": 0.28 for m in range(1, 13)})
    del vals["2025-10"]                                    # no October change
    ref = baselines.reference(CPI_MEAN6, vals, "2026-10")
    six = ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"]
    assert ref["window"] == six
    assert ref["mean"] == round(sum(vals[m] for m in six) / 6, 3)
    # A past target whose own window crosses the gap reaches one month further.
    assert "2025-10" not in ref["spread_targets"]
    assert len(ref["spread_targets"]) == 12
    sparse = {m: v for m, v in vals.items() if m >= "2026-03"}
    try:
        baselines.reference(CPI_MEAN6, sparse, "2026-10")
        raise AssertionError("posted a spread with no past targets")
    except baselines.NotPostable:
        pass
    ok(test_mean_k_on_months_skips_a_missing_month_and_refuses_too_few)


def test_yoy_k_zero_is_same_as_last_year():
    spec = {"name": "seasonal_naive", "family": "yoy_k", "period": "month",
            "k": 0, "lag": 1, "spread": {"rule": "rms_relative", "n": 12}}
    vals = {}
    for y in (2024, 2025, 2026):
        for m in range(1, 13):
            vals[f"{y}-{m:02d}"] = 100.0 * (1 + 0.04 * (y - 2024)) + m
    ref = baselines.reference(spec, vals, "2026-11")
    assert ref["mean"] == vals["2025-11"] and ref["ratio"] == 1.0
    assert ref["spread_targets"][0] == "2026-09"          # M-1 is not posted
    rel = [(vals[t] - vals[baselines.AXES["month"].year_ago(t)])
           / vals[baselines.AXES["month"].year_ago(t)]
           for t in ref["spread_targets"]]
    assert ref["sd"] == round(abs(ref["mean"]) * baselines.rms(rel), 2)
    ok(test_yoy_k_zero_is_same_as_last_year)


def test_yoy_k_partial_week_end_moves_back_and_keeps_its_start():
    """TSA: daily axis, a week target, window D-35 .. min(newest, D-4)."""
    from datetime import date, timedelta
    spec = {"name": "yoy_tsa", "family": "yoy_k", "period": "day", "k": 32,
            "lag": 3, "end": "newest", "max_lag": 10, "keep": "start",
            "target_len": 7,
            "spread": {"rule": "rms", "n": 13, "search": 26, "end": "nominal"}}
    D = date(2026, 11, 16)
    f = lambda d: 2_000_000 + 1000 * (d.toordinal() % 11) + 50 * d.weekday()  # noqa: E731
    days = {D - timedelta(days=i): f(D - timedelta(days=i)) for i in range(4, 800)}
    full = baselines.reference(spec, days, D)
    assert full["window"][0] == (D - timedelta(days=35)).isoformat()
    assert full["window"][-1] == (D - timedelta(days=4)).isoformat()
    assert full["k_used"] == 32 and full["lag_used"] == 3
    assert full["year_ago"][0] == (D - timedelta(days=364)).isoformat()
    assert len(full["year_ago"]) == 7
    assert full["spread_targets"][0] == (D - timedelta(days=14)).isoformat()
    late = {d: v for d, v in days.items() if d <= D - timedelta(days=6)}
    held = baselines.reference(spec, late, D)
    assert held["window"][0] == (D - timedelta(days=35)).isoformat()
    assert held["window"][-1] == (D - timedelta(days=6)).isoformat()
    assert held["k_used"] == 30
    # Nominal spread end: past weeks use the normal D-4 end, so the spread is
    # the one the full capture gives.
    assert held["sd"] == full["sd"]
    gap = dict(days)
    del gap[D - timedelta(days=20)]
    try:
        baselines.reference(spec, gap, D)
        raise AssertionError("a missing day inside the range was posted")
    except baselines.NotPostable:
        pass
    ok(test_yoy_k_partial_week_end_moves_back_and_keeps_its_start)


def test_yoy_k_newest_complete_months_with_an_adapter_base():
    """Chicago: trend over the 3 newest months (L >= M-5), a calendar base
    the adapter supplies, relative spread on the same L offset."""
    spec = {"name": "cal_yoy3", "family": "yoy_k", "period": "month", "k": 3,
            "end": "newest", "max_lag": 4, "base": "cta_daytype",
            "spread": {"rule": "rms_relative", "n": 12}}
    vals = {}
    for y in (2023, 2024, 2025, 2026):
        for m in range(1, 13):
            vals[f"{y}-{m:02d}"] = 1e6 * (1 + 0.03 * (y - 2023)) + 1e4 * m
    for m in ("2026-10", "2026-09", "2026-08", "2026-11"):
        del vals[m]                                       # newest is 2026-07
    calls = []

    def base(target):
        calls.append(target)
        ly = baselines.AXES["month"].year_ago(target)
        return vals[ly] * 1.01, [ly]
    ref = baselines.reference(spec, vals, "2026-11", bases={"cta_daytype": base})
    assert ref["window"] == ["2026-05", "2026-06", "2026-07"]
    assert ref["lag_used"] == 3 and ref["year_ago_value"] == vals["2025-11"] * 1.01
    assert calls[0] == "2026-11" and ref["spread_targets"][0] == "2026-07"
    try:
        baselines.reference(spec, vals, "2026-11")
        raise AssertionError("ran without the adapter's base")
    except ValueError:
        pass
    old = {m: v for m, v in vals.items() if m <= "2026-05"}
    try:
        baselines.reference(spec, old, "2026-11", bases={"cta_daytype": base})
        raise AssertionError("L older than M-5 was posted")
    except baselines.NotPostable:
        pass
    ok(test_yoy_k_newest_complete_months_with_an_adapter_base)


def test_the_spec_is_closed():
    bad = [
        dict(NYC_MEAN4, colour="red"),                     # unknown key
        {k: v for k, v in NYC_MEAN4.items() if k != "spread"},   # default sd
        dict(NYC_MEAN4, name="ewma"),                      # a default null's name
        dict(NYC_MEAN4, name="Mean 4"),
        dict(NYC_MEAN4, period="fortnight"),
        dict(NYC_MEAN4, spread={"rule": "mad", "n": 13}),
        dict(NYC_MEAN4, spread={"rule": "rms", "n": 13, "search": 5}),
        dict(NYC_MEAN4, family="mean_k", k=0),
        dict(baselines.PRESETS["yoy4"], k=5),              # a preset redefined
        {"name": "persistence", "family": "persistence", "k": 1},
        {"name": "x", "family": "yoy_k", "period": "week", "k": 4,
         "end": "newest", "spread": {"rule": "rms", "n": 13}},  # no max_lag
        "yoy5",
    ]
    for spec in bad:
        try:
            baselines.normalize_spec(spec)
            raise AssertionError(f"accepted {spec!r}")
        except ValueError:
            pass
    assert baselines.normalize_spec(dict(baselines.PRESETS["yoy4"])) == \
        baselines.normalize_spec("yoy4")
    from ssa import season
    r = {"round_id": "t", "target_type": "continuous_normal",
         "zero_point": dict(NYC_MEAN4, colour="red")}
    assert season._shape_problems(r)
    r["zero_point"] = NYC_MEAN4
    assert not season._shape_problems(r)
    ok(test_the_spec_is_closed)


def test_a_spec_round_is_computed_from_frozen_history_and_ranked_on_it():
    from datetime import date, timedelta
    D = date(2026, 12, 14)
    f = lambda j: 1000 + (j * 13) % 29                     # noqa: E731
    hist = [{"date": (D - timedelta(days=7 * j)).isoformat(), "value": f(j)}
            for j in range(59, 0, -1)]
    r = {"round_id": "t-zp", "zero_point": dict(NYC_MEAN4, target=D.isoformat())}
    row = {"round_id": "t-zp", "baselines": baselines.all_baselines(hist, "2026-12-22"),
           "scoreable": True, "zero_point": baselines.zero_point_name(r),
           "zero_point_spec": baselines.zero_point_spec(r)}
    refresh.attach_zero_point(row, r, hist)
    assert row["baselines"]["mean4"]["mean"] == round(
        sum(f(j) for j in (2, 3, 4, 5)) / 4, 2)
    # The published row (name + spec) resolves the same reference.
    assert baselines.zero_point(row) is row["baselines"]["mean4"]
    short = hist[-8:]
    row2 = {"baselines": baselines.all_baselines(short, "2026-12-22"), "scoreable": True}
    refresh.attach_zero_point(row2, r, short)
    assert row2["baselines"] is None and row2["scoreable"] is False
    assert "mean4" in row2["baseline_note"]
    # A row naming a non-preset without its spec is refused, not guessed.
    try:
        baselines.zero_point_name({"round_id": "t", "zero_point": "mean4"})
        raise AssertionError("an unknown name was accepted")
    except ValueError:
        pass
    ok(test_a_spec_round_is_computed_from_frozen_history_and_ranked_on_it)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"{len(PASSED)} passed")
