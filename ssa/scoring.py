"""Scoring rules. All strictly proper (Gneiting and Raftery 2007).

- CRPS for continuous toplines, submitted as a normal (mean, sd).
  Plain words: absolute error, generalized to distributions. Confident and
  right scores best, confident and wrong scores worst. Lower is better.
- RPS for ordered categorical bins.
- Skill: 1 - CRPS(entrant) / CRPS(persistence). Positive means better than
  copying the last release, the same move weather forecasting makes against
  its persistence and climatology nulls.
"""
import hashlib
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


# --- Profile scoring: a wave's whole subgroup structure, scored jointly ------
#
# A crosstab round asks for one number per subgroup, so the object being scored
# is a vector rather than a scalar. Averaging a per-cell CRPS would score the
# marginals and ignore the joint: two entrants with identical per-cell
# uncertainty, one believing the subgroups move together and one believing they
# move independently, would be indistinguishable. That difference is precisely
# what having a model of a society means, so the arena scores the vector with
# the energy score, the multivariate generalization of CRPS (Gneiting and
# Raftery 2007, section 4.3). In one dimension the two coincide.
#
# Measured on 80 waves of the Economist/YouGov tracker, the two constructions
# above differ by 7% of energy score while their mean per-cell CRPS differs by
# 1%, which is the whole argument for scoring jointly.

PROFILE_SEED = 20260811       # season 0 opens; frozen in the pre-registration
PROFILE_DRAWS = 400           # energy score is O(draws^2 * cells)


def _dist(a, b):
    return math.sqrt(sum((x - y) * (x - y) for x, y in zip(a, b)))


def energy_score(samples, outcome):
    """Multivariate CRPS: mean||X - y|| - 0.5 * mean||X - X'||.

    Strictly proper for the joint distribution (Gneiting and Raftery 2007,
    section 4.3). Lower is better. `samples` is a list of equal-length vectors
    standing in for the predictive distribution, `outcome` the observed vector.
    In one dimension this is exactly `crps_samples`, which is what keeps a
    profile round and a topline round on one scale.

    **How the estimator is built.** The two expectations are replaced by
    averages over a fixed, deterministically constructed point set -- see
    `profile_samples` and `cell_samples` for how a submission becomes one.
    Nothing here draws a random number, so a third party re-running the scorer
    on the published forecast files reproduces the leaderboard byte for byte.
    That is the point: the arena's reproducibility claim would be worthless if
    the score depended on an RNG, and merely seeding one would make it depend
    on a particular interpreter's generator instead.

    **What it does not do.** Three limitations, all deliberate and all worth
    stating because a reader will otherwise assume them away:

    - The second term uses `2 * acc / (n * n)` over unordered pairs, i.e. the
      biased (V-statistic) form rather than the n(n-1) U-statistic. It is the
      same convention `crps_samples` already uses, so the two agree exactly in
      one dimension; the bias is O(1/n) and identical for every entrant in a
      round, so it cannot reorder a leaderboard.
    - The estimate is only as good as the point set. With `PROFILE_DRAWS`
      points the Monte-Carlo error is well under the differences the arena
      reports, but two forecasts closer than that are not distinguishable and
      should not be reported as if they were.
    - It scores the joint distribution the submission *implies*. A submission
      of per-cell marginals implies independence (see `cell_samples`), which is
      a real claim about the population and is scored as one.
    """
    n = len(samples)
    if n == 0:
        raise ValueError("no samples")
    t1 = sum(_dist(x, outcome) for x in samples) / n
    acc = 0.0
    for i in range(n):
        xi = samples[i]
        for j in range(i + 1, n):
            acc += _dist(xi, samples[j])
    t2 = 2 * acc / (n * n)
    return t1 - 0.5 * t2


_PERMS = {}


