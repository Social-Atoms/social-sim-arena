"""Reference forecasts that run in every round.

- persistence: copy the last published value of the target series.
  Opinion is close to a random walk, so this is the headline null.
- trend: ordinary least squares over the recent history, extrapolated to the
  release date. sd inflated by the fit's residual error.

Both return the same shape a submitted forecast uses: {"mean": .., "sd": ..}.

A round may instead be ranked on a parameterised reference, `mean_k` or
`yoy_k` with its own measured spread (`reference`, the zero-point section
below); persistence stays the default and `persistence_crps` keeps its meaning.
The demographics-lookup baseline applies to crosstab questions and ships with
the crosstab rounds; the poll-average snapshot baseline is just
average.adjusted_average at lock time.
"""
from datetime import date, timedelta


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


# ---------------------------------------------------------------- the ranked zero point
#
# Which reference is a round's skill denominator. Every round scored so far is
# `persistence`, and a round without the key keeps it, so the season's
# existing scores cannot move (tests/test_zero_point.py).
#
# A round names its zero point either by a preset name (a string in PRESETS) or
# by a spec object, one member of a single parameterised family rather than a
# new name per data card (owner rule G1 of the urban cards):
#
#   {"name": "mean4", "family": "mean_k", "period": "week", "k": 4, "lag": 1,
#    "spread": {"rule": "rms", "n": 13, "search": 26}}
#
# Families:
#
# - `persistence`: the arena's default, `persistence(history)` with sd 1.5.
#   Takes no parameters; a persistence with a measured spread is
#   `mean_k` with k = 1.
# - `mean_k`: the mean of the k newest eligible values before the target,
#   after skipping `lag` newer periods that are not settled at lock. Up to
#   `search` periods are examined, so an ineligible one is skipped, not filled.
# - `yoy_k`: R x (last year's target), R = a ratio of sums over k settled
#   periods ending `lag` periods before the target / the same periods one
#   year earlier. Never a mean of ratios. k = 0 is plain same-as-last-year.
#   Options: `step_back` (shift a failing block back up to n periods), `end`
#   ("fixed", or "newest": the end moves back to the newest present period,
#   at most `max_lag`), `keep` ("length": the window slides with the end;
#   "start": its start is fixed and it shrinks), `target_len` (target = sum of
#   that many periods, e.g. a week on a daily axis), `base` (last year's target
#   from a named adapter function instead of the same period, e.g. a
#   day-type calendar; the adapter passes it in `bases`).
#
# Every family but persistence carries its own measured spread (owner rule
# G2, never the default sd 1.5): `rms` / `rms_relative` is the root mean
# square of the same rule's own errors (absolute, or relative x mean) over the
# n newest past targets complete in the same data, searching at most `search`
# targets back; fewer than n and the reference is NotPostable.
# `relative_fixed` is an interim sd = cv x mean, for a card that states one.
#
# Every number is computed from the series it is handed and nothing else, so
# the caller hands it data frozen at lock -- the same invariant as every other
# null here. The reference a round names must be present in its `baselines`;
# a missing one raises rather than falling back to persistence.

DEFAULT_ZERO_POINT = "persistence"

# The Seattle calls family's 4-week year-over-year reference (card
# seattle-community-calls@v4, ssa/adapters/seattle_cad.py): a fixed persistence
# sd of 1.5 on a count of ~4,750 would have been a point forecast, and
# submitting the reference itself with an honest spread would have scored +27%
# skill. It is one setting of `yoy_k`, byte-identical to the rule as it shipped
# (tests/test_zero_point.py).
PRESETS = {
    "persistence": {"name": "persistence", "family": "persistence"},
    "yoy4": {"name": "yoy4", "family": "yoy_k", "period": "week", "k": 4,
             "lag": 1, "step_back": 1,
             "spread": {"rule": "rms", "n": 13, "search": 26}},
}
ZERO_POINTS = tuple(PRESETS)

FAMILIES = ("persistence", "mean_k", "yoy_k")
_COMMON = {"name", "family", "period", "k", "lag", "digits", "target", "spread"}
_KEYS = {
    "persistence": {"name", "family"},
    "mean_k": _COMMON | {"search"},
    "yoy_k": _COMMON | {"step_back", "end", "max_lag", "keep", "target_len",
                        "base"},
}
_DEFAULTS = {
    "mean_k": {"lag": 0, "digits": 2},
    "yoy_k": {"lag": 0, "digits": 2, "step_back": 0, "end": "fixed",
              "keep": "length", "target_len": 1, "base": "same_period"},
}
SPREAD_RULES = ("rms", "rms_relative", "relative_fixed")
_SPREAD_KEYS = {"rms": {"rule", "n", "search", "end"},
                "rms_relative": {"rule", "n", "search", "end"},
                "relative_fixed": {"rule", "cv"}}
