"""Crosstab rounds: the subgroup structure of a wave, asked and scored as one.

`ssa/adapters/yougov_xtab.py` extracts the ground truth and `ssa/scoring.py`
knows how to score a vector. Neither was connected to the round lifecycle, so
no round could ask a crosstab question and no submission could be resolved
against one. This is that connection.

**Why these rounds are monthly and resolve against a four-wave average.**
Measured over all 81 published waves, ten of the seventeen series here carry no
weekly signal at all: for Republicans, the three older age bands, White, Male
and all four education bands, every point of week-to-week movement is
measurement noise. A weekly round on "Postgrad approval" is a round on a coin
flip -- every entrant including a perfect one is scored on noise, and the
arena score's ceiling collapses toward zero, which is exactly the regime where
it returns nonsense.

Averaging four consecutive waves roughly halves the noise (Democrat 1.05 ->
0.23 points, College grad 2.58 -> 0.74) because it averages noise down rather
than throwing data away. So the target is the mean of the four waves published
in the month, and the round is monthly. This is a design constraint the data
imposed, not a preference.

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
import statistics
from datetime import date, timedelta

from . import scoring
from .adapters import yougov_xtab

# Waves averaged into one target. Four is about a month of a weekly tracker and
# is where the noise reduction above was measured.
WAVES_PER_ROUND = 4

# The profile is scored in this fixed order everywhere -- submission,
# resolution, scoring -- so nothing has to carry labels alongside the numbers.
CELLS = yougov_xtab.SCORED_CELLS


def profile_series(waves, cells=None, measure="approve"):
    """[{date, values: [...], bases: [...]}] oldest first, one entry per wave."""
    names = cells or CELLS
    return [{"date": w["date"],
             "values": yougov_xtab.profile(w, measure, names),
             "bases": yougov_xtab.bases(w, names)}
            for w in waves]


def window(series, end_date, n=WAVES_PER_ROUND):
    """The n waves at or before `end_date`, oldest first.

    Fewer than n is returned as-is and the caller decides; a round that resolves
    on three waves is worse-resolved than one on four, and saying so beats
    silently averaging a different number of waves under the same name.
    """
    got = [p for p in series if p["date"] <= end_date]
    return got[-n:]


def target(series, end_date, n=WAVES_PER_ROUND):
    """(vector, detail) -- the mean profile over the window, per cell.

    `detail` records exactly which waves went in, because a mean of four
    numbers is not reproducible from the answer alone and a resolution has to
    be checkable.
    """
    got = window(series, end_date, n)
    if not got:
        raise ValueError(f"no waves at or before {end_date}")
    k = len(got[0]["values"])
    vec = [round(statistics.fmean(p["values"][i] for p in got), 4)
           for i in range(k)]
    return vec, {"waves": [p["date"] for p in got],
                 "n_waves": len(got),
                 "requested_waves": n}


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


def month_end(d):
    """Last day of d's month, as a date."""
    first_next = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return first_next - timedelta(days=1)