def _perm(dim, draws, seed):
    """The fixed permutation of range(draws) used by dimension `dim`.

    The order is the one induced by sorting the indices on
    sha256("seed:dim:index"). That is a pure function of three integers: no
    generator, no state, no dependence on the interpreter's `random` module --
    which is what this replaced, and which tied a headline reproducibility
    claim to the Mersenne Twister's implementation details.

    A hash order behaves like an arbitrary permutation, which is exactly what
    is wanted: the dimensions must not line up. Residual linear correlation
    between two dimensions is O(draws ** -0.5) -- about 0.13 in the worst of
    the 120 pairs at 16 cells and 400 draws, the same magnitude the seeded
    shuffle produced. `tests/test_profile_scoring.py` pins that bound, because
    a construction that quietly coupled two cells would fake exactly the joint
    structure these rounds exist to measure.

    Cached: the permutation depends on nothing about the forecast, and every
    submission in every round rebuilds the same sixteen.
    """
    key = (dim, draws, seed)
    got = _PERMS.get(key)
    if got is None:
        got = _PERMS[key] = sorted(
            range(draws),
            key=lambda i: hashlib.sha256(f"{seed}:{dim}:{i}".encode()).digest())
    return got


def _column(inv_cdf, dim, seed, draws):
    """One cell's coordinates: the stratified grid, permuted, pushed through
    the cell's own inverse CDF."""
    perm = _perm(dim, draws, seed)
    return [inv_cdf((i + 0.5) / draws) for i in perm]


def cell_samples(cells, seed=PROFILE_SEED, draws=PROFILE_DRAWS):
    """A profile submission -> a deterministic joint sample set.

    `cells` is a list of per-cell forecasts in the round's fixed cell order,
    each in one of the two formats a scalar topline may take: `{mean, sd}` or a
    `quantiles` map. Both are turned into the same object -- a column of
    `draws` values -- so the two formats compete on one scale here for the same
    reason `crps_forecast` makes them compete on one scale for a topline.

    **The construction is a Latin hypercube on fixed quantile levels.** Every
    cell walks the same stratified grid `(i + 0.5) / draws`, so each cell's
    marginal is reproduced exactly rather than sampled; the grid is then
    permuted per cell by `_perm`, so the cells are not stapled together in rank
    order. This is the same move `pool_samples` makes for the crowd, one
    dimension at a time.

    **The dependence assumption is independence, and it is the submission's
    own.** A forecast that gives sixteen marginals and nothing else has said
    nothing about how the subgroups move together, and the only honest reading
    of that is that they move independently. The permutation implements that
    reading. An entrant who believes the cells move as one can express it --
    but only by widening the cells whose joint behaviour it is confident about,
    since the submission format carries no correlation term. That is a real
    limit of this round type and the place to extend it first.
    """
    cols = []
    for dim, c in enumerate(cells):
        cols.append(_column(_inv_cdf(c), dim, seed, draws))
    if not cols:
        raise ValueError("no cells")
    return [list(row) for row in zip(*cols)]


def _inv_cdf(cell):
    """A cell forecast -> its quantile function. Rejects a point forecast."""
    q = cell.get("quantiles") if isinstance(cell, dict) else None
    if q:
        items = sorted((float(k), float(v)) for k, v in q.items())
        return lambda lv: _interp_quantile(items, lv)
    mean, sd = float(cell["mean"]), float(cell["sd"])
    if not sd > 0:
        raise ValueError(f"cell sd must be > 0, got {sd}")
    return lambda lv: normal_quantile(mean, sd, lv)


def profile_samples(means, sds, seed=PROFILE_SEED, draws=PROFILE_DRAWS):
    """Independent normal marginals -> a deterministic joint sample set.

    The `{mean, sd}`-only form of `cell_samples`, kept because the crosstab
    path and its tests are written against it.
    """
    return cell_samples([{"mean": m, "sd": s} for m, s in zip(means, sds)],
                        seed=seed, draws=draws)


def profile_skill(entrant_energy, persistence_energy):
    """1 - ES(entrant) / ES(persistence), the scalar skill convention verbatim.

    Named rather than left as a call to `skill`, because the profile
    leaderboard has to be readable next to the scalar one: "0.1 of skill" must
    mean the same thing on both boards -- a tenth of the null's loss removed --
    and the only way to guarantee that is for both to be this one expression.
    Positive means better than per-cell persistence, i.e. better than expecting
    every subgroup to sit where it sat at the last release.
    """
    return skill(entrant_energy, persistence_energy)


