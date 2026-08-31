"""The sixteen-cell profile round: one question, one joint answer.

Every other round in the arena asks for a number. This one asks for a
*population*: Civiqs publishes Trump net approval filtered to each bucket of
every demographic axis its dashboard exposes -- party (3), age (4), race (4),
education (3), gender (2) -- and a profile round asks an entrant to forecast
all sixteen at once, in one submission, scored as one object with the energy
score (`ssa/scoring.energy_score`).

**Why this is the headline round type.** The arena's question is whether a
simulated society forecasts real opinion, and sixteen separate scalar rounds
cannot answer it. Scored one cell at a time, an entrant that predicts the
national mood and copies it into every subgroup is indistinguishable from one
that knows Republicans and independents move differently -- their mean per-cell
CRPS is nearly identical, because the marginals are nearly identical. The joint
is the whole claim, and the energy score is what sees it. `scoring.profile_scores`
splits the result into `level` (the national mean, which a headline reader can
get right) and `structure` (the shape once the level is removed, which requires
a model of who the people are).

It also changes who can enter. A market, a poll aggregator or a single-number
forecaster has nothing to submit here: there is no line to quote on "the shape
of the electorate". That is not a side effect, it is the design -- the round
exists to be answerable only by something that simulates a population.

**Where the sixteen cells come from.** `series.PROFILE_CELLS`, in that fixed
order, everywhere: the round definition names them, the harness asks for exactly
those keys, the submission is stored under them and the outcome vector is read
in that order. One roster, so cell i can never be scored against cell j's
answer.

**Resolution and the freeze.** The outcome is each cell's own series value as
of the round's release date, read from the same archive every other round on
that source resolves from -- no hand-typed numbers. The baselines see only
history strictly before the round's freeze (`batches.freeze_at`), the identical
filter `refresh.build_rounds` applies to scalar rounds, applied sixteen times. Both halves fail loud rather
than guess: a cell with no post-lock release refuses the whole round rather
than resolving fifteen cells and quietly dropping one, because a profile with a
hole is not a profile.

**This module is no longer Civiqs-only, and nothing in it should become so
again.** A round names its own cells and every branch here reads them from the
round definition, so the same machinery now carries three unrelated
populations: the Civiqs modelled demographic profile (`series.PROFILE_CELLS`),
the five-brand Google Trends basket, and the Economist/YouGov crosstab
(`series.YOUGOV_XTAB_CELLS`) -- sixteen *measured* survey cells rather than
sixteen model outputs, which is the point of carrying a second one. The only
thing that had quietly hard-coded a source was the sentence a resolution
publishes about where its number came from; that now comes from
`ARCHIVE_PHRASE`, keyed by the cells' registered source, because a page
crediting Civiqs for a YouGov number is exactly the failure
`ssa/provenance.py` exists to prevent.
"""
from . import batches
from . import baselines, scoring
from . import series as series_registry

# The discriminator in `questions/season0.json`. Every existing round carries
# `"target_type": "continuous_normal"`; this is the second value, and every
# branch added for profile rounds keys on it rather than on the shape of the
# data, so a malformed round definition fails as a malformed profile round
# instead of silently being scored as a scalar one.
TARGET_TYPE = "profile_energy"

CELLS = series_registry.PROFILE_CELLS


def is_profile(r):
    """True for a profile round definition (or a built round row)."""
    return bool(r) and r.get("target_type") == TARGET_TYPE


def cells_for(r):
    """The round's cell roster, in scored order, validated.

    A round may name its cells explicitly; the default is the full sixteen.
    Either way every name has to be a registered series, because the outcome is
    read from that series and an unregistered cell is a round that can never
    resolve -- better said now, at build time, than after the release lands.
    """
    cells = tuple(r.get("cells") or CELLS)
    if len(cells) < 2:
        raise ValueError(
            f"{r.get('round_id')}: a profile round needs at least two cells; "
            "one cell is a scalar round and should be defined as one")
    if len(set(cells)) != len(cells):
        raise ValueError(f"{r.get('round_id')}: a profile cell is listed twice")
    unknown = [c for c in cells if c not in series_registry.SERIES]
    if unknown:
        raise ValueError(
            f"{r.get('round_id')}: unregistered profile cell(s): "
            f"{', '.join(unknown)}")
    return cells


def labels_for(cells):
    """{cell: human label} from the series registry, for prompts and the site."""
    return {c: series_registry.SERIES[c]["label"] for c in cells}


