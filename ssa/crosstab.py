"""Crosstab rounds: the subgroup structure of a wave, asked and scored as one.

`ssa/adapters/yougov_xtab.py` extracts the ground truth and `ssa/scoring.py`
knows how to score a vector. This module is the connection between them and
the round lifecycle: it turns the workbook's waves into the sixteen registered
cell series, and it holds the measurement that says what a round on those
cells can and cannot tell anyone.

**One round per wave.** The tracker is weekly and so is the round: each wave
YouGov puts in the workbook is one sixteen-cell target, dated by the wave's own
date, resolved against that wave and no other. `profile_round.resolution`
enforces the "no other" -- a wave outside the round's own week is refused by
name rather than substituted, because a round scored against last week's
survey is a round whose answer every entrant already had.

**What the noise measurement says, and what it does not.** Measured over the
83 waves published 2025-01-28 to 2026-08-24 (`scoring.noise_floor`, per cell,
from the cell's own first differences), six of the sixteen cells carry real
week-to-week movement and ten do not:

    cell           noise  movement        cell           noise  movement
    Democrat        1.12    0.87          Republican      1.94    0.00
    Independent     2.06    1.38          30-44 / 45-64 / 65+     0.00
    Under 30        2.82    1.01          White / Hispanic / Male 0.00
    Black           1.98    1.57          HS / some college / college grad 0.00
    Female          1.21    0.65
    Postgrad        3.43    1.09          (topline, for scale: 0.78 / 0.48)

A movement of 0.00 means the estimator hit its boundary: over these waves,
every point of that cell's weekly wobble is explained by respondents changing
rather than opinion changing. It is not a claim that Republicans never move;
it is a statement that a week is too short to see them move through a sample
of four hundred. The boundary is also unstable at the margin -- between the
81st and 83rd wave Hispanic crossed from "moves" to "noise" and Postgrad the
other way -- so the split is published as a description, never used as a
scoring rule.

**Why the round is weekly anyway.** An earlier design made the round monthly,
resolved against the mean of the month's four waves, on the argument that
averaging four waves halves the noise. The argument is right about the
target and wrong about the task: that round locked on the date of the month's
*last* wave, by which time the other three were public. What an entrant had
to forecast was one wave divided by four -- the same signal-to-noise as a
weekly round -- with a quarter as many rounds, and a persistence null built
from the previous month's mean that any reader of the public tracker beat
for free. Weekly loses nothing that design actually had, and gains the round
count that lets the season average do the only thing that ever removes
sampling noise from a score.

The consequences for anyone reading a crosstab score:

  - the ten boundary cells add the same expected noise to every entrant's
    energy score; they widen a single round's spread and bias nobody, and a
    season of them narrows the spread by the square root of the round count;
  - `noise_by_cell` is what to publish beside a board, so the spread on a
    cell is never mistaken for skill on it;
  - `arena_score` is season-level only, as its docstring already insists.

**Why the whole profile is one round.** Scoring sixteen cells as sixteen
separate rounds would score the marginals and discard the joint, and the joint
is the entire claim: two entrants with identical per-cell uncertainty, one
believing the subgroups move together and one believing they move
independently, would be indistinguishable. `scoring.profile_scores` keeps them
apart and splits the result into `level` (the national mean) and `structure`
(the shape once the level is removed), which is the split that separates a
model reading the national mood off a headline from one that has a model of a
society.
"""
from . import scoring
from .adapters import yougov_xtab

# The profile is scored in this fixed order everywhere -- submission,
# resolution, scoring -- so nothing has to carry labels alongside the numbers.
CELLS = yougov_xtab.SCORED_CELLS

# The tracker's own week. A wave is dated by its field end, a Monday in 68 of
# 83 waves, a Tuesday in 13 and a Sunday in 2, and enters the workbook within
# days. A round asks about the one wave dated inside the seven days ending on
# its release date; `profile_round.WAVE_WINDOW_DAYS` reads this.
WAVE_WINDOW_DAYS = 7


def profile_series(waves, cells=None, measure="approve"):
    """[{date, values: [...], bases: [...]}] oldest first, one entry per wave."""
    names = cells or CELLS
    return [{"date": w["date"],
             "values": yougov_xtab.profile(w, measure, names),
             "bases": yougov_xtab.bases(w, names)}
            for w in waves]


def weekly_cell_series(waves, cells=None, measure="approve"):
    """{cell: [{date, value}]} -- the weekly series the registry publishes.

    One point per wave, dated by the wave. One call over the whole workbook
    produces all sixteen cells, which is why `series.build_all` derives it
    once and hands each registered cell its own column: sixteen independent
    reads of one payload would be sixteen chances for the cells to disagree
    about which waves exist.
    """
    names = list(cells or CELLS)
    points = profile_series(waves, names, measure)
    return {c: [{"date": p["date"], "value": p["values"][i]} for p in points]
            for i, c in enumerate(names)}


def submission_vector(crosstabs, cells=None, dimension="all"):
    """A submission's `crosstabs` block -> (means, sds) in profile order.

    Raises on a missing cell rather than substituting anything. A profile with
    a hole is not a profile: the energy score is over the whole vector, and
    filling a gap with the national mean would quietly reward exactly the
    entrant this round exists to catch -- one with no view on structure.
    """
    names = cells or CELLS
    block = (crosstabs or {}).get(dimension) or {}
    means, sds, missing = [], [], []
    for n in names:
        cell = block.get(n)
        if not isinstance(cell, dict) or "mean" not in cell or "sd" not in cell:
            missing.append(n)
            continue
        means.append(float(cell["mean"]))
        sds.append(float(cell["sd"]))
    if missing:
        raise ValueError(
            f"crosstab submission is missing {len(missing)} of {len(names)} "
            f"cells: {', '.join(missing[:5])}"
            f"{' ...' if len(missing) > 5 else ''}")
    return means, sds


def score_submission(crosstabs, outcome, cells=None, dimension="all"):
    """Energy score and its decomposition for one submission against one wave."""
    means, sds = submission_vector(crosstabs, cells, dimension)
    if len(means) != len(outcome):
        raise ValueError(f"profile length {len(means)} != outcome {len(outcome)}")
    samples = scoring.profile_samples(means, sds)
    return scoring.profile_scores(samples, outcome)


def noise_by_cell(series, cells=None):
    """{cell: measurement noise in points}, from each cell's own history.

    Published beside the score. A cell whose noise exceeds its real movement
    cannot be forecast by anyone, and a leaderboard that does not say so
    invites a reader to interpret the resulting spread as skill.
    """
    names = cells or CELLS
    out = {}
    for i, name in enumerate(names):
        vals = [p["values"][i] for p in series]
        try:
            m, s = scoring.noise_floor(vals)
        except ValueError:
            continue
        out[name] = {"noise": round(m, 3), "movement": round(s, 3),
                     "forecastable": s > 0}
    return out
