"""A synthetic respondent panel, and the arithmetic that turns it into a poll.

This is the machinery behind the `persona` condition: instead of asking a model
to forecast a number, ask it to *be* twenty-four people in turn, put the actual
survey question to each of them, and then aggregate their answers the way the
pollster aggregates real ones. That is the method the silicon-sampling
literature describes and the method the industry sells, so it is the one worth
putting on a scoreboard next to a moving average.

**Why the panel is a literal table.** Every number below is a published
marginal, written out rather than sampled, because the repository forbids
randomness -- a panel drawn from a distribution would give a different answer on
every run and nothing here would be reproducible. Twenty-four cells is the full
cross of the three axes that move opinion most, so the panel is a quota sample,
not a random one.

**The approximation, stated plainly.** Cell weights are the product of three
independent marginals. Party, age and education are *not* independent in the
United States -- college graduates lean Democratic, older voters lean
Republican -- so a cell like "Republican, 18-29, BA+" carries more weight here
than it does in the population. Correcting it needs a joint distribution from
microdata (CPS or ANES), which is a defensible next step and is not this. The
error is bounded and one-directional: it flattens the party-education
correlation, which makes the simulated electorate slightly more moderate than
the real one. Anything published from this arm has to say so.

**Registered voters are a reweight, not a filter.** Two of the trackers report
registered voters and the rest report adults, and the difference is worth
several points. Dropping cells would shrink an already small panel, so each
cell carries a registration rate and the RV population is the same twenty-four
people under different weights.
"""

# Marginals for the US adult population, rounded to whole percents.
#
#   party      Gallup party identification including leaners, 2025-2026 range
#   age        Census ACS adult age distribution
#   education  Census: share of adults 25+ holding a bachelor's degree or above
#
# Each is a marginal; see the module docstring for what multiplying them costs.
PARTY = {"Democrat": 0.33, "Republican": 0.32, "independent": 0.35}
AGE = {"18-29": 0.20, "30-49": 0.33, "50-64": 0.25, "65+": 0.22}
EDUCATION = {"no college degree": 0.62, "college graduate": 0.38}

# Share of each age x education cell registered to vote. Registration rises
# with age and with education, and both effects are large enough that ignoring
# them would misstate every registered-voter tracker in one direction.
REGISTRATION = {
    ("18-29", "no college degree"): 0.52,
    ("18-29", "college graduate"): 0.68,
    ("30-49", "no college degree"): 0.63,
    ("30-49", "college graduate"): 0.78,
    ("50-64", "no college degree"): 0.71,
    ("50-64", "college graduate"): 0.85,
    ("65+", "no college degree"): 0.76,
    ("65+", "college graduate"): 0.88,
}

# Attributes that round out a persona without being weighting axes. They are
# assigned by position rather than crossed, which keeps the panel at 24 people:
# they add texture to the character being played, and they are not claimed to
# be representative on their own.
_REGIONS = ("the Northeast", "the Midwest", "the South", "the West")
_GENDERS = ("a man", "a woman")
_INCOMES = ("under $40,000", "$40,000-$99,000", "$100,000 or more")


CELLS = len(PARTY) * len(AGE) * len(EDUCATION)

# Respondents per cell. This is the accuracy dial, and it is not free -- one
# call per respondent per round, so the panel size *is* the cost of the
# condition. See `resolution()` for what any setting buys; the short version is
# that one respondent per cell cannot resolve a tracker that moves a point
# between releases, because collapsing a whole cell to a single yes/no is
# itself worth about ten points of error.
REPLICATES = int(__import__("os").environ.get("SSA_PERSONA_REPLICATES", "8"))


def panel(replicates=None):
    """The panel, in a fixed order, each persona with its population weight.

    `CELLS` quota cells, each filled by `replicates` distinct respondents who
    share the three weighting attributes and differ in the rest. Replicates
    exist because one person per cell is not a poll: the cell's real answer is
    a share, and a single respondent can only say all or nothing, which is
    where most of the error below comes from.

    Deterministic in construction and in order -- the same panel every run, so
    a persona forecast is reproducible from the committed replies.
    """
    k = REPLICATES if replicates is None else replicates
    if k < 1:
        raise ValueError("a panel needs at least one respondent per cell")
    out = []
    cell = 0
    for party, pw in PARTY.items():
        for age, aw in AGE.items():
            for edu, ew in EDUCATION.items():
                for rep in range(k):
                    i = cell * k + rep
                    out.append({
                        "id": f"p{i:03d}",
                        "cell": f"c{cell:02d}",
                        "party": party,
                        "age": age,
                        "education": edu,
                        # Varied across replicates so a cell is several people
                        # rather than one person asked repeatedly.
                        "gender": _GENDERS[i % len(_GENDERS)],
                        "region": _REGIONS[i % len(_REGIONS)],
                        "income": _INCOMES[i % len(_INCOMES)],
                        "weight": round(pw * aw * ew / k, 8),
                        "registered": REGISTRATION[(age, edu)],
                    })
                cell += 1
    return out


