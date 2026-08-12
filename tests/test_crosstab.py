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


def test_target_averages_the_window_and_says_which_waves():
    """A mean of four numbers is not checkable from the answer alone, so the
    resolution records the waves it used."""
    s = _series([("2026-07-06", [40, 10]), ("2026-07-13", [42, 12]),
                 ("2026-07-20", [44, 14]), ("2026-07-27", [46, 16]),
                 ("2026-08-03", [90, 90])])
    vec, detail = crosstab.target(s, "2026-07-31")
    assert vec == [43.0, 13.0], vec
    assert detail["waves"] == ["2026-07-06", "2026-07-13",
                               "2026-07-20", "2026-07-27"]
    assert detail["n_waves"] == 4
    # a wave after the window must not leak in
    assert "2026-08-03" not in detail["waves"]


def test_a_short_window_is_reported_not_padded():
    """Three waves is worse-resolved than four. Saying so beats silently
    averaging a different number of waves under the same name."""
    s = _series([("2026-07-13", [40]), ("2026-07-20", [44])])
    vec, detail = crosstab.target(s, "2026-07-31")
    assert vec == [42.0]
    assert detail["n_waves"] == 2 and detail["requested_waves"] == 4


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


def test_month_end():
    from datetime import date
    assert crosstab.month_end(date(2026, 8, 3)) == date(2026, 8, 31)
    assert crosstab.month_end(date(2026, 12, 1)) == date(2026, 12, 31)
    assert crosstab.month_end(date(2028, 2, 5)) == date(2028, 2, 29)


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all crosstab tests passed")