# --- the freeze ------------------------------------------------------------

def frozen_history(r, series, cells=None):
    """{cell: history strictly before the round's freeze}, oldest first.

    The same filter `refresh.build_rounds` applies to a scalar round, run once
    per cell. It is written here rather than inlined so that the sixteen cells
    cannot drift away from the one rule: without it, the moment a release lands
    in a cell's series the per-cell persistence null would contain the very
    value it is scored against.

    The freeze is `batches.freeze_at`, not `lock_at`. Those were the same
    instant until the weekly batch calendar separated them, and this function
    kept the old spelling through that change -- so every per-cell null on the
    round type this module calls the headline one was reading up to seven days
    of series its entrants never saw, which is precisely the bias
    `ssa/batches.py` exists to remove. `freeze_at` returns the lock itself for
    rounds that predate the cutover, so nothing already scored moves.
    """
    cells = cells or cells_for(r)
    lock_date = batches.freeze_at(r["lock_at"]).strftime("%Y-%m-%d")
    return {c: [p for p in (series.get(c) or []) if p["date"] < lock_date]
            for c in cells}


def persistence_profile(hist_by_cell, cells=None, min_points=3):
    """{cell: {mean, sd, method}} -- per-cell persistence, the skill denominator.

    Persistence on a profile is "every subgroup sits where it sat at the last
    release". It is the right null for the same reason it is right for a
    topline (opinion is close to a random walk) and for one more: it is the
    null that *has* the correct structure, since last week's profile is a real
    profile. An entrant only beats it by knowing which cells are about to move,
    which is exactly the skill the round is asking about.

    Raises on a cell without enough history rather than substituting a
    national value, because the denominator of a skill score must be a real
    forecast for every cell or the ratio means nothing.
    """
    cells = cells or tuple(hist_by_cell)
    thin = [c for c in cells if len(hist_by_cell.get(c) or []) < min_points]
    if thin:
        raise ValueError(
            f"profile persistence needs >= {min_points} pre-lock points per "
            f"cell; too little history for: {', '.join(thin[:5])}"
            f"{' ...' if len(thin) > 5 else ''}")
    return {c: baselines.persistence(hist_by_cell[c]) for c in cells}


def profile_baselines(r, series, cells=None):
    """The nulls a profile round ships with, frozen at lock.

    Persistence only, deliberately. It is the denominator every reported skill
    number is defined against; trend, ewma and climatology are informative on a
    scalar series but on sixteen cells they would multiply the leaderboard by
    four to say something the scalar civiqs rounds already say. The place to
    add them is here, and the cost of not having them is one column.
    """
    cells = cells or cells_for(r)
    hist = frozen_history(r, series, cells)
    return {"persistence": persistence_profile(hist, cells)}, hist


# --- resolution ------------------------------------------------------------

def cell_outcome(points, release_date, lock_date, cell):
    """One cell's value as of the release date. Raises rather than guesses.

    Two refusals, both of which would otherwise produce a resolution that looks
    finished and is wrong:

    - no observation at or before the release date: the archive has not caught
      up, and resolving on a later value would answer a different question;
    - the newest observation predates the lock: nothing has published since the
      round froze, so there is no release to score. Resolving anyway would hand
      the persistence null the exact value it forecast and score every entrant
      against a number that existed before they were asked.
    """
    got = [p for p in points if p["date"] <= release_date]
    if not got:
        raise ValueError(f"{cell}: no observation at or before {release_date}")
    last = got[-1]
    if last["date"] < lock_date:
        raise ValueError(
            f"{cell}: newest observation {last['date']} predates the lock "
            f"{lock_date}; nothing has published since the round froze")
    return last


# How each source's outcome archive is described in a published resolution.
# This string is written verbatim into `site/data.json` under the round's
# `resolution.method`, so it is a provenance claim, and it was hard-coded to
# "the archived Civiqs dashboard" back when Civiqs was the only profile source.
# The Trends basket and the Economist/YouGov crosstab are profile rounds over
# entirely different files; a page crediting Civiqs for a number Civiqs never
# published is exactly the failure ssa/provenance.py exists to prevent. Keyed
# by the cells' registered `source`, because that is what produced the number.
ARCHIVE_PHRASE = {
    "civiqs": "the archived Civiqs dashboard",
    "trends_basket": "the archived Google Trends comparison snapshots",
    "yougov_xtab": ("the Economist/YouGov tracker workbook, as the mean of the "
                    "four weekly waves dated in the scored month"),
}
DEFAULT_ARCHIVE_PHRASE = "the cells' own registered series archives"