def weights_for(population, replicates=None):
    """{persona id: weight} for the population a tracker reports.

    "A" is adults and uses the cell weight as is. "RV" and "LV" multiply it by
    the cell's registration rate and renormalise, so the panel is reweighted
    rather than thinned -- the panel is already small enough. Weights always
    sum to 1, so an aggregate is a share of the stated population and not of
    whoever happened to answer.
    """
    people = panel(replicates)
    if (population or "A").upper() == "A":
        raw = {p["id"]: p["weight"] for p in people}
    else:
        raw = {p["id"]: p["weight"] * p["registered"] for p in people}
    total = sum(raw.values())
    if total <= 0:
        raise ValueError(f"population {population!r} left the panel empty")
    return {k: v / total for k, v in raw.items()}


def describe(p):
    """The persona as the model is told to inhabit it.

    Second person and present tense, with no mention of forecasting, polls or
    aggregates: this respondent is being interviewed, not consulted. A persona
    that knows it is feeding a prediction market answers as an analyst, which
    is the very thing this condition exists to be compared against.
    """
    return (f"You are {p['gender']} living in {p['region']}, aged {p['age']}, "
            f"{p['education']}, with a household income of {p['income']}. "
            f"Politically you identify as {'an' if p['party'][0] in 'aeiou' else 'a'} "
            f"{p['party']}.")


# --- turning answers into the number a pollster would publish ---------------

def _tally(answers, weights, key):
    """{option: weight} over the personas that answered, plus the total.

    Personas whose reply failed to parse are absent from `answers` and are
    excluded from both, so a partial panel yields a share of who answered
    rather than counting a missing person as a "no".
    """
    got, total = {}, 0.0
    for pid, ans in answers.items():
        w = weights.get(pid)
        if w is None:
            continue
        val = (ans or {}).get(key)
        if val is None:
            continue
        got[val] = got.get(val, 0.0) + w
        total += w
    return got, total


def approve_share(answers, weights, item="approval", positive="approve"):
    """Percent approving, as a share of everyone asked.

    Of everyone, not of approve+disapprove: in the published data the two sum
    to about 96-97, so there is a real "not sure" residual and dividing it away
    would inflate every simulated number by three or four points.
    """
    got, total = _tally(answers, weights, item)
    if total <= 0:
        raise ValueError("no usable answers to aggregate")
    return 100.0 * got.get(positive, 0.0) / total


def party_margin(answers, weights, item="vote", left="Democrat", right="Republican"):
    """Democratic minus Republican, in points, over everyone asked."""
    got, total = _tally(answers, weights, item)
    if total <= 0:
        raise ValueError("no usable answers to aggregate")
    return 100.0 * (got.get(left, 0.0) - got.get(right, 0.0)) / total


# The Index of Consumer Sentiment is not a share -- it is a published formula
# over five specific items. Each item becomes a relative score (percent
# favourable minus percent unfavourable, plus 100); the five are summed,
# divided by the 1966 base value, and offset. Reproducing the formula is the
# only way a simulated ICS is on the same scale as the real one; a model asked
# for "the index" directly would be guessing at a normalisation it cannot see.
ICS_ITEMS = ("pago", "pexp", "bus12", "bus5", "dur")
ICS_BASE = 6.7558          # 1966 base period
ICS_OFFSET = 2.0           # correction for sample design changes


# The five items keep the survey's own vocabulary rather than a single
# normalised one, because the respondent is answering the real question:
# personal finances are "better/worse", business conditions are "good/bad". The
# index treats them identically, so they are folded together only here.
_FAVOURABLE = ("better", "good")
_UNFAVOURABLE = ("worse", "bad")


def umich_ics(answers, weights):
    """The Index of Consumer Sentiment from five item responses per persona."""
    total_score = 0.0
    for item in ICS_ITEMS:
        got, total = _tally(answers, weights, item)
        if total <= 0:
            raise ValueError(f"no usable answers for ICS item {item!r}")
        share = lambda opts: 100.0 * sum(
            got.get(o, 0.0) for o in opts) / total          # noqa: E731
        total_score += (share(_FAVOURABLE) - share(_UNFAVOURABLE)) + 100.0
    return total_score / ICS_BASE + ICS_OFFSET