def profile_scores(samples, outcome):
    """Energy score plus the decomposition RQ3 turns on.

    `level` is the national mean across cells, scored on its own. `structure`
    is what is left after removing it: the shape of the population. A model
    that reads the national mood off a headline but has no idea how approval
    decomposes scores well on level and badly on structure, and the point of
    the crosstab rounds is to make that visible rather than average it away.
    """
    k = len(outcome)
    level_samples = [sum(x) / k for x in samples]
    level_outcome = sum(outcome) / k
    centered = [[v - sum(x) / k for v in x] for x in samples]
    centered_outcome = [v - level_outcome for v in outcome]
    per_cell = [crps_samples([x[i] for x in samples], outcome[i])
                for i in range(k)]
    return {
        "energy": energy_score(samples, outcome),
        "level": crps_samples(level_samples, level_outcome),
        "structure": energy_score(centered, centered_outcome),
        "per_cell": per_cell,
        "mean_cell_crps": sum(per_cell) / k,
    }


def noise_floor(series):
    """Measurement noise of a published series, in points, from its own history.

    For a level that moves plus independent measurement noise,
    var(diff) = s^2 + 2m^2 and cov(diff_t, diff_t+1) = -m^2, so the lag-1
    autocovariance of first differences identifies the noise m without any
    assumption about sample design. Measuring beats computing here: panel
    overlap and weighting to fixed targets make the published wobble smaller
    than the base size implies, 0.78 points against 1.44 on the YouGov
    topline, so the textbook standard error would overstate the floor and
    understate what an entrant could have achieved.

    Returns (m, s): measurement noise and real movement, per release, in the
    units of the series. s is zero when the estimate hits the boundary, which
    happens when a cell's movement is entirely noise over the window measured.
    """
    d = [series[i + 1] - series[i] for i in range(len(series) - 1)]
    n = len(d)
    if n < 3:
        raise ValueError("need at least four observations")
    mean = sum(d) / n
    var = sum((x - mean) ** 2 for x in d) / n
    cov1 = sum((d[i] - mean) * (d[i + 1] - mean) for i in range(n - 1)) / n
    # -cov1 can exceed var/2, which no level-plus-noise process can produce
    # (its first differences bottom out at autocorrelation -0.5). It happens
    # when the level itself mean-reverts, and left uncapped it inflates the
    # noise estimate until the ceiling drops below what a smoother already
    # achieves and scores run past 100. Cap at the edge of the admissible
    # space, where all movement is noise and none is signal.
    m2 = min(max(-cov1, 0.0), var / 2.0)
    s2 = max(var - 2 * m2, 0.0)
    return math.sqrt(m2), math.sqrt(s2)


def arena_score(entrant_crps, persistence_crps, noise):
    """0 to 100: how much of the attainable accuracy an entrant captured.

    0 is copying the last release. 100 is knowing what the public actually
    thinks: the only error left is the survey's own sampling noise, which no
    forecaster can predict because the respondents have not been interviewed
    yet when the round locks. A perfect probabilistic forecast centered on the
    true level with the right spread has expected CRPS of noise / sqrt(pi),
    which is where the top of the scale sits.

    Skill against a persistence null is the same quantity on a scale whose top
    depends on the target: 20 points of skill is near-perfect on one series and
    mediocre on another, because they carry different amounts of noise. This
    puts every target on one scale, so a leaderboard can be read across them.

    Season-level only. Averaged over one round the denominator is a single
    CRPS difference, which is dominated by that round's sampling noise; on the
    first live wave it produced scores of 2430 and -792 on adjacent cells.
    Aggregate CRPS over rounds first, then put the aggregates through this.
    Single rounds publish raw CRPS and energy scores, never this scale.
    """
    ceiling = persistence_crps - noise / SQRT_PI
    if ceiling <= 0:
        return float("nan")     # target too noisy to be scored on this scale
    return 100.0 * (persistence_crps - entrant_crps) / ceiling
