"""Reference forecasts that run in every round.

- persistence: copy the last published value of the target series.
  Opinion is close to a random walk, so this is the headline null.
- trend: ordinary least squares over the recent history, extrapolated to the
  release date. sd inflated by the fit's residual error.

Both return the same shape a submitted forecast uses: {"mean": .., "sd": ..}.
The demographics-lookup baseline applies to crosstab questions and ships with
the crosstab rounds; the poll-average snapshot baseline is just
average.adjusted_average at lock time.
"""
from datetime import date


def persistence(history, sd=1.5):
    """history: [{date/'date', value}], oldest first. sd defaults to typical
    release-to-release movement of slow trackers."""
    last = history[-1]["value"]
    return {"mean": round(last, 2), "sd": sd, "method": "persistence"}


def trend(history, target_date, lookback=8, min_sd=1.0, max_horizon_days=21):
    """OLS over the last `lookback` points, evaluated at target_date.

    Extrapolation is clamped to `max_horizon_days` past the last observation:
    a local slope pushed months out produces nonsense, and clamping keeps the
    baseline honest when a source lags. sd grows with the real horizon.
    """
    pts = history[-lookback:]
    if len(pts) < 3:
        return persistence(history)
    xs = [_ordinal(p["date"]) for p in pts]
    ys = [p["value"] for p in pts]
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return persistence(history)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    intercept = my - slope * mx
    resid = [y - (intercept + slope * x) for x, y in zip(xs, ys)]
    rse = (sum(r * r for r in resid) / max(n - 2, 1)) ** 0.5
    last_x = xs[-1]
    target_x = min(_ordinal(target_date), last_x + max_horizon_days)
    horizon = max(_ordinal(target_date) - last_x, 0)
    mean = intercept + slope * target_x
    sd = max(rse * 1.5, min_sd) * (1 + horizon / 30.0) ** 0.5
    return {"mean": round(mean, 2), "sd": round(sd, 2), "method": "trend_ols"}


def ewma(history, alpha=0.4, min_sd=1.0):
    """Exponentially weighted moving average of the series."""
    if not history:
        raise ValueError("empty history")
    m = history[0]["value"]
    for p in history[1:]:
        m = alpha * p["value"] + (1 - alpha) * m
    diffs = [abs(b["value"] - a["value"]) for a, b in zip(history, history[1:])][-12:]
    sd = max(sum(diffs) / len(diffs) if diffs else min_sd, min_sd)
    return {"mean": round(m, 2), "sd": round(sd, 2), "method": "ewma"}


def climatology(history, lookback=24, min_sd=1.0):
    """Long-run mean and spread of the series: the 'seasonal average' null."""
    pts = [p["value"] for p in history[-lookback:]]
    if not pts:
        raise ValueError("empty history")
    m = sum(pts) / len(pts)
    var = sum((x - m) ** 2 for x in pts) / max(len(pts) - 1, 1)
    return {"mean": round(m, 2), "sd": round(max(var ** 0.5, min_sd), 2), "method": "climatology"}


# All four nulls stay in the default set. `persistence` is required -- it is the
# denominator of the skill score, so every reported number is defined against
# it. The other three are kept because they are informative in different
# directions: `ewma` is the hardest statistical null to beat, while `trend` and
# `climatology` scoring far *worse* than doing nothing is itself the finding
# that these series are near random walks on this horizon. `names` lets a caller
# score against a subset without changing what the arena publishes.
DEFAULT = ("persistence", "trend", "ewma", "climatology")
ALL = DEFAULT


# The ranked zero point: which reference is a round's skill denominator.
# Every round scored so far is `persistence`, and a round without the key keeps
# it, so the season's existing scores cannot move (tests/test_zero_point.py).
# `yoy4` is the Seattle calls family's 4-week year-over-year reference, with its
# own spread (ssa/adapters/seattle_cad.py, card seattle-community-calls@v3): a
# fixed persistence sd of 1.5 on a count of ~4,750 would have been a point
# forecast, and submitting the reference itself with an honest spread would
# have scored +27% skill. The reference a round names must be present in its
# `baselines`; a missing one raises rather than falling back to persistence.
DEFAULT_ZERO_POINT = "persistence"
ZERO_POINTS = ("persistence", "yoy4")


def zero_point_name(r):
    name = r.get("zero_point", DEFAULT_ZERO_POINT)
    if name not in ZERO_POINTS:
        raise ValueError(f"{r.get('round_id')}: unknown zero_point {name!r}; "
                         f"known: {', '.join(ZERO_POINTS)}")
    return name


def zero_point(r):
    """The round's ranked reference forecast, {mean, sd, ...}."""
    name = zero_point_name(r)
    ref = (r.get("baselines") or {}).get(name)
    if not ref:
        raise KeyError(f"{r.get('round_id')}: zero_point {name!r} is not among "
                       "its baselines; refusing to score against another null")
    return ref


def all_baselines(history, target_date, names=DEFAULT):
    fns = {
        "persistence": lambda: persistence(history),
        "trend": lambda: trend(history, target_date),
        "ewma": lambda: ewma(history),
        "climatology": lambda: climatology(history),
    }
    unknown = [n for n in names if n not in fns]
    if unknown:
        raise ValueError(f"unknown baseline(s): {', '.join(unknown)}")
    return {n: fns[n]() for n in names}


def _ordinal(d):
    if isinstance(d, str):
        d = date.fromisoformat(d[:10])
    return d.toordinal()