def archive_phrase(cells):
    """How to describe where a profile round's outcome came from.

    Falls back to a source-neutral phrase when the cells do not share one
    source, rather than naming whichever source happened to be first: a mixed
    profile has no single archive, and picking one would misattribute the rest.
    """
    sources = {series_registry.SERIES[c]["source"] for c in cells
               if c in series_registry.SERIES}
    if len(sources) != 1:
        return DEFAULT_ARCHIVE_PHRASE
    return ARCHIVE_PHRASE.get(sources.pop(), DEFAULT_ARCHIVE_PHRASE)


def resolution(r, series, cells=None):
    """The outcome vector for a profile round, or a raised explanation.

    All sixteen or none. A partial profile cannot be scored -- the energy score
    is a norm over the whole vector -- and filling a hole with anything at all
    would reward the entrant with no view on the cell that went missing.
    """
    cells = cells or cells_for(r)
    release_date, lock_date = r["release_at"][:10], r["lock_at"][:10]
    values, dates, missing = {}, {}, []
    for c in cells:
        try:
            p = cell_outcome(series.get(c) or [], release_date, lock_date, c)
        except ValueError as e:
            missing.append(str(e))
            continue
        values[c], dates[c] = p["value"], p["date"]
    if missing:
        raise ValueError(
            f"{r['round_id']}: cannot resolve {len(missing)} of {len(cells)} "
            f"cells: {'; '.join(missing[:3])}"
            f"{' ...' if len(missing) > 3 else ''}")
    return {
        "cells": list(cells),
        "values": values,
        "observed_dates": dates,
        "vector": [values[c] for c in cells],
        "release_date": release_date,
        "method": ("each cell's own series value as of the release date, from "
                   f"{archive_phrase(cells)}; the same freeze the round's "
                   "per-cell persistence null used"),
    }


def outcome_vector(res, cells):
    """A stored resolution -> the vector in `cells` order. Raises on a hole."""
    values = res.get("values") or {}
    missing = [c for c in cells if c not in values]
    if missing:
        raise ValueError(
            f"resolution is missing {len(missing)} of {len(cells)} cells: "
            f"{', '.join(missing[:5])}{' ...' if len(missing) > 5 else ''}")
    return [float(values[c]) for c in cells]


# --- submissions -----------------------------------------------------------

def submission_cells(fc, cells):
    """A submission's `profile` block -> per-cell forecasts in scored order.

    Raises on a missing cell rather than substituting anything, for the reason
    in the module docstring: a profile with a hole is not a profile. It is also
    the only place that can catch it -- the schema knows a cell's shape but not
    which sixteen a given round asked for.
    """
    block = (fc or {}).get("profile")
    if not isinstance(block, dict):
        raise ValueError(
            f"{(fc or {}).get('entrant')}: a profile round needs a `profile` "
            "block; this submission has none")
    missing = [c for c in cells if not isinstance(block.get(c), dict)]
    if missing:
        raise ValueError(
            f"profile submission is missing {len(missing)} of {len(cells)} "
            f"cells: {', '.join(missing[:5])}"
            f"{' ...' if len(missing) > 5 else ''}")
    extra = [k for k in block if k not in cells]
    if extra:
        raise ValueError(
            f"profile submission has {len(extra)} cell(s) this round did not "
            f"ask for: {', '.join(sorted(extra)[:5])}"
            f"{' ...' if len(extra) > 5 else ''}")
    return [block[c] for c in cells]


def score_cells(cell_forecasts, outcome):
    """Energy score and its decomposition for one profile forecast."""
    if len(cell_forecasts) != len(outcome):
        raise ValueError(
            f"profile length {len(cell_forecasts)} != outcome {len(outcome)}")
    return scoring.profile_scores(scoring.cell_samples(cell_forecasts), outcome)


def score_submission(fc, outcome, cells):
    """Energy score and decomposition for a submitted forecast file."""
    return score_cells(submission_cells(fc, cells), outcome)


def persistence_cells(persistence, cells):
    """The persistence null as a list of cell forecasts, in scored order."""
    missing = [c for c in cells if c not in persistence]
    if missing:
        raise ValueError(
            f"persistence null is missing {len(missing)} of {len(cells)} cells")
    return [{"mean": persistence[c]["mean"], "sd": persistence[c]["sd"]}
            for c in cells]
