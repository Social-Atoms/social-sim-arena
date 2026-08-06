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


def all_baselines(history, target_date):
    return {
        "persistence": persistence(history),
        "trend": trend(history, target_date),
        "ewma": ewma(history),
        "climatology": climatology(history),
    }


def _ordinal(d):
    if isinstance(d, str):
        d = date.fromisoformat(d[:10])
    return d.toordinal()
