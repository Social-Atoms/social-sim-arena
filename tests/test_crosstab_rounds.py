"""The Economist/YouGov crosstab as a profile round, end to end.

Run: PYTHONPATH=. python tests/test_crosstab_rounds.py

Plain asserts, no pytest, no network: every wave is a fixture, so the monthly
aggregation under test is this repository's code rather than something the
fixture pre-computed.

`tests/test_crosstab.py` holds the extractor and the joint scorer. This file
holds the wiring that turns them into a round -- the monthly series in the
registry, the freeze, the resolution and the board -- and in particular the two
places where this source can go wrong quietly:

- **a month averaged over the wrong waves.** `crosstab.target` averages "the
  last four waves at or before a date", which reaches into the previous month
  when the month asked for is short. A number labelled September that is one
  quarter August is wrong in a way nothing downstream can see.
- **a lock that lets the answer into its own history.** A month's point is
  dated by the month's last wave, and the freeze is `date < lock_at[:10]`. One
  day of slack in the release date puts the outcome inside the pre-lock history
  its own persistence null is built from -- the invariant `refresh.build_rounds`
  exists to protect, in the one round type where the dates make it easy to lose.
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa import crosstab, harness, profile_round, refresh, scoring
from ssa import series as series_registry
from ssa.adapters import yougov_xtab

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CELLS = list(series_registry.YOUGOV_XTAB_CELLS)
LABELS = list(yougov_xtab.SCORED_CELLS)

# The round as `questions/season0.json` carries it. Kept here in full rather
# than read from the file: the season is edited constantly, and a test that
# silently stops covering the round when its id changes is worse than no test.
ROUND = {
    "round_id": "yougov-xtab-2026-09",
    "tracker": "yougov_xtab",
    "profile_noun": "subgroup",
    "series": "yougov_xtab_approve_dem",
    "cells": [
        "yougov_xtab_approve_dem",
        "yougov_xtab_approve_ind",
        "yougov_xtab_approve_rep",
        "yougov_xtab_approve_age_under_30",
        "yougov_xtab_approve_age_30_44",
        "yougov_xtab_approve_age_45_64",
        "yougov_xtab_approve_age_65_up",
        "yougov_xtab_approve_race_white",
        "yougov_xtab_approve_race_black",
        "yougov_xtab_approve_race_hispanic",
        "yougov_xtab_approve_male",
        "yougov_xtab_approve_female",
        "yougov_xtab_approve_edu_hs_or_less",
        "yougov_xtab_approve_edu_some_college",
        "yougov_xtab_approve_edu_college_grad",
        "yougov_xtab_approve_edu_postgrad"
    ],
    "question": "Economist/YouGov weekly tracker: Donald Trump's job approval among US registered voters, broken into the survey's own sixteen crosstab cells, for September 2026. Forecast the percentage approving in each subgroup, averaged over the four weekly waves dated in September 2026 (expected 2026-09-07, 09-14, 09-21 and 09-28). These are measured cells of one survey, not a model's subgroup estimates: they move with the national mood and they also move apart, and the whole profile is scored.",
    "unit": "percent approving",
    "methodology": "Economist/YouGov weekly tracker of US registered voters, taken from YouGov's own public tracker workbook, one sheet per subgroup. This is the survey's crosstab: a cell is the answer of the respondents actually interviewed in that subgroup that week, and the cells are linked by nothing but the electorate -- unlike a modelled tracker, where one model produces every cell. YouGov publishes each cell rounded to a whole percentage point; approve, disapprove and not sure sum to 100. The scored value is the mean of the four weekly waves dated in the calendar month, which is the unit the noise in this file makes scoreable: at wave level nine of the sixteen cells show no real week-to-week movement at all, and the four-wave average cuts a cell's measurement noise by a median of 62 percent. Median weighted base per wave and the measured noise of the four-wave monthly average, per cell, in respondents / points: Democrat 394 / 0.43; Independent 384 / 1.69; Republican 428 / 0.58; Under 30 186 / 1.23; 30-44 273 / 1.51; 45-64 414 / 0.68; 65+ 317 / 0.73; White 842 / 0.75; Black 141 / 0.00; Hispanic 138 / 0.59; Male 558 / 0.78; Female 629 / 0.56; HS or less 332 / 0.85; Some college 356 / 0.90; College grad 316 / 0.96; Postgrad 190 / 1.29 -- measured over the 82 waves published 2025-01-28 to 2026-08-17. Both figures come from the series' own first differences and the monthly one rests on only eighteen complete months, so a monthly 0.00 does not mean a cell has no sampling error; it means the estimator could not separate any of that cell's movement from signal on this much history. Read the small ones as a lower bound.",
    "release_at": "2026-09-30T14:00:00Z",
    "release_estimated": True,
    "lock_at": "2026-09-28T14:00:00Z",
    "resolve": "for each of the sixteen cells, the mean of that cell's approve percentage over the four weekly Economist/YouGov waves dated in September 2026, read from the tracker workbook at api-test.yougov.com/public-data/v5/us/trackers/donald-trump-approval/download/. If September carries five waves the last four dated in September are averaged, so the target is always a four-wave mean; if it carries fewer than four the round does not resolve rather than averaging three. The month's point is dated by the last of those four waves, which is why the lock sits on that wave's own date: one day later and the answer would fall inside the strictly-pre-lock history its own persistence null is built from.",
    "target_type": "profile_energy"
}

# The cell roster is the registry constant, not a second copy of it: a round
# whose `cells` had drifted from `series.YOUGOV_XTAB_CELLS` would score cell i
# against cell j's answer, and a hand-copied list here could not catch it.
assert ROUND["cells"] == CELLS
ROUND["cells"] = CELLS

# The wave calendar the real workbook carries from 2026-01 (Mondays), plus the
# September the round scores. 2026-09 has exactly four Mondays -- 7, 14, 21, 28
# -- which is why the round's release can be the last wave plus 48 hours.
JAN_TO_SEP = (
    ["2026-01-05", "2026-01-12", "2026-01-19", "2026-01-26"]
    + ["2026-02-02", "2026-02-09", "2026-02-16", "2026-02-23"]
    # March 2026 is one of the six five-wave months on this tracker
    + ["2026-03-02", "2026-03-09", "2026-03-16", "2026-03-23", "2026-03-30"]
    + ["2026-04-06", "2026-04-13", "2026-04-20", "2026-04-27"]
    + ["2026-05-04", "2026-05-11", "2026-05-18", "2026-05-26"]
    + ["2026-06-01", "2026-06-08", "2026-06-15", "2026-06-22", "2026-06-29"]
    + ["2026-07-06", "2026-07-13", "2026-07-20", "2026-07-27"]
    + ["2026-08-03", "2026-08-10", "2026-08-17", "2026-08-24", "2026-08-31"]
    + ["2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28"]
)

# A distinct level per cell, so a scorer or an aggregator that mixed two cells
# up cannot pass by accident.
LEVELS = {label: 5.0 + 5.0 * i for i, label in enumerate(LABELS)}


def wave(date, bump=0.0, cells=None):
    """One parsed wave in `yougov_xtab.parse`'s shape.

    Every scored cell plus the topline sheet, because `parse` only emits a wave
    when all seventeen are present and code downstream is entitled to assume it.
    """
    names = cells if cells is not None else LABELS
    out = {}
    for name in [yougov_xtab.TOPLINE] + list(names):
        approve = (40.0 if name == yougov_xtab.TOPLINE
                   else LEVELS[name]) + bump
        out[name] = {"approve": approve, "disapprove": 100.0 - approve - 5.0,
                     "not_sure": 5.0, "base": 500.0, "unweighted_base": 480.0}
    return {"date": date, "question": "Do you approve or disapprove ...",
            "cells": out}


def waves_for(dates, bumps=None):
    """Waves on `dates`, oldest first; `bumps` shifts every cell of wave i."""
    bumps = bumps or {}
    return [wave(d, bumps.get(d, 0.0)) for d in dates]


# The ramp's September mean: the last four indices of JAN_TO_SEP, averaged.
# Computed rather than written down, so a change to the calendar above cannot
# leave a hand-copied constant quietly disagreeing with the fixture.
SEPT_BUMP = sum(range(len(JAN_TO_SEP) - 4, len(JAN_TO_SEP))) / 4.0


def full_waves():
    """The calendar through September, each wave one point above the last.

    A ramp of one point per wave makes every four-wave mean checkable by hand
    and makes a window that borrowed the wrong wave show up as a wrong number
    rather than as the same number.
    """
    return waves_for(JAN_TO_SEP,
                     {d: float(i) for i, d in enumerate(JAN_TO_SEP)})


def built(waves, cells_only=True):
    """`build_all` over the crosstab rows only, from injected waves.

    Narrowing `SERIES` is how every other source's build test stays off the
    network: `build_all` fetches per *source present in the registry*, so the
    other twenty-odd sources would otherwise all be hit.
    """
    saved = series_registry.SERIES
    if cells_only:
        series_registry.SERIES = {k: v for k, v in saved.items()
                                  if v["source"] == "yougov_xtab"}
    saved_fetch = yougov_xtab.fetch
    fetched = []
    yougov_xtab.fetch = lambda *a, **k: fetched.append(1) or b""
    try:
        return series_registry.build_all(sources={"yougov_xtab": waves}), fetched
    finally:
        series_registry.SERIES = saved
        yougov_xtab.fetch = saved_fetch


class Clock:
    """Pin `refresh.now_utc`, so a round dated in the future can be scored."""

    def __init__(self, iso):
        self.iso = iso

    def __enter__(self):
        self.saved = refresh.now_utc
        stamp = refresh.parse_iso(self.iso)
        refresh.now_utc = lambda: stamp
        return self

    def __exit__(self, *a):
        refresh.now_utc = self.saved


class Scratch:
    """A temp forecasts/ directory, so nothing writes into the repository."""

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="ssa-xtab-")
        self.saved = (refresh.FORECASTS, refresh.LOCKS)
        refresh.FORECASTS = os.path.join(self.dir, "forecasts")
        refresh.LOCKS = os.path.join(self.dir, "locks")
        return self

    def __exit__(self, *a):
        refresh.FORECASTS, refresh.LOCKS = self.saved
        shutil.rmtree(self.dir, ignore_errors=True)

    def file(self, entrant, profile):
        d = os.path.join(refresh.FORECASTS, ROUND["round_id"])
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, entrant + ".json"), "w") as f:
            json.dump({"round_id": ROUND["round_id"], "entrant": entrant,
                       "profile": profile, "notes": "test"}, f)


# --- the monthly aggregation ------------------------------------------------

def test_the_monthly_value_is_the_four_wave_mean_dated_by_the_last_wave():
    """The one arithmetic claim the whole round type rests on."""
    dates = ["2026-04-06", "2026-04-13", "2026-04-20", "2026-04-27"]
    ws = waves_for(dates, dict(zip(dates, [0.0, 2.0, 4.0, 6.0])))
    pts = crosstab.monthly_profile(ws, LABELS)
    assert len(pts) == 1, pts
    p = pts[0]
    assert p["month"] == "2026-04"
    # dated by the last wave averaged, not by the month label and not by the
    # month's last day: the freeze compares this string against `lock_at`.
    assert p["date"] == "2026-04-27", p["date"]
    assert p["waves"] == dates
    for i, label in enumerate(LABELS):
        assert p["values"][i] == LEVELS[label] + 3.0, (label, p["values"][i])
    # and the cell series carries exactly that, per cell
    cs = crosstab.monthly_cell_series(ws, LABELS)
    assert cs["Democrat"] == [{"date": "2026-04-27",
                               "value": LEVELS["Democrat"] + 3.0}]


def test_a_five_wave_month_keeps_the_last_four_and_stays_inside_the_month():
    """Six of the twenty months on this tracker carry five waves. Averaging
    whatever a month happens to hold would give the target a different noise
    floor month to month, and two rounds' energy scores would stop being
    comparable while looking identical in the data."""
    dates = ["2026-03-02", "2026-03-09", "2026-03-16", "2026-03-23", "2026-03-30"]
    ws = waves_for(["2026-02-23"] + dates,
                   {"2026-02-23": -100.0, **dict(zip(dates, [0.0, 2.0, 4.0, 6.0, 8.0]))})
    pts = crosstab.monthly_profile(ws, LABELS)
    march = [p for p in pts if p["month"] == "2026-03"][0]
    assert march["waves"] == dates[1:], march["waves"]
    assert march["date"] == "2026-03-30"
    assert march["values"][0] == LEVELS[LABELS[0]] + 5.0, march["values"][0]
    # February had one wave, so it publishes nothing at all rather than a
    # one-wave "average" -- and its -100 never reaches March.
    assert [p["month"] for p in pts] == ["2026-03"], [p["month"] for p in pts]


def test_a_month_with_three_waves_is_refused_not_averaged():
    """The failure this guard exists for, shown against the raw window.

    `crosstab.target` is a rolling-window helper and will happily complete a
    three-wave August with July's last wave. `month_target` filters to the
    month first, so it raises instead -- a three-wave mean is a different
    estimator with a different noise floor, and publishing one under the same
    series name makes two rounds' scores incomparable.
    """
    july = ["2026-07-06", "2026-07-13", "2026-07-20", "2026-07-27"]
    august = ["2026-08-03", "2026-08-10", "2026-08-17"]
    ws = waves_for(july + august,
                   {d: (-50.0 if d in july else 0.0) for d in july + august})
    series = crosstab.profile_series(ws, LABELS)

    # the raw window really does borrow across the boundary ...
    _, detail = crosstab.target(series, "2026-08-31", 4)
    assert detail["waves"][0] == "2026-07-27", detail["waves"]

    # ... and the month-aware call refuses rather than publishing it
    try:
        crosstab.month_target(series, "2026-08")
    except ValueError as e:
        assert "3 wave(s), not 4" in str(e), str(e)
    else:
        raise AssertionError("a three-wave month must be refused, not averaged")

    # so the monthly series carries July and nothing labelled August
    pts = crosstab.monthly_profile(ws, LABELS)
    assert [p["month"] for p in pts] == ["2026-07"], [p["month"] for p in pts]
    cs = crosstab.monthly_cell_series(ws, LABELS)
    assert [q["date"] for q in cs["Postgrad"]] == ["2026-07-27"]


def test_monthly_coverage_names_the_months_that_were_dropped():
    """A month missing because the tracker has not finished it and a month
    missing because a wave failed to parse look identical in the output series
    and are entirely different problems."""
    ws = full_waves()
    cov = crosstab.monthly_coverage(crosstab.profile_series(ws, LABELS))
    assert cov["2026-03"] == 5 and cov["2026-09"] == 4
    assert cov["2026-08"] == 5
    assert set(cov) == {f"2026-{m:02d}" for m in range(1, 10)}


# --- the registry -----------------------------------------------------------

def test_the_registered_cells_are_exactly_the_adapter_roster_in_order():
    """The adapter owns what the workbook contains. A cell registered under a
    label the workbook does not carry is a round that can never resolve; a
    label missing from the registry is a sixteen-cell round scored on fifteen.
    The registry asserts this at import; this is the same check, said once
    where a reader looking for it will find it."""
    assert len(CELLS) == 16 == len(LABELS)
    got = [series_registry.SERIES[c]["yougov_xtab"]["cell"] for c in CELLS]
    assert got == list(yougov_xtab.SCORED_CELLS), got
    # order is load-bearing: the outcome vector is read in it
    assert got[:3] == ["Democrat", "Independent", "Republican"]
    assert got[-1] == "Postgrad"
    # and the roster is exactly the union of the adapter's own dimensions
    flat = [c for cells in yougov_xtab.DIMENSIONS.values() for c in cells]
    assert sorted(got) == sorted(flat)
    # the excluded cell stays excluded: a median base of 69 cannot be forecast
    assert "Other" in yougov_xtab.EXCLUDED
    assert not any("other" in c for c in CELLS)


def test_every_cell_row_tells_an_entrant_what_it_is_graded_on():
    """The registry's own rule: anything the resolution uses and the prompt
    omits is a question the entrant cannot see but is scored on. Here that is
    the four-wave rule, the cell's base size and its measured noise, which
    differ across these cells by a factor of six."""
    for c in CELLS:
        d = series_registry.describe(c)
        assert d["unit"] == "percent approving", c
        assert "four" in d["cadence"] and "month" in d["cadence"], c
        assert "four weekly waves" in d["question"], c
        assert "median weighted base" in d["methodology"], c
        assert "measurement noise" in d["methodology"], c
        # who published it, and that a cell is measured rather than modelled
        assert "Economist" in d["publisher"] and "YouGov" in d["publisher"], c
        assert "not a modelled estimate" in d["publisher"], c
        assert series_registry.SERIES[c]["source"] == "yougov_xtab", c
        assert series_registry.SERIES[c]["tracker"] == "yougov_xtab", c
        # no persona instrument: weights_for cannot express "postgraduates
        # only", so a panel run would answer nationally and file it as a
        # subgroup. survey() returning None is the refusal, by name.
        assert series_registry.survey(c) is None, c


def test_build_all_derives_the_monthly_series_from_injected_waves_and_never_fetches():
    out, fetched = built(full_waves())
    assert fetched == [], "an injected source must not fetch"
    assert set(out) == set(CELLS)
    # January has four waves but is the first month, September the last: both
    # complete, so eight months of the nine in the fixture publish (August has
    # five, March five, the rest four).
    assert {len(v) for v in out.values()} == {9}, {len(v) for v in out.values()}
    dem = out["yougov_xtab_approve_dem"]
    assert dem[-1]["date"] == "2026-09-28"
    assert [p["date"] for p in dem][:2] == ["2026-01-26", "2026-02-23"]
    # one derivation shared by sixteen cells: every cell covers the same months
    assert len({tuple(p["date"] for p in v) for v in out.values()}) == 1


# --- the round --------------------------------------------------------------

def test_the_lock_excludes_the_month_the_round_scores():
    """The invariant the release date is chosen to satisfy.

    A month's point is dated by the month's last wave and the freeze is a
    string comparison, `date < lock_at[:10]`. So the lock must fall on or
    before that wave's own date. `release - 48h` with a release two days after
    the last wave lands the lock exactly on it, and the strict comparison
    excludes it. A release one day later would put September's mean inside the
    pre-lock history its own persistence null is built from -- the same failure
    `refresh.build_rounds` freezes scalar rounds to avoid.
    """
    series, _ = built(full_waves())
    hist = profile_round.frozen_history(ROUND, series, CELLS)
    for c in CELLS:
        assert all(p["date"] < "2026-09-28" for p in hist[c]), c
        assert hist[c][-1]["date"] == "2026-08-31", c
        assert not any(p["date"][:7] == "2026-09" for p in hist[c]), c

    # and the same round with one more day of slack does leak, which is why
    # the release is pinned to the last wave plus exactly 48 hours
    late = dict(ROUND, lock_at="2026-09-29T14:00:00Z")
    leaked = profile_round.frozen_history(late, series, CELLS)
    assert leaked[CELLS[0]][-1]["date"] == "2026-09-28", \
        "this fixture must actually be able to leak, or the test proves nothing"


def test_the_round_builds_freezes_and_gets_a_per_cell_null():
    series, _ = built(full_waves())
    hist = profile_round.frozen_history(ROUND, series, CELLS)
    per = profile_round.persistence_profile(hist, CELLS)
    for c in CELLS:
        # August's mean, not September's: the answer is not in the null
        assert per[c]["mean"] == round(hist[c][-1]["value"], 2), c
        assert per[c]["sd"] > 0, c

    season = {"season": 0, "rounds": [ROUND]}
    with Clock("2026-09-29T00:00:00Z"):
        rows, _ = refresh.build_rounds(season, series, {}, refresh.now_utc())
    row = rows[0]
    assert row["status"] == "locked", row["status"]
    assert row["baselines"] is None, "a vector round has no scalar denominator"
    assert row["scoreable"] is True, row.get("baseline_note")
    assert row["profile"]["cells"] == CELLS
    assert set(row["profile"]["history_points"].values()) == {8}
    got = row["profile"]["baselines"]["persistence"]
    assert got[CELLS[0]]["mean"] == per[CELLS[0]]["mean"]


def test_a_thin_history_refuses_the_null_instead_of_inventing_one():
    """The denominator of a skill score has to be a real forecast for every
    cell, or the ratio means nothing."""
    short = ["2026-08-03", "2026-08-10", "2026-08-17", "2026-08-24",
             "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28"]
    series, _ = built(waves_for(short))
    hist = profile_round.frozen_history(ROUND, series, CELLS)
    assert all(len(v) <= 1 for v in hist.values())
    try:
        profile_round.persistence_profile(hist, CELLS)
    except ValueError as e:
        assert "pre-lock points per cell" in str(e), str(e)
    else:
        raise AssertionError("a thin cell must refuse the round")


def test_the_prompt_asks_for_all_sixteen_cells_and_says_who_published_them():
    series, _ = built(full_waves())
    hist = profile_round.frozen_history(ROUND, series, CELLS)
    prompt = harness.build_profile_prompt(ROUND, hist, cells=CELLS)
    assert "broken down into its subgroups" in prompt
    assert "all 16 subgroups" in prompt
    for c in CELLS:
        assert c in prompt, c
    assert "Economist and YouGov" in prompt
    assert "not a modelled estimate" in prompt
    assert "percent approving" in prompt
    # the history shown is the frozen one, and no September value is in it
    assert "2026-08-31" in prompt and "2026-09-28" not in prompt
    # and the answer format is the profile footer, keyed by series id
    assert '{"<subgroup id>": {"mean": <number>, "sd": <number>}, ...}' in prompt


def test_the_round_resolves_from_the_monthly_series_and_credits_the_workbook():
    series, _ = built(full_waves())
    res = profile_round.resolution(ROUND, series, CELLS)
    assert res["release_date"] == "2026-09-30"
    assert set(res["observed_dates"].values()) == {"2026-09-28"}
    # September's four waves are the 34th..37th of the ramp, mean 35.5
    for i, c in enumerate(CELLS):
        assert res["vector"][i] == LEVELS[LABELS[i]] + SEPT_BUMP, (c, res["vector"][i])
    # the published provenance names the workbook, not Civiqs
    assert "Economist/YouGov tracker workbook" in res["method"], res["method"]
    assert "Civiqs" not in res["method"]


def test_a_september_with_three_waves_never_resolves():
    """Fail loud: an incomplete month publishes no point, so there is nothing
    at or before the release date newer than the lock, and the round waits."""
    thin = [d for d in JAN_TO_SEP if d != "2026-09-28"]
    series, _ = built(waves_for(thin, {d: float(i) for i, d in enumerate(thin)}))
    try:
        profile_round.resolution(ROUND, series, CELLS)
    except ValueError as e:
        assert "predates the lock" in str(e), str(e)
    else:
        raise AssertionError("a three-wave September must not resolve")


def test_the_board_scores_the_round_and_ranks_the_better_profile_first():
    """The integration: freeze, resolve, score, rank -- on the crosstab cells,
    against the per-cell persistence null."""
    series, _ = built(full_waves())
    season = {"season": 0, "rounds": [ROUND]}
    with Clock("2026-10-01T00:00:00Z"), Scratch() as sc:
        rows, _ = refresh.build_rounds(season, series, {}, refresh.now_utc())
        truth = {c: LEVELS[LABELS[i]] + SEPT_BUMP for i, c in enumerate(CELLS)}
        sc.file("sharp", {c: {"mean": truth[c], "sd": 2.0} for c in CELLS})
        sc.file("biased", {c: {"mean": truth[c] + 9.0, "sd": 2.0} for c in CELLS})
        # an entrant with the national level right and no view on structure
        level = sum(truth.values()) / len(truth)
        sc.file("flat", {c: {"mean": level, "sd": 2.0} for c in CELLS})
        board = refresh.build_profile_leaderboard(rows, {}, series)

    assert board["scored_rounds"] == 1, board["skipped"]
    rnd = board["rounds"][0]
    assert rnd["round_id"] == ROUND["round_id"] and rnd["n_cells"] == 16
    entries = {e["entrant"]: e for e in rnd["entries"]}
    assert set(entries) == {"sharp", "biased", "flat"}
    assert entries["sharp"]["energy"] < entries["biased"]["energy"]
    assert entries["sharp"]["skill"] > 0, entries["sharp"]
    # the flat entrant has the level right and the structure entirely wrong,
    # which is the split this round type exists to expose
    assert entries["flat"]["level"] < entries["biased"]["level"]
    assert entries["flat"]["structure"] > 5 * entries["sharp"]["structure"], entries
    # skill is defined against the per-cell persistence null, not against zero
    per = rnd["persistence_energy"]
    assert abs(entries["sharp"]["skill"]
               - (1 - entries["sharp"]["energy"] / per)) < 1e-3


def test_a_partial_profile_is_excluded_and_named_never_repaired():
    """Filling a missing cell with the national mean would flatter exactly the
    entrant this round exists to catch: one with no view on structure."""
    series, _ = built(full_waves())
    season = {"season": 0, "rounds": [ROUND]}
    truth = {c: LEVELS[LABELS[i]] + SEPT_BUMP for i, c in enumerate(CELLS)}
    with Clock("2026-10-01T00:00:00Z"), Scratch() as sc:
        rows, _ = refresh.build_rounds(season, series, {}, refresh.now_utc())
        sc.file("whole", {c: {"mean": truth[c], "sd": 2.0} for c in CELLS})
        sc.file("holey", {c: {"mean": truth[c], "sd": 2.0} for c in CELLS[:-1]})
        board = refresh.build_profile_leaderboard(rows, {}, series)
    named = " ".join(f"{a} {b}" for a, b in board["skipped"])
    assert CELLS[-1] in named, board["skipped"]
    assert [e["entrant"] for e in board["rounds"][0]["entries"]] == ["whole"]


# --- the two sources must not interfere -------------------------------------

def test_the_civiqs_profile_path_is_untouched():
    """Two independent populations, one machinery. A crosstab round must not
    be able to change what a Civiqs round does."""
    assert set(series_registry.PROFILE_CELLS).isdisjoint(CELLS)
    assert len(series_registry.PROFILE_CELLS) == 16
    for c in series_registry.PROFILE_CELLS:
        assert series_registry.SERIES[c]["source"] == "civiqs", c
        assert series_registry.SERIES[c]["unit"] == \
            "net points (approve minus disapprove)", c
    # the default roster is still Civiqs: a profile round that names no cells
    # gets the sixteen it always got
    assert profile_round.CELLS == series_registry.PROFILE_CELLS
    bare = dict(ROUND)
    bare.pop("cells")
    assert list(profile_round.cells_for(bare)) == list(series_registry.PROFILE_CELLS)
    # and the Civiqs resolution still credits the Civiqs dashboard, verbatim
    assert profile_round.archive_phrase(series_registry.PROFILE_CELLS) == \
        "the archived Civiqs dashboard"


def test_a_crosstab_round_validates_against_the_repository_schema():
    """The submission format is the round type's, not the tracker's: a
    crosstab profile is the same object a Civiqs profile is, keyed by these
    sixteen series ids. Nothing in the schema or the validator needs to know
    this source exists -- which is the point of reusing the round type -- and
    this is the test that says so."""
    doc = {"round_id": ROUND["round_id"], "entrant": "some-model",
           "profile": {c: {"mean": 40.0, "sd": 3.0} for c in CELLS}}
    try:
        import jsonschema
    except Exception as e:                                     # noqa: BLE001
        # Not a silent pass: CI installs requirements.txt and runs this for
        # real, so an environment that cannot import the checker says so
        # rather than reporting a check it never made.
        print("   SKIPPED schema half: jsonschema will not import here "
              f"({type(e).__name__})")
    else:
        with open(os.path.join(ROOT, "schema", "forecast.schema.json")) as f:
            jsonschema.validate(doc, json.load(f))

    path = os.path.join(ROOT, "tools", "validate_submission.py")
    spec = importlib.util.spec_from_file_location("validate_submission_x", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # The validator reads the round's own roster, so it needs no crosstab
    # knowledge -- but it must actually hold a submission to these sixteen.
    mod.check_answer_matches_round("f.json", doc, ROUND)
    for bad in (dict(doc, profile={c: {"mean": 40.0, "sd": 3.0}
                                   for c in CELLS[:-1]}),
                {"round_id": ROUND["round_id"], "entrant": "x",
                 "topline": {"mean": 40.0, "sd": 3.0}}):
        try:
            mod.check_answer_matches_round("f.json", bad, ROUND)
        except SystemExit:
            pass
        else:
            raise AssertionError("a wrong-shaped answer must fail validation")


def test_averaging_four_waves_is_what_makes_these_cells_scoreable():
    """The measurement the whole round type rests on, shown on a fixture.

    Ten of the seventeen series in this workbook carry no weekly signal at all
    -- every point of their week-to-week movement is measurement noise -- and a
    round on a cell like that scores every entrant, including a perfect one, on
    a coin flip. Averaging the four waves dated in a month averages the noise
    down without throwing data away, and that is the only reason these rounds
    are monthly.

    Here that is a level that really moves plus an alternating +/-3 point
    measurement error. At wave level the error dominates; the four-wave mean
    cancels most of it, and the cell becomes forecastable.
    """
    import math
    bumps = {d: 5.0 * math.sin(i / 3.0) + (3.0 if i % 2 else -3.0)
             for i, d in enumerate(JAN_TO_SEP)}
    ws = waves_for(JAN_TO_SEP, bumps)

    weekly = crosstab.noise_by_cell(crosstab.profile_series(ws, LABELS), LABELS)
    monthly = crosstab.noise_by_cell(crosstab.monthly_profile(ws, LABELS), LABELS)
    assert set(weekly) == set(monthly) == set(LABELS)
    for label in LABELS:
        w, m = weekly[label], monthly[label]
        # the injected error is +/-3 points a wave, and the estimator finds it
        assert w["noise"] > 2.5, (label, w)
        # the four-wave mean cuts it to a fraction of that ...
        assert m["noise"] < w["noise"] / 3, (label, w, m)
        # ... and what is left is real movement the arena can score
        assert m["forecastable"] is True, (label, m)

    # every registered cell publishes its own measured pair, in points, because
    # they differ across these sixteen by a factor of six and a reader given
    # only the spread would read a noise floor as skill
    for c in CELLS:
        method = series_registry.SERIES[c]["methodology"]
        assert "weekly measurement noise is" in method, c
        assert "four-wave monthly average's is" in method, c


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"\n{len(tests)} crosstab round tests passed")
