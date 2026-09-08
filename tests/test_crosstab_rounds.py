"""The Economist/YouGov crosstab as a profile round, end to end.

Run: PYTHONPATH=. python tests/test_crosstab_rounds.py

Plain asserts, no pytest, no network: every wave is a fixture, so the series
under test is this repository's code rather than something the fixture
pre-computed.

`tests/test_crosstab.py` holds the extractor and the joint scorer. This file
holds the wiring that turns them into a round -- the weekly series in the
registry, the freeze, the resolution, the board and the generator -- and in
particular the two places where this source can go wrong quietly:

- **a round resolved on last week's wave.** Under the batch calendar the
  freeze is a Monday, YouGov dates its waves by a Monday field end, and the
  previous wave -- public before every entrant answered -- is dated on the
  freeze day. A freeze-only guard passes it. `profile_round.resolution` adds
  the round's own seven-day wave window, and the stale archive is refused by
  name rather than scored.
- **a wave leaking into its own null.** The wave a round scores is published
  the day after the batch deadline; `date < freeze` keeps it out of every
  entrant's persistence null, and the pre-cutover round -- freeze on its own
  lock -- gets the same answer.
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa import batches, crosstab, harness, profile_round, refresh  # noqa: E402
from ssa import series as series_registry  # noqa: E402
from ssa.adapters import yougov_xtab  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CELLS = list(series_registry.YOUGOV_XTAB_CELLS)
LABELS = list(yougov_xtab.SCORED_CELLS)

_spec = importlib.util.spec_from_file_location(
    "_gen_xtab", os.path.join(ROOT, "tools", "generate_rounds.py"))
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


def season_rounds():
    with open(os.path.join(ROOT, "questions", "season0.json")) as fh:
        return json.load(fh)["rounds"]


def xtab_rounds():
    return sorted((r for r in season_rounds() if r["tracker"] == "yougov_xtab"),
                  key=lambda r: r["release_at"])


# The two reviewed rounds this file exercises, read from the season so a test
# cannot keep passing against a round that was edited out from under it. w38
# locks before the batch cutover (freeze == lock); w39 is governed by the
# first batch (freeze == Monday 2026-09-14 12:00Z, six days before its lock).
def _round(rid):
    got = [r for r in xtab_rounds() if r["round_id"] == rid]
    assert got, f"{rid} is no longer in the season"
    return got[0]


ROUND = _round("yougov-xtab-2026-w39")
PRE = _round("yougov-xtab-2026-w38")
assert not batches.governed_by_batch(PRE["lock_at"])
assert batches.governed_by_batch(ROUND["lock_at"])

# The cell roster is the registry constant, not a second copy of it: a round
# whose `cells` had drifted from `series.YOUGOV_XTAB_CELLS` would score cell i
# against cell j's answer, and a hand-copied list here could not catch it.
assert ROUND["cells"] == CELLS and PRE["cells"] == CELLS

# The wave calendar the real workbook carries from 2026-01 (Mondays), through
# the two waves the rounds above score: 2026-09-14 (w38) and 2026-09-21 (w39).
WAVES = (
    ["2026-01-05", "2026-01-12", "2026-01-19", "2026-01-26"]
    + ["2026-02-02", "2026-02-09", "2026-02-16", "2026-02-23"]
    + ["2026-03-02", "2026-03-09", "2026-03-16", "2026-03-23", "2026-03-30"]
    + ["2026-04-06", "2026-04-13", "2026-04-20", "2026-04-27"]
    + ["2026-05-04", "2026-05-11", "2026-05-18", "2026-05-26"]
    + ["2026-06-01", "2026-06-08", "2026-06-15", "2026-06-22", "2026-06-29"]
    + ["2026-07-06", "2026-07-13", "2026-07-20", "2026-07-27"]
    + ["2026-08-03", "2026-08-10", "2026-08-17", "2026-08-24", "2026-08-31"]
    + ["2026-09-07", "2026-09-14", "2026-09-21"]
)
W38_WAVE, W39_WAVE = "2026-09-14", "2026-09-21"

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


def ramp(dates):
    """Each wave one point above the last, so a resolution against the wrong
    wave shows up as a wrong number rather than as the same number."""
    return waves_for(dates, {d: float(i) for i, d in enumerate(dates)})


def full_waves():
    return ramp(WAVES)


def bump_of(date, dates=WAVES):
    return float(dates.index(date))


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

    def file(self, entrant, profile, rid=None):
        rid = rid or ROUND["round_id"]
        d = os.path.join(refresh.FORECASTS, rid)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, entrant + ".json"), "w") as f:
            json.dump({"round_id": rid, "entrant": entrant,
                       "profile": profile, "notes": "test"}, f)


# --- the weekly series ------------------------------------------------------

def test_the_series_is_one_point_per_wave_dated_by_the_wave():
    out, fetched = built(full_waves())
    assert fetched == [], "an injected source must not fetch"
    assert set(out) == set(CELLS)
    dem = out["yougov_xtab_approve_dem"]
    assert [p["date"] for p in dem] == WAVES
    assert dem[-1]["value"] == LEVELS["Democrat"] + bump_of(W39_WAVE)
    # one derivation shared by sixteen cells: every cell covers the same waves
    assert len({tuple(p["date"] for p in v) for v in out.values()}) == 1
    # and nothing aggregates: the module that averaged months is gone
    assert not hasattr(crosstab, "monthly_cell_series")
    assert not hasattr(crosstab, "WAVES_PER_ROUND")
    print("ok test_the_series_is_one_point_per_wave_dated_by_the_wave")


def test_the_registered_cells_are_exactly_the_adapter_roster_in_order():
    """The adapter owns what the workbook contains. A cell registered under a
    label the workbook does not carry is a round that can never resolve; a
    label missing from the registry is a sixteen-cell round scored on fifteen.
    The registry asserts this at import; this is the same check, said once
    where a reader looking for it will find it."""
    assert len(CELLS) == 16 == len(LABELS)
    got = [series_registry.SERIES[c]["yougov_xtab"]["cell"] for c in CELLS]
    assert got == list(yougov_xtab.SCORED_CELLS), got
    assert got[:3] == ["Democrat", "Independent", "Republican"]
    assert got[-1] == "Postgrad"
    flat = [c for cells in yougov_xtab.DIMENSIONS.values() for c in cells]
    assert sorted(got) == sorted(flat)
    assert "Other" in yougov_xtab.EXCLUDED
    assert not any("other" in c for c in CELLS)
    print("ok test_the_registered_cells_are_exactly_the_adapter_roster_in_order")


def test_every_cell_row_tells_an_entrant_what_it_is_graded_on():
    """The registry's own rule: anything the resolution uses and the prompt
    omits is a question the entrant cannot see but is scored on. Here that is
    the cadence, the cell's base size, its measured noise and its measured
    movement -- which is 0.00 on ten of the sixteen, and an entrant is
    entitled to know that before it is scored on one of them."""
    zeros = 0
    for c in CELLS:
        d = series_registry.describe(c)
        assert d["unit"] == "percent approving", c
        assert d["cadence"].startswith("weekly"), (c, d["cadence"])
        assert "month" not in d["cadence"], c
        assert "week's wave" in d["question"], c
        assert "median weighted base" in d["methodology"], c
        assert "weekly measurement noise is" in d["methodology"], c
        assert "real week-to-week movement is" in d["methodology"], c
        assert "four" not in d["question"], c
        if "movement is 0.00" in d["methodology"]:
            zeros += 1
        assert "Economist" in d["publisher"] and "YouGov" in d["publisher"], c
        assert "not a modelled estimate" in d["publisher"], c
        assert series_registry.SERIES[c]["source"] == "yougov_xtab", c
        assert series_registry.SERIES[c]["tracker"] == "yougov_xtab", c
        assert series_registry.survey(c) is None, c
    # The measurement the docstrings cite. Pinned so a re-measurement that
    # changes the count also has to change the prose that quotes it.
    assert zeros == 10, zeros
    print("ok test_every_cell_row_tells_an_entrant_what_it_is_graded_on")


# --- the freeze -------------------------------------------------------------

def test_the_freeze_excludes_the_wave_published_after_the_close():
    """The wave w39 scores is dated Monday 09-21 and published the next day.
    w39 closes on its own lock, 09-20 14:00Z, so its frozen history ends at
    the 09-14 wave; w38 closes 09-13 and ends at 09-07. Neither reads the
    wave it is scored against."""
    series, _ = built(full_waves())
    for r, last in ((ROUND, "2026-09-14"), (PRE, "2026-09-07")):
        freeze = batches.freeze_at(r["lock_at"]).strftime("%Y-%m-%d")
        hist = profile_round.frozen_history(r, series, CELLS)
        for c in CELLS:
            assert hist[c][-1]["date"] == last, (r["round_id"], c)
            assert all(p["date"] < freeze for p in hist[c]), c
    # The fixture must actually be able to leak, or this proves nothing: a
    # lock two batches on (freeze Monday 09-28) reads the 09-21 wave.
    late = dict(ROUND, lock_at="2026-09-29T14:00:00Z")
    leaked = profile_round.frozen_history(late, series, CELLS)
    assert leaked[CELLS[0]][-1]["date"] == W39_WAVE
    print("ok test_the_freeze_excludes_the_wave_published_after_the_deadline")


def test_the_round_builds_freezes_and_gets_a_per_cell_null():
    series, _ = built(full_waves())
    hist = profile_round.frozen_history(ROUND, series, CELLS)
    per = profile_round.persistence_profile(hist, CELLS)
    for c in CELLS:
        assert per[c]["mean"] == round(hist[c][-1]["value"], 2), c
        assert per[c]["sd"] > 0, c
    season = {"season": 0, "rounds": [ROUND]}
    with Clock("2026-09-21T00:00:00Z"):
        rows, _ = refresh.build_rounds(season, series, {}, refresh.now_utc())
    row = rows[0]
    assert row["status"] == "locked", row["status"]
    assert row["baselines"] is None, "a vector round has no scalar denominator"
    assert row["scoreable"] is True, row.get("baseline_note")
    assert row["profile"]["cells"] == CELLS
    assert set(row["profile"]["history_points"].values()) == {WAVES.index("2026-09-14") + 1}
    got = row["profile"]["baselines"]["persistence"]
    assert got[CELLS[0]]["mean"] == per[CELLS[0]]["mean"]
    print("ok test_the_round_builds_freezes_and_gets_a_per_cell_null")


def test_a_thin_history_refuses_the_null_instead_of_inventing_one():
    # One wave before the round closes (09-20) is not a history to baseline on.
    short = ["2026-09-14", "2026-09-21"]
    series, _ = built(waves_for(short))
    hist = profile_round.frozen_history(ROUND, series, CELLS)
    assert all(len(v) <= 1 for v in hist.values()), \
        {c: [p["date"] for p in v] for c, v in hist.items()}
    try:
        profile_round.persistence_profile(hist, CELLS)
    except ValueError as e:
        assert "pre-lock points per cell" in str(e), str(e)
    else:
        raise AssertionError("a thin cell must refuse the round")
    print("ok test_a_thin_history_refuses_the_null_instead_of_inventing_one")


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
    # The history shown is the frozen one: everything up to the round's close
    # and nothing after it. W38_WAVE (09-14) is before w39 closes and belongs;
    # W39_WAVE (09-21) is the wave being scored and must not appear.
    assert "2026-09-07" in prompt and W38_WAVE in prompt
    assert W39_WAVE not in prompt
    assert '{"<subgroup id>": {"mean": <number>, "sd": <number>}, ...}' in prompt
    print("ok test_the_prompt_asks_for_all_sixteen_cells_and_says_who_published_them")


# --- the resolution ---------------------------------------------------------

def test_the_round_resolves_on_its_own_wave_and_credits_the_workbook():
    series, _ = built(full_waves())
    res = profile_round.resolution(ROUND, series, CELLS)
    assert res["release_date"] == "2026-09-22"
    assert set(res["observed_dates"].values()) == {W39_WAVE}
    for i, c in enumerate(CELLS):
        assert res["vector"][i] == LEVELS[LABELS[i]] + bump_of(W39_WAVE), c
    assert "Economist/YouGov tracker workbook" in res["method"], res["method"]
    assert "Civiqs" not in res["method"]
    print("ok test_the_round_resolves_on_its_own_wave_and_credits_the_workbook")


def test_a_stale_archive_is_refused_by_name_never_resolved_on_last_weeks_wave():
    """The failure this round type makes easy: resolving on last week's survey
    because this week's has not landed yet.

    Two guards stop it, and with the round closing on its own lock they stop
    it at different distances. The freeze alone now refuses the 09-14 wave,
    because w39 closes 09-20 and that wave predates the close. The window
    guard is what still refuses a wave that is *after* the close but outside
    the seven days ending at the release."""
    stale = ramp([d for d in WAVES if d != W39_WAVE])
    series, _ = built(stale)
    freeze = batches.freeze_at(ROUND["lock_at"]).strftime("%Y-%m-%d")
    pts = series[CELLS[0]]
    try:
        profile_round.cell_outcome(pts, ROUND["release_at"][:10], freeze,
                                   CELLS[0])
    except ValueError as e:
        assert "predates the freeze" in str(e), str(e)
    else:
        raise AssertionError("last week's wave resolved a round it predates")
    try:
        profile_round.resolution(ROUND, series, CELLS)
    except ValueError as e:
        msg = str(e)
        assert "predates the freeze" in msg or "not this round's wave" in msg, msg
    else:
        raise AssertionError("last week's wave must not resolve this round")
    # A wave dated any day of the round's week resolves it: YouGov dates 13 of
    # 83 waves a Tuesday and 2 a Sunday.
    for day in ("2026-09-20", "2026-09-22"):
        moved = ramp([d for d in WAVES if d != W39_WAVE] + [day])
        series, _ = built(moved)
        res = profile_round.resolution(ROUND, series, CELLS)
        assert set(res["observed_dates"].values()) == {day}
    print("ok test_a_stale_archive_is_refused_by_name_never_resolved_on_last_weeks_wave")


def test_two_consecutive_rounds_never_share_a_wave():
    series, _ = built(full_waves())
    a = profile_round.resolution(PRE, series, CELLS)
    b = profile_round.resolution(ROUND, series, CELLS)
    assert set(a["observed_dates"].values()) == {W38_WAVE}
    assert set(b["observed_dates"].values()) == {W39_WAVE}
    assert a["vector"] != b["vector"]
    print("ok test_two_consecutive_rounds_never_share_a_wave")


def test_the_window_is_the_crosstab_source_only():
    """The rule is keyed by source. A Civiqs profile is a daily dashboard and
    keeps the freeze-only guard that `tests/test_batches.py` pins; a mixed
    roster has no single week and gets no window either."""
    assert profile_round.wave_window(CELLS) == ("yougov_xtab", 7)
    assert profile_round.wave_window(list(series_registry.PROFILE_CELLS)) is None
    assert profile_round.wave_window(CELLS[:8] + list(series_registry.PROFILE_CELLS[:8])) is None
    assert profile_round.WAVE_WINDOW_DAYS["yougov_xtab"] == crosstab.WAVE_WINDOW_DAYS
    print("ok test_the_window_is_the_crosstab_source_only")


# --- the board --------------------------------------------------------------

def test_the_board_scores_the_round_and_ranks_the_better_profile_first():
    series, _ = built(full_waves())
    season = {"season": 0, "rounds": [ROUND]}
    truth = {c: LEVELS[LABELS[i]] + bump_of(W39_WAVE) for i, c in enumerate(CELLS)}
    with Clock("2026-09-24T00:00:00Z"), Scratch() as sc:
        rows, _ = refresh.build_rounds(season, series, {}, refresh.now_utc())
        sc.file("sharp", {c: {"mean": truth[c], "sd": 2.0} for c in CELLS})
        sc.file("biased", {c: {"mean": truth[c] + 9.0, "sd": 2.0} for c in CELLS})
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
    assert entries["flat"]["level"] < entries["biased"]["level"]
    assert entries["flat"]["structure"] > 5 * entries["sharp"]["structure"], entries
    per = rnd["persistence_energy"]
    assert abs(entries["sharp"]["skill"]
               - (1 - entries["sharp"]["energy"] / per)) < 1e-3
    print("ok test_the_board_scores_the_round_and_ranks_the_better_profile_first")


def test_a_round_whose_wave_has_not_arrived_waits_on_the_board():
    """The board says why, by round, and scores nothing for it."""
    series, _ = built(ramp([d for d in WAVES if d != W39_WAVE]))
    season = {"season": 0, "rounds": [ROUND]}
    with Clock("2026-09-24T00:00:00Z"), Scratch() as sc:
        rows, _ = refresh.build_rounds(season, series, {}, refresh.now_utc())
        sc.file("sharp", {c: {"mean": 40.0, "sd": 2.0} for c in CELLS})
        board = refresh.build_profile_leaderboard(rows, {}, series)
    assert board["scored_rounds"] == 0
    named = " ".join(f"{a} {b}" for a, b in board["skipped"])
    assert ROUND["round_id"] in named, named
    assert ("not this round's wave" in named
            or "predates the freeze" in named), named
    print("ok test_a_round_whose_wave_has_not_arrived_waits_on_the_board")


def test_a_partial_profile_is_excluded_and_named_never_repaired():
    series, _ = built(full_waves())
    season = {"season": 0, "rounds": [ROUND]}
    truth = {c: LEVELS[LABELS[i]] + bump_of(W39_WAVE) for i, c in enumerate(CELLS)}
    with Clock("2026-09-24T00:00:00Z"), Scratch() as sc:
        rows, _ = refresh.build_rounds(season, series, {}, refresh.now_utc())
        sc.file("whole", {c: {"mean": truth[c], "sd": 2.0} for c in CELLS})
        sc.file("holey", {c: {"mean": truth[c], "sd": 2.0} for c in CELLS[:-1]})
        board = refresh.build_profile_leaderboard(rows, {}, series)
    named = " ".join(f"{a} {b}" for a, b in board["skipped"])
    assert CELLS[-1] in named, board["skipped"]
    assert [e["entrant"] for e in board["rounds"][0]["entries"]] == ["whole"]
    print("ok test_a_partial_profile_is_excluded_and_named_never_repaired")


# --- the two sources must not interfere -------------------------------------

def test_the_civiqs_profile_path_is_untouched():
    assert set(series_registry.PROFILE_CELLS).isdisjoint(CELLS)
    assert len(series_registry.PROFILE_CELLS) == 16
    for c in series_registry.PROFILE_CELLS:
        assert series_registry.SERIES[c]["source"] == "civiqs", c
        assert series_registry.SERIES[c]["unit"] == \
            "net points (approve minus disapprove)", c
    assert profile_round.CELLS == series_registry.PROFILE_CELLS
    bare = dict(ROUND)
    bare.pop("cells")
    assert list(profile_round.cells_for(bare)) == list(series_registry.PROFILE_CELLS)
    assert profile_round.archive_phrase(series_registry.PROFILE_CELLS) == \
        "the archived Civiqs dashboard"
    print("ok test_the_civiqs_profile_path_is_untouched")


def test_a_crosstab_round_validates_against_the_repository_schema():
    doc = {"round_id": ROUND["round_id"], "entrant": "some-model",
           "profile": {c: {"mean": 40.0, "sd": 3.0} for c in CELLS}}
    try:
        import jsonschema
    except Exception as e:                                     # noqa: BLE001
        print("   SKIPPED schema half: jsonschema will not import here "
              f"({type(e).__name__})")
    else:
        with open(os.path.join(ROOT, "schema", "forecast.schema.json")) as f:
            jsonschema.validate(doc, json.load(f))
    path = os.path.join(ROOT, "tools", "validate_submission.py")
    spec = importlib.util.spec_from_file_location("validate_submission_x", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
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
    print("ok test_a_crosstab_round_validates_against_the_repository_schema")


# --- the measurement, published not enforced --------------------------------

def test_noise_is_reported_per_cell_and_a_boundary_cell_is_still_asked():
    """A cell whose weekly wobble is all sampling reports movement 0.00 and
    `forecastable: False`. It is still one of the sixteen the round asks and
    scores: the joint is the claim, and dropping a cell for being noisy would
    let the roster drift with the estimator's boundary, which moved between
    the 81st and 83rd wave."""
    import math
    dates = WAVES
    moving = {d: 5.0 * math.sin(i / 3.0) for i, d in enumerate(dates)}
    flipping = {d: (3.0 if i % 2 else -3.0) for i, d in enumerate(dates)}
    ws = []
    for d in dates:
        w = wave(d)
        w["cells"]["Democrat"]["approve"] = LEVELS["Democrat"] + moving[d]
        w["cells"]["Republican"]["approve"] = LEVELS["Republican"] + flipping[d]
        ws.append(w)
    nb = crosstab.noise_by_cell(crosstab.profile_series(ws, LABELS), LABELS)
    assert nb["Democrat"]["forecastable"] and nb["Democrat"]["movement"] > 1.0, nb["Democrat"]
    assert not nb["Republican"]["forecastable"], nb["Republican"]
    assert nb["Republican"]["movement"] == 0.0 and nb["Republican"]["noise"] > 2.0
    # and the round still asks for Republicans
    assert "yougov_xtab_approve_rep" in ROUND["cells"]
    print("ok test_noise_is_reported_per_cell_and_a_boundary_cell_is_still_asked")


# --- the season and the generator ------------------------------------------

def test_the_season_carries_the_family_weekly_and_the_generator_rolls_it_forward():
    rounds = xtab_rounds()
    assert len(rounds) >= 6, [r["round_id"] for r in rounds]
    for r in rounds:
        rel = datetime.strptime(r["release_at"], "%Y-%m-%dT%H:%M:%SZ")
        lock = datetime.strptime(r["lock_at"], "%Y-%m-%dT%H:%M:%SZ")
        assert (rel - lock).total_seconds() == 48 * 3600, r["round_id"]
        assert rel.weekday() == 1, (r["round_id"], "releases on a Tuesday")
        assert r["cells"] == CELLS and r["series"] == gen.XTAB_SERIES
        assert r["target_type"] == "profile_energy"
        assert r["release_estimated"] is True
        assert "seven days" in r["resolve"] and "workbook" in r["resolve"]
        assert "never resolves on the previous wave" in r["resolve"]
        assert r["question"] == gen.XTAB_QUESTION.format(
            day=gen._monday_phrase(rel.date())), r["round_id"]
        # one id per ISO week, the same week the topline round uses
        year, week = rel.date().isocalendar()[:2]
        assert r["round_id"] == gen.XTAB_ID.format(year=year, week=week)
    gaps = {(datetime.strptime(b["release_at"], "%Y-%m-%dT%H:%M:%SZ")
             - datetime.strptime(a["release_at"], "%Y-%m-%dT%H:%M:%SZ")).days
            for a, b in zip(rounds, rounds[1:])}
    assert gaps == {7}, gaps
    # the crosstab and topline rounds of a week are the same survey wave
    topline = {r["round_id"]: r for r in season_rounds()
               if r["series"] == "yougov_approval"}
    for r in rounds:
        twin = topline.get(r["round_id"].replace("yougov-xtab-", "yougov-") + "-approval")
        if twin:
            assert twin["release_at"] == r["release_at"], r["round_id"]
            assert twin["lock_at"] == r["lock_at"], r["round_id"]

    # Rolled forward from the newest reviewed round, in its shape.
    now = datetime(2026, 9, 4, 12, tzinfo=timezone.utc)
    nxt = gen.xtab_candidates(season_rounds(), 3, now, through=date(2026, 11, 10))
    # The weeks after the newest reviewed one, contiguous, and never a week the
    # season already has. Computed rather than listed: the reviewed set grows
    # every time somebody promotes a batch, and a hard-coded trio pins the test
    # to the day it was written.
    newest = max(int(r["round_id"].rsplit("-w", 1)[1]) for r in rounds)
    want = [f"yougov-xtab-2026-w{newest + i}" for i in range(1, len(nxt) + 1)]
    assert nxt, "the roller offered nothing after the newest reviewed week"
    assert [r["round_id"] for r in nxt] == want, [r["round_id"] for r in nxt]
    for r in nxt:
        assert r["cells"] == CELLS and r["resolve"] == rounds[-1]["resolve"]
        assert r["profile_noun"] == "subgroup"
        rel = datetime.strptime(r["release_at"], "%Y-%m-%dT%H:%M:%SZ")
        assert r["question"] == gen.XTAB_QUESTION.format(
            day=gen._monday_phrase(rel.date()))
        profile_round.cells_for(r)
    # the generator's own dedupe: a reviewed week is not offered again
    assert not {r["round_id"] for r in nxt} & {r["round_id"] for r in rounds}

    # Drifted wording is refused, not paraphrased.
    drifted = [dict(r) for r in season_rounds()]
    for r in drifted:
        if r["round_id"] == rounds[-1]["round_id"]:
            r["question"] = r["question"].replace("sixteen", "16")
    try:
        gen.xtab_candidates(drifted, 1, now)
    except ValueError as e:
        assert "XTAB_QUESTION no longer reproduces" in str(e), str(e)
    else:
        raise AssertionError("a paraphrased reviewed round must stop generation")

    # And the scalar loop declines the cells by name rather than templating them.
    ok, why = gen.gate("yougov_xtab_approve_dem",
                       series_registry.SERIES["yougov_xtab_approve_dem"], [])
    assert not ok and why["gate"] == "declined_family", why
    assert "xtab_candidates" in why["detail"], why
    print("ok test_the_season_carries_the_family_weekly_and_the_generator_rolls_it_forward")


if __name__ == "__main__":
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            fn()
    print("passed")
