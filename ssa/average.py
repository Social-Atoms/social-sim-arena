"""Poll averaging with house-effect adjustment.

The resolution target for average-based questions. Deliberately simple and
fully deterministic, in the spirit of Jackman (2005): pool the polls, give
each pollster a measurable lean (house effect), subtract it, then take a
recency and sample-size weighted mean.

Steps, in plain words:
1. Rolling raw average: for every day, average all polls whose field midpoint
   falls in the trailing window, weighted by recency and sqrt(sample size).
2. House effect per pollster: mean gap between that pollster's polls and the
   rolling raw average on the same day, over the trailing 180 days. Shrunk
   toward zero for pollsters with few polls.
3. Adjusted average: step 1 again, with each poll shifted by minus its
   pollster's house effect.
"""
import math
from datetime import date, timedelta

WINDOW_DAYS = 21
HALFLIFE_DAYS = 10.0
HOUSE_LOOKBACK_DAYS = 180
HOUSE_SHRINK_K = 3.0  # effective prior strength, in polls


def _weight(poll, asof):
    age = (asof - poll["date"]).days
    if age < 0 or age > WINDOW_DAYS:
        return 0.0
    recency = 0.5 ** (age / HALFLIFE_DAYS)
    size = math.sqrt(min(poll["n"], 3000) / 1000.0)
    return recency * size


def raw_average(polls, asof, key="value"):
    num = den = 0.0
    for p in polls:
        w = _weight(p, asof)
        if w > 0:
            num += w * p[key]
            den += w
    return num / den if den > 0 else None


def house_effects(polls, asof, key="value"):
    """pollster -> shrunken mean gap vs the same-day raw rolling average."""
    start = asof - timedelta(days=HOUSE_LOOKBACK_DAYS)
    gaps = {}
    for p in polls:
        if p["date"] < start or p["date"] > asof:
            continue
        base = raw_average(polls, p["date"], key)
        if base is None:
            continue
        gaps.setdefault(p["pollster"], []).append(p[key] - base)
    effects = {}
    for pollster, g in gaps.items():
        effects[pollster] = sum(g) / (len(g) + HOUSE_SHRINK_K)
    return effects


def adjusted_average(polls, asof=None, key="value", effects=None):
    """House-effect-adjusted average as of a date. Returns (value, n_polls_in_window).

    Pass precomputed `effects` when calling repeatedly over many dates: house
    effects move slowly, so one computation at the anchor date is fine for a
    year of weekly points, and it avoids quadratic recomputation."""
    asof = asof or date.today()
    if effects is None:
        effects = house_effects(polls, asof, key)
    adjusted = []
    n_window = 0
    for p in polls:
        q = dict(p)
        q[key] = p[key] - effects.get(p["pollster"], 0.0)
        adjusted.append(q)
        if _weight(p, asof) > 0:
            n_window += 1
    return raw_average(adjusted, asof, key), n_window


def weekly_series(polls, n_weeks=52, key="value"):
    """Weekly adjusted-average points ending at the newest poll date.
    House effects computed once at the anchor and reused."""
    if not polls:
        return []
    anchor = polls[-1]["date"]
    effects = house_effects(polls, anchor, key)
    out = []
    for weeks_back in range(n_weeks - 1, -1, -1):
        asof = anchor.fromordinal(anchor.toordinal() - 7 * weeks_back)
        val, _ = adjusted_average(polls, asof, key, effects=effects)
        if val is not None:
            out.append({"date": asof.isoformat(), "value": round(val, 2)})
    return out