# The subgroup trackers ask the same question on a four-point scale plus "not
# sure", because that is the only way to report "strongly" and "somewhat"
# separately -- and those two are themselves published series. So the scale has
# three readings, and they are consistent by construction: strong + weak is
# exactly the headline approval number. A model that forecasts all three and
# breaks that identity has been caught.
def _share_of(options):
    def agg(answers, weights, item="approval"):
        got, total = _tally(answers, weights, item)
        if total <= 0:
            raise ValueError("no usable answers to aggregate")
        return 100.0 * sum(got.get(o, 0.0) for o in options) / total
    return agg


approve_share_4pt = _share_of(("strongly approve", "somewhat approve"))
strong_share = _share_of(("strongly approve",))
weak_share = _share_of(("somewhat approve",))

AGGREGATORS = {
    "approve_share": approve_share,
    "approve_share_4pt": approve_share_4pt,
    "strong_share": strong_share,
    "weak_share": weak_share,
    "party_margin": party_margin,
    "umich_ics": umich_ics,
}


def aggregate(kind, answers, weights):
    fn = AGGREGATORS.get(kind)
    if fn is None:
        raise ValueError(f"unknown aggregator {kind!r}; "
                         f"known: {sorted(AGGREGATORS)}")
    return fn(answers, weights)


# --- how uncertain the panel should say it is -------------------------------

# Three things separate this estimate from the number that gets published, and
# only the first has a closed form:
#
#   1. Discretisation. Every respondent answers all-or-nothing while the group
#      they stand for answers a share, so collapsing them costs real accuracy.
#      This is not "sampling error" in the usual sense -- the panel is a fixed
#      quota sample and reruns nothing -- but the arithmetic is identical, and
#      it is the term that makes a small panel useless. It is computed below.
#   2. The real survey's own sampling error, one to three points.
#   3. The gap between simulated and actual opinion, which is the entire open
#      question this arena exists to measure and is very likely the largest of
#      the three.
#
# (2) and (3) have no closed form here, so the series' own release-to-release
# volatility stands in for both. It comes from the pre-lock history the
# baselines already read, so it leaks nothing about the outcome.
#
# The honest consequence, worth stating before anyone reads a persona score:
# this sd is a harness choice, not the model's. A persona entrant's CRPS is
# therefore partly a property of our calibration, unlike every other entrant,
# which reports its own uncertainty. Once a season of persona residuals exists
# the right move is to calibrate on those instead.
MIN_SD = 0.5


def panel_se(weights, p=0.5):
    """Discretisation error of a weighted share from this panel, in points.

    Kish's effective sample size, so unequal weights cost what they actually
    cost: respondents weighted unevenly carry less information than the same
    number weighted equally.

    p=0.5 is the worst case. Real cells are more homogeneous than a coin --
    a Republican cell approves at something like 0.9 -- so this overstates the
    error somewhat, which is the direction to err in.
    """
    ws = list(weights.values())
    if not ws:
        return float("inf")
    n_eff = sum(ws) ** 2 / sum(w * w for w in ws)
    return 100.0 * (p * (1 - p) / n_eff) ** 0.5


def resolution(replicates_options=(1, 2, 4, 8, 16, 32), population="A"):
    """[(replicates, panel size, discretisation error in points)].

    The table to look at before choosing a panel size, because the cost is
    linear in it and the accuracy is not: error falls as one over the square
    root, so buying a point of resolution costs four times as much every time.
    A tracker that moves a point or two between releases needs the bottom of
    this table to say anything a moving average has not already said.
    """
    out = []
    for k in replicates_options:
        w = weights_for(population, replicates=k)
        out.append((k, CELLS * k, round(panel_se(w), 2)))
    return out


def step_sd(history, n=24):
    """How much this series usually moves between releases, in its own unit."""
    pts = [p["value"] for p in (history or [])][-(n + 1):]
    steps = [b - a for a, b in zip(pts, pts[1:])]
    if len(steps) < 2:
        return None
    mean = sum(steps) / len(steps)
    var = sum((s - mean) ** 2 for s in steps) / (len(steps) - 1)
    return var ** 0.5


def sd_for(weights, history, scale=1.0):
    """The sd a persona forecast should report.

    Discretisation error and the series' own volatility, added in quadrature
    because they are independent, then floored so a degenerate history can
    never produce a confident forecast. `scale` converts a share-based error
    into the unit of a derived index; it is 1 for a percentage.

    This will be wide, and it should be. A panel small enough to be affordable
    genuinely does not know the next release to within a point, and reporting
    otherwise would be a calibration failure that CRPS is built to catch.
    """
    se = panel_se(weights) * scale
    vol = step_sd(history)
    if vol is None:
        # Nothing to calibrate against -- widen rather than sound certain.
        return max(round(se * 2.0, 3), MIN_SD)
    return max(round((se ** 2 + vol ** 2) ** 0.5, 3), MIN_SD)
