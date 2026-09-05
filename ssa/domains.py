"""What a round is *about*, as one published word per round.

The season already carries two ways to sort a question, and neither answers
the one a visitor asks first. `target_type` says what *shape* the answer takes
-- a number, a population, an order. `tracker` says which *organisation*
published the figure. A reader looking at the board wants neither: they want
to know whether the arena asks about elections, or about how people say the
economy feels, or about what the country looked up last week.

So this is a third axis, and it is deliberately small. Four domains, each one
a claim about the world that a forecaster would prepare for differently:

  elections        a vote, a margin, a seat count -- settled by a ballot
  public-opinion   approval and issue attitudes, national and by cut
  consumer         how households and investors say the economy feels
  attention        what people looked up, as an order rather than a level

**Keyed on the series, not the tracker.** One publisher asks about several
things: Civiqs runs both the presidential approval tracker and four questions
about the economy, and filing all fifty-two Civiqs rounds under one heading
would say the arena asks one question fifty-two times. The Economist/YouGov
wave carries both an approval number and a generic-ballot margin, and those
belong on opposite sides of the elections line.

**An unknown series is named, not guessed at.** `domain_of` raises by default,
because a silent fallback would file a new round under whichever heading the
default happens to name and tell nobody. But `refresh` asks with
`strict=False` and gets `UNPLACED`, which the site renders as its own heading.
The reason is the one `participants.py` gives for leaving an unkeyed entrant
off the roster: the six-hourly run must not die over a taxonomy gap, and a red
cron every cycle teaches everyone to ignore the colour.

That is safe because the gap is caught earlier. `tests/test_domains.py` walks
the series registry and the frozen season, so an unassigned series fails in CI
on the commit that adds it -- long before a round using it is scheduled.
"""

# id, label, and the one sentence the site prints under it. Ordered as the
# board displays them: the two that carry the most rounds first.
DOMAINS = (
    ("public-opinion", "Public opinion",
     "Approval and issue attitudes, nationally and by demographic cut."),
    ("consumer", "Consumer sentiment",
     "How households and investors say the economy feels."),
    ("attention", "Attention",
     "What the country looked up, asked as an order rather than a level."),
    ("elections", "Elections",
     "Margins and seat counts: the questions a ballot settles."),
)

# Not a fifth domain: a marker that something reached the site the taxonomy
# has never seen. It carries no blurb and is not in `DOMAINS`, so a board that
# iterates the headings will not invent one for it -- but a board that groups
# on the published value will show it, by name, rather than dropping the round.
UNPLACED = "unplaced"

LABELS = {d: label for d, label, _ in DOMAINS}
IDS = tuple(d for d, _, _ in DOMAINS)

# Exact series ids. Spelled out rather than pattern-matched: `civiqs_net_*`
# would sweep the four economic questions in with the approval cuts, and
# `*_generic_*` would depend on a naming habit rather than on a decision
# somebody made. A rule you cannot read off the page is a rule that will be
# broken by the next series that is named slightly differently.
_BY_SERIES = {}


def _assign(domain, *series_ids):
    for sid in series_ids:
        _BY_SERIES[sid] = domain


_assign("elections",
        "generic_ballot_margin", "house_seats", "house_margin",
        "yougov_generic_margin", "mc_generic_margin", "ipsos_generic_margin")

_assign("consumer",
        "aaii_bull_bear_spread",
        "umich_sentiment", "umich_party_dem", "umich_party_ind",
        "umich_party_rep",
        "sce_inflation_1y", "sce_inflation_3y",
        "civiqs_net_econ_now", "civiqs_net_econ_direction",
        "civiqs_net_family_finances", "civiqs_net_inflation_concern")

_assign("attention",
        "wiki_top10_en", "wiki_views_trump", "wiki_views_taylor_swift",
        "trends_iphone", "trends_tesla",
        "trends_share_iphone", "trends_share_tesla", "trends_share_samsung",
        "trends_share_netflix", "trends_share_disney")

# Everything the arena asks about approval or an issue attitude. Listed rather
# than defaulted, for the reason the module docstring gives.
_assign("public-opinion",
        "civiqs_net_approval", "civiqs_angry_share",
        # Civiqs demographic cuts of the approval tracker.
        "civiqs_net_approval_dem", "civiqs_net_approval_rep",
        "civiqs_net_approval_ind",
        "civiqs_net_approval_male", "civiqs_net_approval_female",
        "civiqs_net_approval_age_18_34", "civiqs_net_approval_age_35_49",
        "civiqs_net_approval_age_50_64", "civiqs_net_approval_age_65_up",
        "civiqs_net_approval_race_white", "civiqs_net_approval_race_black",
        "civiqs_net_approval_race_hispanic", "civiqs_net_approval_race_other",
        "civiqs_net_approval_edu_noncollege", "civiqs_net_approval_edu_college",
        "civiqs_net_approval_edu_postgrad",
        # Wave pollsters: the headline number and each issue split.
        "yougov_approval", "yougov_rv_approval",
        "yougov_strong_approval", "yougov_weak_approval",
        "yougov_econ_approval", "yougov_immig_approval",
        "yougov_cost_approval", "yougov_trade_approval",
        "mc_approval", "mc_strong_approval", "mc_weak_approval",
        "mc_econ_approval", "mc_immig_approval", "mc_trade_approval",
        "hh_trump_approval", "rasmussen_approval", "rmg_approval",
        "ipsos_approval", "navigator_approval",
        # The YouGov crosstab cells, which are one profile round's roster.
        "yougov_xtab_approve_dem", "yougov_xtab_approve_ind",
        "yougov_xtab_approve_rep",
        "yougov_xtab_approve_male", "yougov_xtab_approve_female",
        "yougov_xtab_approve_age_under_30", "yougov_xtab_approve_age_30_44",
        "yougov_xtab_approve_age_45_64", "yougov_xtab_approve_age_65_up",
        "yougov_xtab_approve_race_white", "yougov_xtab_approve_race_black",
        "yougov_xtab_approve_race_hispanic",
        "yougov_xtab_approve_edu_hs_or_less",
        "yougov_xtab_approve_edu_some_college",
        "yougov_xtab_approve_edu_college_grad",
        "yougov_xtab_approve_edu_postgrad")


class UnknownDomain(KeyError):
    """A series nobody has placed on the board."""


def domain_of(series, strict=True):
    """The domain id for a series.

    `strict` raises for a series nobody has placed, which is what a caller
    wants when it can do something about it. `strict=False` answers
    `UNPLACED`, which is what the publish path wants: a gap in this table is
    a thing to fix on Monday, not a reason for the data refresh to stop.
    """
    try:
        return _BY_SERIES[series]
    except KeyError:
        if not strict:
            return UNPLACED
        raise UnknownDomain(
            f"series '{series}' has no domain in ssa/domains.py. Add it to "
            "one of the four, or add a fifth: a round the site cannot file "
            "under a heading is a round nobody browsing will find."
        ) from None


def known(series):
    """True when `series` is placed. For callers that must not raise."""
    return series in _BY_SERIES


def counts(rounds):
    """{domain_id: n} over `rounds`, in DOMAINS order, zeroes included.

    Zeroes are kept so a board renders the same set of headings every week: a
    domain that vanishes when nothing is scheduled reads as a domain that was
    removed.
    """
    out = {d: 0 for d in IDS}
    for r in rounds:
        d = domain_of(r["series"], strict=False)
        out[d] = out.get(d, 0) + 1
    return out