_NAME_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789_")


class NotPostable(ValueError):
    """The data cannot support the reference by its own rule: the round is
    not posted (or, at refresh, not scored), never given another null."""


def _int(spec, key, lo, rid):
    v = spec.get(key)
    if isinstance(v, bool) or not isinstance(v, int) or v < lo:
        raise ValueError(f"{rid}: zero_point `{key}` must be an integer >= {lo}, "
                         f"got {v!r}")
    return v


def normalize_spec(zp, rid="?"):
    """A zero point (preset name or spec object) -> its full spec, defaults
    filled. Closed: an unknown key, family, period or rule raises."""
    preset = isinstance(zp, str)
    if preset:
        if zp not in PRESETS:
            raise ValueError(f"{rid}: unknown zero_point {zp!r}; known: "
                             f"{', '.join(ZERO_POINTS)}, or a spec object")
        zp = PRESETS[zp]
    if not isinstance(zp, dict):
        raise ValueError(f"{rid}: zero_point must be a preset name or an object")
    fam = zp.get("family")
    if fam not in FAMILIES:
        raise ValueError(f"{rid}: unknown zero_point family {fam!r}; "
                         f"known: {', '.join(FAMILIES)}")
    extra = set(zp) - _KEYS[fam]
    if extra:
        raise ValueError(f"{rid}: zero_point {fam} does not take "
                         f"{', '.join(sorted(extra))}")
    name = zp.get("name")
    if not isinstance(name, str) or not name or set(name) - _NAME_CHARS \
            or not name[0].isalpha():
        raise ValueError(f"{rid}: zero_point `name` must be lower-case "
                         f"[a-z][a-z0-9_]*, got {name!r}")
    if fam == "persistence":
        if name != "persistence":
            raise ValueError(f"{rid}: the persistence family is named "
                             "'persistence'; a measured one is mean_k, k=1")
        return dict(zp)
    if name in ALL:
        raise ValueError(f"{rid}: zero_point name {name!r} is a default "
                         "baseline's; name the reference for its rule")
    spec = dict(_DEFAULTS[fam])
    spec.update(zp)
    if spec.get("period") not in AXES:
        raise ValueError(f"{rid}: zero_point `period` must be one of "
                         f"{', '.join(AXES)}, got {spec.get('period')!r}")
    _int(spec, "k", 1 if fam == "mean_k" else 0, rid)
    _int(spec, "lag", 0, rid)
    _int(spec, "digits", 0, rid)
    if fam == "mean_k":
        spec.setdefault("search", spec["k"])
        if _int(spec, "search", 1, rid) < spec["k"]:
            raise ValueError(f"{rid}: zero_point `search` must be >= k")
    else:
        _int(spec, "step_back", 0, rid)
        _int(spec, "target_len", 1, rid)
        if spec["end"] not in ("fixed", "newest"):
            raise ValueError(f"{rid}: zero_point `end` is 'fixed' or 'newest'")
        if spec["keep"] not in ("length", "start"):
            raise ValueError(f"{rid}: zero_point `keep` is 'length' or 'start'")
        if spec["end"] == "newest":
            _int(spec, "max_lag", spec["lag"], rid)      # an explicit bound
        elif "max_lag" in spec:
            raise ValueError(f"{rid}: `max_lag` only applies to end 'newest'")
        if not isinstance(spec["base"], str) or not spec["base"]:
            raise ValueError(f"{rid}: zero_point `base` must be a name")
        if spec["period"] == "index":
            raise ValueError(f"{rid}: yoy_k needs a calendar period, not 'index'")
    if "target" in spec and not isinstance(spec["target"], (str, int)):
        raise ValueError(f"{rid}: zero_point `target` must name a period")
    sp = spec.get("spread")
    if not isinstance(sp, dict) or sp.get("rule") not in SPREAD_RULES:
        raise ValueError(f"{rid}: zero_point needs a measured `spread` with rule "
                         f"one of {', '.join(SPREAD_RULES)} (never the default "
                         "sd 1.5)")
    extra = set(sp) - _SPREAD_KEYS[sp["rule"]]
    if extra:
        raise ValueError(f"{rid}: spread {sp['rule']} does not take "
                         f"{', '.join(sorted(extra))}")
    sp = dict(sp)
    if sp["rule"] == "relative_fixed":
        cv = sp.get("cv")
        if isinstance(cv, bool) or not isinstance(cv, (int, float)) or cv <= 0:
            raise ValueError(f"{rid}: spread `cv` must be > 0")
    else:
        _int(sp, "n", 1, rid)
        sp.setdefault("search", sp["n"])
        if _int(sp, "search", 1, rid) < sp["n"]:
            raise ValueError(f"{rid}: spread `search` must be >= n")
        sp.setdefault("end", "resolved")
        if sp["end"] not in ("resolved", "nominal"):
            raise ValueError(f"{rid}: spread `end` is 'resolved' or 'nominal'")
    spec["spread"] = sp
    if not preset and name in PRESETS and spec != normalize_spec(name):
        raise ValueError(f"{rid}: zero_point {name!r} is a preset; a different "
                         "rule needs a different name")
    return spec


