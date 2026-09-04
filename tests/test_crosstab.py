"""Tests for crosstab rounds: the wave's subgroup structure asked and scored
as one object. Plain asserts, no pytest, no network.

The extractor and the joint scorer were already tested; these cover the wiring
between them and the round lifecycle, which is where a profile can silently
become sixteen unrelated numbers.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa import crosstab, scoring
from ssa.adapters import newsdigest


def _series(rows):
    """rows: [(date, [values...])] -> the shape profile_series produces."""
    return [{"date": d, "values": list(v), "bases": [500.0] * len(v)}
            for d, v in rows]


def test_weekly_cell_series_is_one_point_per_wave_in_profile_order():
    """The registry's sixteen columns come from one read of the workbook, and
    a wave's cell i must land in cell i's series -- not a neighbour's."""
    from ssa.adapters import yougov_xtab
    labels = list(yougov_xtab.SCORED_CELLS)[:3]

    def wave(date, base):
        cells = {yougov_xtab.TOPLINE: {"approve": 40.0}}
        for i, name in enumerate(labels):
            cells[name] = {"approve": base + 10.0 * i, "base": 500.0}
        return {"date": date, "cells": cells}

    ws = [wave("2026-07-06", 40.0), wave("2026-07-13", 42.0)]
    out = crosstab.weekly_cell_series(ws, labels)
    assert list(out) == labels
    assert out[labels[0]] == [{"date": "2026-07-06", "value": 40.0},
                              {"date": "2026-07-13", "value": 42.0}]
    assert out[labels[2]][-1]["value"] == 62.0
    # nothing averages: a point per wave, dated by the wave
    assert [p["date"] for p in out[labels[1]]] == ["2026-07-06", "2026-07-13"]


def test_a_profile_with_a_hole_is_refused():
    """The energy score is over the whole vector. Filling a gap with the
    national mean would reward exactly the entrant this round exists to catch:
    one with no view on structure."""
    cells = crosstab.CELLS[:3]
    sub = {"all": {cells[0]: {"mean": 40, "sd": 3},
                   cells[1]: {"mean": 30, "sd": 3}}}
    try:
        crosstab.submission_vector(sub, cells)
        assert False, "a missing cell must raise"
    except ValueError as e:
        assert cells[2] in str(e), e
    # and a cell without an sd is missing, not a point forecast
    sub["all"][cells[2]] = {"mean": 20}
    try:
        crosstab.submission_vector(sub, cells)
        assert False, "a cell without sd must raise"
    except ValueError:
        pass


def test_scoring_separates_level_from_structure():
    """The whole reason crosstab rounds exist: an entrant that reads the
    national mood off a headline but has no idea how approval decomposes must
    score well on level and badly on structure."""
    cells = crosstab.CELLS[:4]
    outcome = [80.0, 10.0, 45.0, 25.0]          # mean 40
    right = {"all": {c: {"mean": v, "sd": 3.0} for c, v in zip(cells, outcome)}}
    flat = {"all": {c: {"mean": 40.0, "sd": 3.0} for c in cells}}

    r = crosstab.score_submission(right, outcome, cells)
    f = crosstab.score_submission(flat, outcome, cells)

    assert r["energy"] < f["energy"], (r["energy"], f["energy"])
    # the flat entrant has the level exactly right ...
    assert f["level"] < 1.0, f["level"]
    # ... and the structure entirely wrong
    assert f["structure"] > 5 * r["structure"], (f["structure"], r["structure"])


def test_energy_beats_averaging_per_cell_crps():
    """Two entrants with identical marginals differ only in the joint. Mean
    per-cell CRPS cannot tell them apart; the energy score must."""
    # The outcome has to sit off the joint centre for the joint to matter: at
    # the centre both sample sets are equidistant and the scores coincide,
    # which is correct and is why this once looked like a bug in the scorer.
    outcome = [54.0, 46.0]                       # the cells moved oppositely
    together = [[48.0, 48.0], [52.0, 52.0]]      # believes they move as one
    apart = [[48.0, 52.0], [52.0, 48.0]]         # believes they move oppositely
    e_t = scoring.energy_score(together, outcome)
    e_a = scoring.energy_score(apart, outcome)
    assert e_a < e_t - 0.1, (e_a, e_t)           # the right joint wins
    # identical marginals by construction
    for i in range(2):
        a = sorted(x[i] for x in together)
        b = sorted(x[i] for x in apart)
        assert a == b


def test_noise_is_published_per_cell_with_a_forecastable_flag():
    """A cell whose noise exceeds its real movement cannot be forecast by
    anyone. A leaderboard that does not say so invites the reader to read the
    resulting spread as skill."""
    # A level that genuinely moves, with no measurement noise on top. Not a
    # straight ramp: constant increments have zero variance, so the estimator
    # correctly reports no movement and the fixture, not the code, is wrong.
    import math
    moving = _series([(f"2026-01-{i+1:02d}", [40 + 5 * math.sin(i / 3.0)])
                      for i in range(30)])
    nb = crosstab.noise_by_cell(moving, crosstab.CELLS[:1])
    cell = crosstab.CELLS[0]
    assert nb[cell]["forecastable"] is True, nb

    # alternating noise around a fixed level: no real movement at all
    flip = _series([(f"2026-01-{i+1:02d}", [40.0 + (5 if i % 2 else -5)])
                    for i in range(30)])
    nb = crosstab.noise_by_cell(flip, crosstab.CELLS[:1])
    assert nb[cell]["forecastable"] is False, nb
    assert nb[cell]["noise"] > nb[cell]["movement"]


def test_a_digest_for_a_lock_that_has_not_happened_is_never_archived():
    """The window is the fourteen days before the lock, so before the lock most
    of it has not happened. Pre-fetching a round ten days out produced six days
    of fourteen; archiving that would be worse than no cache, because for_round
    serves the archive whenever the asof matches and the round would then use
    the truncated copy at lock time instead of the corpus that existed by then."""
    from datetime import datetime, timezone
    now = datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)
    assert not newsdigest._window_closed("2026-08-22T14:00:00Z", now)
    assert newsdigest._window_closed("2026-08-01T14:00:00Z", now)
    # exactly at the lock counts as closed
    assert newsdigest._window_closed("2026-08-12T10:00:00Z", now)


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all crosstab tests passed")
