"""Scoring rules. All strictly proper (Gneiting and Raftery 2007).

- CRPS for continuous toplines, submitted as a normal (mean, sd).
  Plain words: absolute error, generalized to distributions. Confident and
  right scores best, confident and wrong scores worst. Lower is better.
- RPS for ordered categorical bins.
- Skill: 1 - CRPS(entrant) / CRPS(persistence). Positive means better than
  copying the last release, the same move weather forecasting makes against
  its persistence and climatology nulls.
"""
import math

SQRT_PI = math.sqrt(math.pi)


def _phi(z):  # standard normal pdf
    return math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)


def _Phi(z):  # standard normal cdf
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def crps_normal(mean, sd, outcome):
    """Closed form CRPS for a normal predictive distribution."""
    if sd <= 0:
        return abs(outcome - mean)
    z = (outcome - mean) / sd
    return sd * (z * (2 * _Phi(z) - 1) + 2 * _phi(z) - 1 / SQRT_PI)


def rps(probs, outcome_index):
    """Ranked probability score for ordered bins. probs sums to 1."""
    total = 0.0
    cum_f = 0.0
    for i, p in enumerate(probs):
        cum_f += p
        cum_o = 1.0 if i >= outcome_index else 0.0
        total += (cum_f - cum_o) ** 2
    return total / (len(probs) - 1) if len(probs) > 1 else total


def skill(entrant_crps, persistence_crps):
    """Positive = better than persistence. Undefined if persistence is perfect."""
    if persistence_crps == 0:
        return 0.0
    return 1.0 - entrant_crps / persistence_crps


# --- distribution-level scoring -------------------------------------------
# A submission is either a normal (mean, sd) or a set of quantiles. Quantiles
# let an entrant express skew and fat tails that mean+sd cannot. Both are
# scored with (approximations of) the same CRPS, so formats compete fairly.

QUANT_LEVELS = [i / 40.0 for i in range(1, 40)]  # 0.025 .. 0.975


def pinball(level, q, outcome):
    """Pinball (quantile) loss for one quantile at one outcome."""
    if outcome >= q:
        return level * (outcome - q)
    return (1 - level) * (q - outcome)


def crps_from_quantiles(quantiles, outcome):
    """CRPS approximated from a quantile dict {level(str): value}.
    Average of 2x pinball loss over the provided levels (standard identity:
    CRPS = 2 * integral of pinball loss over levels)."""
    items = sorted((float(k), float(v)) for k, v in quantiles.items())
    if not items:
        raise ValueError("empty quantiles")
    return sum(2 * pinball(lv, q, outcome) for lv, q in items) / len(items)


def normal_quantile(mean, sd, level):
    """Inverse CDF of a normal via erfinv-free rational approximation
    (Acklam); adequate for scoring and sampling."""
    # Acklam's algorithm
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    p = min(max(level, 1e-9), 1 - 1e-9)
    if p < 0.02425:
        q = math.sqrt(-2 * math.log(p))
        z = (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
            ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    elif p > 1 - 0.02425:
        q = math.sqrt(-2 * math.log(1 - p))
        z = -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
            ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    else:
        q = p - 0.5
        r = q * q
        z = (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
            (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    return mean + sd * z


def crps_samples(samples, outcome):
    """Empirical CRPS from a deterministic sample set:
    mean|X - outcome| - 0.5 * mean|X - X'| (energy form)."""
    xs = sorted(samples)
    n = len(xs)
    if n == 0:
        raise ValueError("no samples")
    t1 = sum(abs(x - outcome) for x in xs) / n
    # pairwise mean|X-X'| in O(n) using the sorted order
    acc = 0.0
    run = 0.0
    for i, x in enumerate(xs):
        acc += x * i - run
        run += x
    t2 = 2 * acc / (n * n)
    return t1 - 0.5 * t2


def pool_samples(forecasts):
    """Deterministic equal-weight mixture of submitted forecasts: for each
    component take its quantiles at fixed levels, then pool. This is the
    'crowd' baseline (Metaculus-style community aggregate)."""
    xs = []
    for fc in forecasts:
        t = fc["topline"]
        if "quantiles" in t and t["quantiles"]:
            items = sorted((float(k), float(v)) for k, v in t["quantiles"].items())
            # interpolate onto the fixed levels for equal weighting
            for lv in QUANT_LEVELS:
                xs.append(_interp_quantile(items, lv))
        else:
            for lv in QUANT_LEVELS:
                xs.append(normal_quantile(t["mean"], t["sd"], lv))
    return xs


def _interp_quantile(items, level):
    if level <= items[0][0]:
        return items[0][1]
    if level >= items[-1][0]:
        return items[-1][1]
    for (l0, q0), (l1, q1) in zip(items, items[1:]):
        if l0 <= level <= l1:
            w = (level - l0) / (l1 - l0) if l1 > l0 else 0.0
            return q0 + w * (q1 - q0)
    return items[-1][1]


def crps_forecast(forecast_topline, outcome):
    """Score any accepted topline format."""
    t = forecast_topline
    if "quantiles" in t and t["quantiles"]:
        return crps_from_quantiles(t["quantiles"], outcome)
    return crps_normal(t["mean"], t["sd"], outcome)