def zero_point_spec(r):
    """The round's zero point as a full spec. A published row carries the
    name under `zero_point` and the rule under `zero_point_spec`."""
    rid = r.get("round_id")
    zp = r.get("zero_point", DEFAULT_ZERO_POINT)
    if isinstance(zp, str) and "zero_point_spec" in r:
        spec = normalize_spec(r["zero_point_spec"], rid)
        if spec["name"] != zp:
            raise ValueError(f"{rid}: zero_point {zp!r} and its spec "
                             f"{spec['name']!r} disagree")
        return spec
    return normalize_spec(zp, rid)


def zero_point_name(r):
    return zero_point_spec(r)["name"]


def zero_point(r):
    """The round's ranked reference forecast, {mean, sd, ...}."""
    name = zero_point_name(r)
    ref = (r.get("baselines") or {}).get(name)
    if not ref:
        raise KeyError(f"{r.get('round_id')}: zero_point {name!r} is not among "
                       "its baselines; refusing to score against another null")
    return ref


# ---------------------------------------------------------------- period axes

def _as_day(p):
    if isinstance(p, date):
        return date(p.year, p.month, p.day)
    return date.fromisoformat(str(p)[:10])


def _as_month(p):
    if isinstance(p, date):
        return f"{p.year:04d}-{p.month:02d}"
    s = str(p)
    if len(s) < 7 or s[4] != "-":
        raise ValueError(f"not a month: {p!r}")
    return f"{int(s[:4]):04d}-{int(s[5:7]):02d}"


def _month_shift(m, n):
    i = int(m[:4]) * 12 + int(m[5:7]) - 1 - n
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


class _Axis:
    def __init__(self, key, back, year_ago, fmt):
        self.key, self.back, self.year_ago, self.fmt = key, back, year_ago, fmt


def _no_year(p):
    raise ValueError("an index axis has no year-earlier period")


# `back(p, n)` is n periods before p. A year earlier is 364 days on a day or
# week axis -- the same weekday, so holidays on fixed weekdays line up and
# the ones on fixed dates move by a day -- and 12 months on a month axis.
AXES = {
    "day": _Axis(_as_day, lambda p, n: p - timedelta(days=n),
                 lambda p: p - timedelta(days=364), lambda p: p.isoformat()),
    "week": _Axis(_as_day, lambda p, n: p - timedelta(days=7 * n),
                  lambda p: p - timedelta(days=364), lambda p: p.isoformat()),
    "month": _Axis(_as_month, _month_shift, lambda p: _month_shift(p, 12),
                   lambda p: p),
    "index": _Axis(int, lambda p, n: p - n, _no_year, lambda p: p),
}


class _Series:
    """{period: value} on one axis, with an eligibility predicate."""

    def __init__(self, series, axis, valid=None):
        self.axis = AXES[axis] if isinstance(axis, str) else axis
        self.values = {}
        for p, v in series.items():
            k = self.axis.key(p)
            if k in self.values:
                raise ValueError(f"two values for period {self.axis.fmt(k)}")
            if v is not None:
                self.values[k] = v
        self.valid = valid

    def present(self, p):
        return p in self.values

    def ok(self, p):
        return p in self.values and (self.valid is None or self.valid(p))

    def total(self, periods):
        """Sum over the periods, or None if any is not eligible."""
        if not all(self.ok(p) for p in periods):
            return None
        return sum(self.values[p] for p in periods)


