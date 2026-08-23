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


# --- the calendar month, which is the unit a crosstab round is scored on -----
#
# `target` above averages "the last four waves at or before a date", which is
# the right rule for a rolling window and the wrong one for a month. Asked for
# August 2026 -- three waves published so far -- it happily reaches back into
# July for a fourth and returns a number labelled August that is one quarter
# July. The functions below exist so that cannot happen: every one of them
# filters to the month *first* and only then averages, so the boundary is
# structural rather than something a caller has to remember to check.
#
# **Why a month is refused rather than shortened.** The whole justification for
# this round type is the measured noise reduction of averaging four waves
# (module docstring above). A three-wave average is a different estimator with
# a different noise floor, and publishing it under the same series name would
# make two rounds' scores incomparable while looking identical in the data. The
# tracker's own calendar makes this common, not hypothetical: of the twenty
# months from 2025-01 to 2026-08, one has a single wave (the tracker's first
# month), one has three so far (the month in progress) and six have five.

def month_end_iso(month):
    """'YYYY-MM' -> the ISO date of that month's last day."""
    return month_end(date(int(month[:4]), int(month[5:7]), 1)).isoformat()


def months_present(series):
    """The calendar months the series carries waves in, 'YYYY-MM', oldest first."""
    seen = []
    for p in series:
        m = p["date"][:7]
        if m not in seen:
            seen.append(m)
    return seen


def monthly_coverage(series):
    """{'YYYY-MM': waves dated in it} -- what `monthly_profile` kept and dropped.

    Published rather than logged, so a caller can say *why* a month is missing
    from a monthly series. A month absent because the tracker has not finished
    it and a month absent because the extractor dropped an incomplete wave look
    identical in the output series and are entirely different problems.
    """
    out = {}
    for p in series:
        out[p["date"][:7]] = out.get(p["date"][:7], 0) + 1
    return out


def month_target(series, month, n=WAVES_PER_ROUND):
    """(vector, detail) -- the n-wave mean for one calendar month. Raises if short.

    The month is isolated before the window is taken, so a short month can
    never borrow the previous month's last wave to make up its count. It raises
    instead, for the reason in the block comment above: a three-wave average is
    a different estimator wearing the same name, and every score computed
    against it would be quietly incomparable with every other month's.

    A month with *more* than n waves -- six of the twenty months on this
    tracker have five -- keeps the last n. Fixing n rather than averaging
    whatever the month happens to carry is what keeps the target's own noise
    floor the same number every month, which is the property that makes two
    rounds' energy scores comparable at all.
    """
    inside = [p for p in series if p["date"][:7] == month]
    if len(inside) < n:
        raise ValueError(
            f"{month} carries {len(inside)} wave(s), not {n}: refusing to "
            f"publish a {n}-wave average computed from {len(inside)}")
    vec, detail = target(inside, month_end_iso(month), n)
    detail["month"] = month
    detail["waves_in_month"] = len(inside)
    return vec, detail


def monthly_profile(waves, cells=None, measure="approve", n=WAVES_PER_ROUND):
    """[{month, date, values, bases, waves}] -- one point per *complete* month.

    Oldest first. A month that cannot produce an n-wave average is not in the
    output, because there is no n-wave average for it to be: the loud refusal
    lives in `month_target`, which is the function that would otherwise return
    a wrong number, and `monthly_coverage` names every month either way so the
    omission is inspectable rather than mysterious.

    Each point is dated by the newest wave in its own average -- the month's
    last wave. Not by the month label and not by the month's last day: the
    round lifecycle freezes history with a string comparison on ISO dates
    (`refresh.build_rounds`, `profile_round.frozen_history`), so a point dated
    later than the observation it summarises would be excluded from a history
    it belongs in, and one dated earlier would leak into a history it does not.

    `bases` is the mean *per-wave* weighted base across the averaged waves, not
    the effective base of the average. The waves share a panel, so the four are
    not independent draws and multiplying by four would overstate precision by
    an unknown amount. What the noise reduction actually is was measured, not
    computed -- see the module docstring.
    """
    names = list(cells or CELLS)
    series = profile_series(waves, names, measure)
    out = []
    for m in months_present(series):
        try:
            vec, detail = month_target(series, m, n)
        except ValueError:
            continue
        # Re-selected inside the month, not by date alone: the month filter is
        # what makes the boundary structural everywhere in this block, and a
        # lookup keyed only on the date would quietly reintroduce the one bug
        # these functions exist to prevent if a wave date ever repeated.
        chosen = set(detail["waves"])
        got = [p for p in series
               if p["date"][:7] == m and p["date"] in chosen]
        k = len(names)
        out.append({
            "month": m,
            "date": detail["waves"][-1],
            "values": vec,
            "bases": [round(statistics.fmean(p["bases"][i] for p in got), 1)
                      for i in range(k)],
            "waves": list(detail["waves"]),
        })
    return out


def monthly_cell_series(waves, cells=None, measure="approve", n=WAVES_PER_ROUND):
    """{cell: [{date, value}]} -- the monthly series the registry publishes.

    One call over the whole workbook produces all sixteen cells, which is why
    `series.build_all` derives it once and hands each registered cell its own
    column: sixteen independent aggregations of one payload would be sixteen
    chances for the cells to disagree about which waves September had.
    """
    names = list(cells or CELLS)
    points = monthly_profile(waves, names, measure, n)
    return {c: [{"date": p["date"], "value": p["values"][i]} for p in points]
            for i, c in enumerate(names)}