def _rounded(x, digits):
    return round(x, digits)


# ---------------------------------------------------------------- the families

def mean_k(s, target, k, lag=0, search=None, digits=2):
    """Mean of the k newest eligible values from `lag` + 1 periods before the
    target back, examining at most `search` periods (default k)."""
    ax = s.axis
    first = 1 + lag
    picked = []
    for off in range(first, first + (search or k)):
        p = ax.back(target, off)
        if s.ok(p):
            picked.append(p)
            if len(picked) == k:
                break
    if len(picked) < k:
        raise NotPostable(f"only {len(picked)} of {k} eligible values within "
                          f"{search or k} periods before {ax.fmt(target)}")
    newest = next(o for o in range(first, first + (search or k))
                  if ax.back(target, o) == picked[0])
    picked.reverse()                                  # oldest first
    total = sum(s.values[p] for p in picked)
    return {"mean": _rounded(total / k, digits),
            "window": [ax.fmt(p) for p in picked],
            "step_back": newest - first}


def target_block(ax, target, target_len):
    """The periods the target sums over, oldest first."""
    return [ax.back(target, -i) for i in range(target_len)]


def yoy_k(s, target, k, lag=0, step_back=0, end="fixed", max_lag=None,
          keep="length", target_len=1, base=None, digits=2):
    """R x last year's target; R a ratio of sums (see the module notes).

    Returns the point and its parts: ratio, numerator, denominator, the
    window, last year's periods and value, the step back and the end lag the
    rule resolved to (`lag_used`, `k_used`), which the spread re-uses.
    """
    ax = s.axis
    if base is None:
        aligned = [ax.year_ago(p) for p in target_block(ax, target, target_len)]
        ly = s.total(aligned)
        if ly is None:
            raise NotPostable(f"last year's aligned period "
                              f"{ax.fmt(aligned[0])} is not eligible")
        ly_periods = [ax.fmt(p) for p in aligned]
    else:
        ly, ly_periods = base(target)
    if end == "newest":
        top = 1 + (max_lag if max_lag is not None else lag)
        e = next((o for o in range(1 + lag, top + 1)
                  if s.present(ax.back(target, o))), None)
        if e is None:
            raise NotPostable(f"nothing posted between {1 + lag} and {top} "
                              f"periods before {ax.fmt(target)}")
    else:
        e = 1 + lag
    length = k if keep == "length" else k - (e - 1 - lag)
    if k and length < 1:
        raise NotPostable(f"the window before {ax.fmt(target)} is empty")
    if k == 0:
        return {"mean": _rounded(ly, digits), "ratio": 1.0, "numerator": None,
                "denominator": None, "window": [], "year_ago": ly_periods,
                "year_ago_value": ly, "step_back": 0, "lag_used": e - 1,
                "k_used": 0}
    for step in range(step_back + 1):
        win = [ax.back(target, o) for o in range(e + step + length - 1,
                                                  e + step - 1, -1)]
        earlier = [ax.year_ago(p) for p in win]
        num, den = s.total(win), s.total(earlier)
        if num is None or den is None:
            continue
        if den <= 0:
            raise NotPostable("year-earlier reference periods sum to zero")
        ratio = num / den
        return {"mean": _rounded(ratio * ly, digits), "ratio": round(ratio, 6),
                "numerator": num, "denominator": den,
                "window": [ax.fmt(p) for p in win], "year_ago": ly_periods,
                "year_ago_value": ly, "step_back": step, "lag_used": e - 1,
                "k_used": length}
    raise NotPostable(f"reference periods for {ax.fmt(target)} are not "
                      f"eligible even after {step_back} step(s) back")


def spread_errors(targets, actual, reference, n):
    """(errors, targets used), newest first: actual - reference mean over the
    first n targets whose actual is known and whose reference is postable.
    `targets` is newest first and already bounded by the search depth."""
    errors, used = [], []
    for t in targets:
        a = actual(t)
        if a is None:
            continue
        try:
            ref = reference(t)
        except NotPostable:
            continue
        errors.append((a, ref["mean"]))
        used.append(t)
        if len(errors) == n:
            return errors, used
    raise NotPostable(f"only {len(errors)} of {n} past targets qualify for "
                      "the spread; the round is not posted")


def rms(errors):
    return (sum(e * e for e in errors) / len(errors)) ** 0.5


def _point(spec, s, target, bases):
    fam = spec["family"]
    if fam == "mean_k":
        return mean_k(s, target, spec["k"], spec["lag"], spec["search"],
                      spec["digits"])
    base = None
    if spec["base"] != "same_period":
        fn = (bases or {}).get(spec["base"])
        if fn is None:
            raise ValueError(f"zero_point base {spec['base']!r} needs the "
                             "adapter's function; none was passed")
        base = fn
    return yoy_k(s, target, spec["k"], spec["lag"], spec["step_back"],
                 spec["end"], spec.get("max_lag"), spec["keep"],
                 spec["target_len"], base, spec["digits"])


def first_past_target(spec):
    """Offset, in targets, of the newest past target the spread may use: the
    newest whose every period is at least lag + 1 periods before the target."""
    L = spec.get("target_len", 1)
    return -(-(spec["lag"] + L) // L)


def reference(spec, series, target, valid=None, bases=None):
    """The zero point `spec` for `target` from `series` ({period: value}),
    with its measured spread. `valid(period)` marks a present value as
    ineligible (an outage); `bases` maps a spec's `base` name to
    fn(target) -> (value, [periods]).

    Returns {method: name, family, mean, sd, spread, spread_targets, ...the
    family's parts}. Raises NotPostable when the rule cannot be computed.
    """
    spec = normalize_spec(spec)
    if spec["family"] == "persistence":
        raise ValueError("persistence is computed by baselines.persistence")
    s = _Series(series, spec["period"], valid)
    ax = s.axis
    target = ax.key(target)
    point = _point(spec, s, target, bases)
    out = {"method": spec["name"], "family": spec["family"]}
    out.update(point)
    sp = spec["spread"]
    if sp["rule"] == "relative_fixed":
        out["sd"] = max(_rounded(sp["cv"] * abs(point["mean"]), spec["digits"]),
                        10 ** -spec["digits"])
        out["spread"] = f"relative_fixed_{sp['cv']}"
        return out
    past = dict(spec)
    if spec["family"] == "yoy_k":
        past["end"] = "fixed"
        past.pop("max_lag", None)
        if sp["end"] == "resolved":
            past["lag"], past["k"] = point["lag_used"], point["k_used"]
            past["keep"] = "length"
    L = spec.get("target_len", 1)
    # Past targets start at the newest one complete in the same data: with a
    # resolved end, the end this round's rule actually reached.
    first = first_past_target(past if spec["family"] == "yoy_k" else spec)
    targets = [ax.back(target, L * j) for j in range(first, first + sp["search"])]
    pairs, used = spread_errors(
        targets,
        lambda t: s.total(target_block(ax, t, L)),
        lambda t: _point(past, s, t, bases), sp["n"])
    if sp["rule"] == "rms":
        sd = rms([a - m for a, m in pairs])
    else:
        if any(m <= 0 for _, m in pairs):
            raise NotPostable("a relative spread needs positive past references")
        sd = abs(point["mean"]) * rms([(a - m) / m for a, m in pairs])
    # Floor after rounding: CRPS needs sd > 0, and a perfectly flat history
    # (only ever a fixture) must not round its floor away.
    out["sd"] = max(_rounded(sd, spec["digits"]), 10 ** -spec["digits"])
    out["spread"] = f"{sp['rule']}_{sp['n']}"
    out["spread_targets"] = [ax.fmt(t) for t in used]
    return out


def reference_from_history(spec, history, target=None):
    """The zero point from a refresh history [{date, value}] frozen at lock.
    Dates must name the period observed (a month's first day, a week's
    first day), not the release. `target` defaults to the spec's."""
    spec = normalize_spec(spec)
    target = spec.get("target") if target is None else target
    if target is None:
        raise ValueError(f"zero_point {spec['name']!r} names no target period")
    ax = AXES[spec["period"]]
    series = {}
    for p in history:
        k = ax.key(p["date"])
        if k in series:
            raise ValueError(f"history has two points for period {ax.fmt(k)}")
        series[k] = p["value"]
    return reference(spec, series, target)


def _ordinal(d):
    if isinstance(d, str):
        d = date.fromisoformat(d[:10])
    return d.toordinal()
