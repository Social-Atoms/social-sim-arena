"""The ranking round: one question, one ordered list.

A scalar round asks for a number and a profile round asks for a population.
This one asks for an *order*: which ten articles the English Wikipedia read most
last week, in order; which of five fixed search terms the United States googled
most, in order. FutureX scores lists, this scores lists, and the reason is the
same -- a great many real forecasting questions are about rank rather than
level, and a benchmark made only of levels never asks them.

**Why the arena wants one.** Every other round type here resolves against a
published *number*, and a number carries a scale a model can anchor to. A
ranking has no scale to anchor to: an entrant cannot be roughly right by being
near the last value, because "near" is not defined on a permutation. Persistence
is still the null -- last week's list, in last week's order -- but beating it
requires knowing which specific thing will overtake which other specific thing,
which is a claim about the world and not about a series. On the Wikipedia side
it is a claim about attention itself: seven of the ten articles in a typical
week were not in the previous week's ten at all, so an entrant that cannot
anticipate what people will look up scores near the floor no matter how well it
extrapolates.

**Deliberately point-scored, and that is a deviation.** Everything else in this
repository refuses a point forecast: a topline is a distribution or it is
rejected, because a number without an interval hides whether the forecaster
knew anything. A ranking round does the opposite on purpose. The natural
"distribution" here is a distribution over permutations -- 10! orderings for the
Wikipedia round, and an unbounded space for the top-10-out-of-everything version
-- and nobody, human or model, can express one. Asking for it would measure
whether an entrant can emit a Plackett-Luce parameterization, not whether it
knows what people will read. So the answer is a list, the loss is a metric on
lists, and the round says so in its own definition rather than pretending its
score is comparable to a CRPS.

**Two losses, because there are two shapes of question.**

- *Rank-biased overlap* for the Wikipedia top ten (`rbo`, p fixed in the round).
  The item set is open: the truth is drawn from six million articles, an
  entrant's list may share nine items with it or none, and every rank-correlation
  measure -- Kendall, Spearman, footrule -- is undefined on lists that are not
  permutations of one another. RBO is defined for non-conjoint lists by
  construction, and it is top-weighted: it scores the agreement of the two
  prefixes at every depth and discounts depth geometrically, so getting first
  place right is worth more than getting tenth right. That matches the question
  a reader would actually ask of the answer.

- *Normalized Kendall tau distance* for the five-query Trends basket
  (`kendall`). Here the item set is closed and both lists are permutations of
  the same five queries, so every pairwise comparison is defined and every one
  of them is a real claim about the world: "more people searched Tesla than
  Netflix" is either right or wrong. The number of pairs an entrant got backwards,
  over the ten pairs available, is the natural loss -- it is the Kemeny distance,
  it is 0 for a perfect answer and 1 for an exact reversal, and it weights no
  position above another because the basket has no privileged position. A
  top-weighted measure would say that mixing up the first two is worse than
  mixing up the last two, which nothing about the question supports.

Both are deterministic, both land in [0, 1], and `skill = 1 - loss / loss of
persistence` is the scalar convention verbatim (`scoring.skill`), so a tenth of
skill means on this board what it means on every other one.

**No crowd row.** The scalar board pools every submission into an equal-weight
mixture and scores it; there is no such object here. A mixture of permutations
is a distribution over permutations, which is exactly what this round type has
decided not to score, and the alternatives (a Borda or Kemeny consensus) are a
different aggregation rule that would need its own justification and its own
tests. Left out rather than approximated.

**The freeze is the same one, three times over.** History is filtered to
`date < batches.freeze_at(...)` before persistence is computed, exactly as
`refresh.build_rounds` does for a scalar round and `profile_round.frozen_history`
does per cell. "The same one" is load-bearing and was briefly false: when the
weekly batch calendar moved the scalar freeze off `lock_at`, these two kept the
old spelling, and their nulls read days of history their entrants never saw. Both sources make that filter exact rather than approximate: a
Wikipedia week is dated by the Sunday it ends and its daily counts are final
within two days, and a Trends week is dated by its Saturday and frozen at the
value the earliest snapshot containing it showed. Neither can be relabelled into
history after the fact, which is the failure lock snapshots exist to prevent for
monthly series.
"""
from datetime import date, timedelta

from . import batches
from . import scoring
from .adapters import trends as trends_adapter
from .adapters import wikipedia as wikipedia_adapter

# The discriminator in `questions/season0.json`, the third value `target_type`
# takes. Every branch added for ranking rounds keys on it rather than on the
# shape of the data, so a malformed round definition fails as a malformed
# ranking round instead of being scored as something else.
TARGET_TYPE = "ranking_list"

# How many completed weeks of real lists a round carries as history: shown to
# entrants, and the pool the persistence null is taken from. Six rather than
# sixteen for a reason with a bill attached -- on the Wikipedia side each week
# is seven archived daily top-1000 files, about 220 KB, so history depth is disk
# in the repository forever. Six weeks is enough to show an entrant how much the
# list churns (it churns enormously) without committing a megabyte a round.
HISTORY_WEEKS = 6

# Which loss each kind may declare. A round names its loss explicitly so the
# scoring rule is frozen in the question rather than inferred at scoring time,
# and this map stops a round from naming one that does not apply to its shape:
# Kendall on an open item set is undefined, and RBO on a closed permutation
# would weight the basket's first position above its last for no reason.
KINDS = {
    "wiki_top10": {
        "losses": ("rbo",),
        "closed_set": False,
        "week_starts_on": 0,          # Monday
        "week_ends_on": 6,            # Sunday
    },
    "trends_basket": {
        "losses": ("kendall",),
        "closed_set": True,
        "week_starts_on": 6,          # Sunday
        "week_ends_on": 5,            # Saturday
    },
}

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday")


def is_ranking(r):
    """True for a ranking round definition (or a built round row)."""
    return bool(r) and r.get("target_type") == TARGET_TYPE


# --- the round definition ----------------------------------------------------

def spec_for(r):
    """The round's `ranking` block, validated. Raises rather than guesses.

    Every check here is one a round definition could fail silently: a length
    that disagrees with the basket, a week whose end is not the weekday the
    source publishes on, a loss that does not apply to the kind. All of them
    produce a round that collects forecasts and then cannot be scored, which is
    the one outcome worse than refusing to list it.
    """
    rid = r.get("round_id", "?")
    spec = r.get("ranking")
    if not isinstance(spec, dict):
        raise ValueError(f"{rid}: a ranking round needs a `ranking` block")
    kind = spec.get("kind")
    if kind not in KINDS:
        raise ValueError(
            f"{rid}: unknown ranking kind {kind!r}; known: {sorted(KINDS)}")
    meta = KINDS[kind]

    length = spec.get("length")
    if not isinstance(length, int) or isinstance(length, bool) or length < 2:
        raise ValueError(f"{rid}: `length` must be an integer >= 2, got {length!r}")

    loss = spec.get("loss")
    if loss not in meta["losses"]:
        raise ValueError(
            f"{rid}: kind {kind} is scored with {' or '.join(meta['losses'])}, "
            f"not {loss!r}")

    start, end = _week(rid, spec, meta)

    out = {
        "kind": kind, "length": length, "loss": loss,
        "week_start": start.isoformat(), "week_end": end.isoformat(),
        "closed_set": meta["closed_set"],
    }

    if kind == "wiki_top10":
        if spec.get("items"):
            raise ValueError(
                f"{rid}: a wiki_top10 round must not name `items`. Its answer "
                "is drawn from every article on the wiki; a fixed candidate "
                "list would be a different and much easier question.")
        out["project"] = spec.get("project") or wikipedia_adapter.PROJECT
        out["access"] = spec.get("access") or wikipedia_adapter.ACCESS
        out["exclusions"] = spec.get("exclusions") \
            or wikipedia_adapter.EXCLUSION_RULE_ID
        # Raises on a rule this build does not implement, here at listing time
        # rather than at resolution time.
        wikipedia_adapter.is_excluded("Main_Page", out["exclusions"])
        p = spec.get("rbo_p")
        if not isinstance(p, (int, float)) or isinstance(p, bool) \
                or not (0.0 < float(p) < 1.0):
            raise ValueError(
                f"{rid}: `rbo_p` must be strictly between 0 and 1, got {p!r}")
        out["rbo_p"] = float(p)
    else:
        items = trends_adapter.check_basket(spec.get("items"))
        if len(items) != length:
            raise ValueError(
                f"{rid}: `length` is {length} but the basket holds "
                f"{len(items)} queries; a basket round ranks all of them")
        out["items"] = list(items)
        out["geo"] = spec.get("geo") or trends_adapter.GEO
    return out


def _week(rid, spec, meta):
    """(start, end) as dates, checked against the source's own week boundary."""
    try:
        start = date.fromisoformat(str(spec.get("week_start")))
        end = date.fromisoformat(str(spec.get("week_end")))
    except (TypeError, ValueError):
        raise ValueError(
            f"{rid}: `week_start` and `week_end` must be ISO dates, got "
            f"{spec.get('week_start')!r} and {spec.get('week_end')!r}") from None
    if end - start != timedelta(days=6):
        raise ValueError(
            f"{rid}: {start} to {end} is {(end - start).days + 1} days; a "
            "ranking week is seven")
    for label, d, want in (("week_start", start, meta["week_starts_on"]),
                           ("week_end", end, meta["week_ends_on"])):
        if d.weekday() != want:
            raise ValueError(
                f"{rid}: {label} {d.isoformat()} is a {_WEEKDAYS[d.weekday()]}, "
                f"but this source's week {label.split('_')[1]}s on a "
                f"{_WEEKDAYS[want]}")
    return start, end


def question_universe(spec):
    """The sentence the prompt uses to say what may appear in the answer."""
    if spec["kind"] == "wiki_top10":
        return (
            "Any article on the English Wikipedia may appear. Write titles in "
            "the site's canonical form, with underscores for spaces, exactly as "
            "they appear in a wikipedia.org URL (for example Donald_Trump, "
            "Deaths_in_2026, The_Odyssey_(2026_film)).\n"
            "These are excluded from the ranking and must not appear in your "
            "answer: Main_Page, and any title in a non-article namespace -- "
            "anything beginning " + ", ".join(
                wikipedia_adapter.NAMESPACE_PREFIXES[:10]) + " or their _talk: "
            "variants. Everything else counts, including titles that merely "
            "contain a colon (Spider-Man:_Brand_New_Day is an article and is "
            "eligible).\n")
    return ("Your answer is an ordering of exactly these "
            f"{spec['length']} queries and nothing else -- every one of them "
            "appears exactly once:\n  "
            + "\n  ".join(spec["items"]) + "\n")


# --- observations: the source's own history of lists -------------------------

def observations(r, fetch=False, now=None, spec=None, diagnostics=None):
    """[{date, items, ...}] oldest first: one completed week per entry.

    `date` is the week's last day, which is what makes the freeze below a plain
    string comparison against the lock date. `fetch` off by default: this is
    read at build time on every refresh including CI's, where the Trends fetch
    cannot succeed and the archive is the answer.
    """
    spec = spec or spec_for(r)
    if spec["kind"] == "wiki_top10":
        return _wiki_observations(
            spec, fetch=fetch, now=now, diagnostics=diagnostics)
    return _basket_observations(
        spec, fetch=fetch, now=now, diagnostics=diagnostics)


def _week_ends(spec, count=HISTORY_WEEKS):
    """The round's own week end, and the `count` week ends before it."""
    end = date.fromisoformat(spec["week_end"])
    return [end - timedelta(days=7 * i) for i in range(count, -1, -1)]


def _wiki_observations(spec, fetch=False, now=None, diagnostics=None):
    out = []
    for end in _week_ends(spec):
        try:
            items, totals = wikipedia_adapter.weekly_top(
                end, spec["length"], spec["exclusions"], spec["project"],
                spec["access"], fetch=fetch, now=now)
        except Exception as error:            # noqa: BLE001 - see below
            # A week the archive does not hold all seven days of is simply not
            # history yet: before the round's own week it has not happened, and
            # before the archive began it never will. Skipped rather than
            # raised, because history is allowed to be short -- while
            # `resolution` below raises loudly for the one week that must be
            # there, with the adapter's own message naming the missing days.
            # A not-yet-final target week normally reports only that archive
            # days are missing; that is expected, not an outage.  Any other
            # exception while fetch=True is a failed live attempt (HTTP,
            # transport, or parser) and must survive even when six older
            # archived weeks still make a useful history available.
            expected_incomplete = str(error).startswith("week ending ") and \
                " from the " in str(error) and " archive " in str(error)
            if fetch and not expected_incomplete and diagnostics is not None:
                diagnostics.append({
                    "source": "ranking_wikitop",
                    "scope": end.isoformat(),
                    "error": error,
                    "archive_evidence": (
                        "same-source Wikimedia daily-top archive retained "
                        "other complete weeks"),
                })
            continue
        out.append({"date": end.isoformat(), "items": items,
                    "views": {t: totals[t] for t in items}})
    return out


def _basket_observations(spec, fetch=False, now=None, diagnostics=None):
    weeks = trends_adapter.basket_weeks(spec["items"], spec["geo"], now=now,
                                        fetch=fetch, diagnostics=diagnostics)
    keep = {d.isoformat() for d in _week_ends(spec)}
    return [{"date": w["date"],
             "items": trends_adapter.basket_order(w["values"], spec["items"]),
             "index": dict(w["values"])}
            for w in weeks if w["date"] in keep]


# --- the freeze --------------------------------------------------------------

def frozen_history(r, obs):
    """The observations strictly before the round's freeze, oldest first.

    `refresh.build_rounds`' filter, written here rather than inlined so a
    ranking round cannot drift away from the one rule: without it, the moment
    the measured week lands in the archive the persistence null would be the
    very list it is scored against.

    The freeze is `batches.freeze_at`, not `lock_at` -- the same correction
    `profile_round.frozen_history` needed. The two were one instant until the
    weekly batch calendar separated them, and a null frozen at the lock reads
    series its entrants could not. `freeze_at` returns the lock for rounds that
    predate the cutover, so nothing already scored moves.
    """
    lock_date = batches.freeze_at(r["lock_at"]).strftime("%Y-%m-%d")
    return [o for o in (obs or []) if o["date"] < lock_date]


def persistence_list(hist, spec):
    """The last completed week's list, in its own order: the skill denominator.

    Persistence on a ranking is "the same things, in the same order, again". It
    is the right null for the same reason it is right for a topline -- it is
    what a reader with no model would say -- and for one more: it is a *real*
    list, so it is automatically well formed, which no other cheap null is. An
    entrant beats it only by naming a change, which is the whole question.

    Raises rather than substituting anything. A ranking round with no pre-lock
    week has no denominator, and a skill number against a made-up null means
    nothing.
    """
    if not hist:
        raise ValueError(
            "no completed week before the lock: this ranking round has no "
            "persistence null and therefore no skill denominator")
    last = hist[-1]
    items = list(last["items"])
    if len(items) != spec["length"]:
        raise ValueError(
            f"the pre-lock week {last['date']} holds {len(items)} items, not "
            f"{spec['length']}; the null would not be a valid submission")
    return {"items": items, "observed_date": last["date"], "method": "persistence"}


def ranking_baselines(r, obs, spec=None):
    """The nulls a ranking round ships with, frozen at lock.

    Persistence only, and unlike the profile round's "persistence only" this one
    is not a deferred decision. Trend, ewma and climatology are arithmetic on a
    level; there is no such arithmetic on an order. A trend null would have to
    mean "keep moving up the list at the rate you were", which for an item that
    just entered at rank one is undefined and for one that left the list is
    unrepresentable. The nulls that *do* exist for orders -- a random
    permutation, an alphabetical list -- are not forecasts anyone would make and
    would only flatter the board.
    """
    spec = spec or spec_for(r)
    hist = frozen_history(r, obs)
    return {"persistence": persistence_list(hist, spec)}, hist


# --- resolution --------------------------------------------------------------

def resolution(r, obs=None, spec=None, fetch=False, now=None):
    """The true ordered list for the round's own week, or a raised explanation.

    Read from the same archive the history came from, at the week the round
    names. ``obs`` is an explicit dependency-injection seam for an offline
    rehearsal: when supplied it must contain that exact completed week and is
    normalized through the same round contract. Production callers omit it,
    so a missing live week still gets the adapter's precise complaint (which
    days are absent, which snapshot is needed).

    The one check that is not the adapter's: the answer must not be a week the
    frozen history already contained. That cannot happen if the locks are set
    correctly -- every round here locks before its measured week begins -- but a
    mis-set lock would otherwise resolve a round against a list its own
    persistence null had already seen, scoring everyone against a number that
    existed before they were asked.
    """
    spec = spec or spec_for(r)
    week_end = spec["week_end"]
    # The boundary is the freeze, not the lock: `batches.freeze_at` is where
    # this round's persistence null stops reading, and "existed when the round
    # froze" is a claim about that instant. The two coincide before the batch
    # cutover. After it the lock is up to seven days later, so anchoring here on
    # the lock would refuse a week that began after entrants answered.
    freeze_date = batches.freeze_at(r["lock_at"]).strftime("%Y-%m-%d")
    if week_end < freeze_date:
        raise ValueError(
            f"{r.get('round_id')}: the measured week ends {week_end}, before "
            f"the freeze at {freeze_date}; its answer existed when the "
            "round froze and cannot be scored")
    if obs is not None:
        got = next((row for row in obs if row.get("date") == week_end), None)
        if got is None:
            raise ValueError(
                f"{r.get('round_id')}: injected archive has no completed week "
                f"ending {week_end}")
        items = normalize(got.get("items"), spec, where="resolution archive")
        return {
            "items": items,
            "week_start": spec["week_start"],
            "week_end": week_end,
            "method": ("the explicitly supplied offline archive observation, "
                       "validated by the production ranking resolver"),
        }
    if spec["kind"] == "wiki_top10":
        items, totals = wikipedia_adapter.weekly_top(
            week_end, spec["length"], spec["exclusions"], spec["project"],
            spec["access"], fetch=fetch, now=now)
        detail = {
            "items": items,
            "week_start": spec["week_start"],
            "week_end": week_end,
            "views": {t: totals[t] for t in items},
            "method": (
                "the seven archived daily top-1000 lists for the week, summed "
                f"per article, exclusion rule {spec['exclusions']} applied, "
                f"top {spec['length']} by weekly total, ties broken by title "
                "ascending"),
        }
    else:
        weeks = trends_adapter.basket_weeks(spec["items"], spec["geo"],
                                            now=now, fetch=fetch)
        got = next((w for w in weeks if w["date"] == week_end), None)
        if got is None:
            raise ValueError(
                f"{r.get('round_id')}: no completed week ending {week_end} in "
                f"the Trends basket archive for {list(spec['items'])}; the "
                "snapshot carrying it has not been committed yet")
        detail = {
            "items": trends_adapter.basket_order(got["values"], spec["items"]),
            "week_start": spec["week_start"],
            "week_end": week_end,
            "index": dict(got["values"]),
            "method": (
                "the first archived five-query comparison snapshot carrying "
                "the completed week, ordered by weekly search interest, ties "
                "broken by the basket's declared order"),
        }
    return detail


def outcome_items(res, spec):
    """A stored resolution -> the truth list, normalized. Raises on a bad one."""
    items = (res or {}).get("items")
    if not isinstance(items, list):
        raise ValueError("resolution carries no `items` list")
    return normalize(items, spec, where="resolution")


# --- submissions -------------------------------------------------------------

def normalize(raw, spec, where="submission"):
    """An ordered list -> the canonical form both sides are scored in.

    Every rejection here is one that would otherwise be scored as a wrong answer
    rather than as a malformed one, which are different things and only one of
    them is the entrant's fault. The canonicalization is narrow on purpose: it
    fixes the envelope (spacing, the one letter MediaWiki itself case-folds, the
    capitalization of a fixed basket word) and never the content. `Roblox` and
    `roblox` are one article and normalize together; `Roblox` and `Roblux` are
    two, and no amount of helpfulness should turn the second into the first.
    """
    if not isinstance(raw, list):
        raise ValueError(
            f"{where}: a ranking answer is a JSON array of strings, got "
            f"{type(raw).__name__}")
    n = spec["length"]
    if len(raw) != n:
        raise ValueError(
            f"{where}: a ranking answer for this round is exactly {n} items, "
            f"got {len(raw)}")
    if spec["kind"] == "wiki_top10":
        items = [wikipedia_adapter.canonical_title(x) for x in raw]
        bad = [x for x in items
               if wikipedia_adapter.is_excluded(x, spec["exclusions"])]
        if bad:
            raise ValueError(
                f"{where}: {', '.join(bad[:3])} "
                f"{'is' if len(bad) == 1 else 'are'} excluded from this round "
                f"by rule {spec['exclusions']} and cannot be ranked")
    else:
        canon = {q.casefold(): q for q in spec["items"]}
        items = []
        for x in raw:
            if not isinstance(x, str):
                raise ValueError(
                    f"{where}: every item is a string, got "
                    f"{type(x).__name__}")
            got = canon.get(x.strip().casefold())
            if got is None:
                raise ValueError(
                    f"{where}: {x!r} is not one of this round's basket "
                    f"queries ({', '.join(spec['items'])})")
            items.append(got)
    dupes = sorted({x for x in items if items.count(x) > 1})
    if dupes:
        raise ValueError(
            f"{where}: a ranking lists each item once; repeated: "
            f"{', '.join(dupes[:3])}")
    return items


def submission_list(fc, spec):
    """A submission's `ranking` block -> the normalized ordered list.

    Raises on a missing block for the reason `profile_round.submission_cells`
    gives about a hole: the schema knows what a ranking looks like but not which
    question this round asked, and only here can the two be compared.
    """
    block = (fc or {}).get("ranking")
    if block is None:
        raise ValueError(
            f"{(fc or {}).get('entrant')}: a ranking round needs a `ranking` "
            "list; this submission has none")
    return normalize(block, spec, where=f"{(fc or {}).get('entrant')}")


# --- the two losses ----------------------------------------------------------

def rbo_similarity(predicted, truth, p, depth=None):
    """Rank-biased overlap of two lists, in [0, 1]. 1 is identical.

    RBO scores the *prefixes*: at each depth d it takes the fraction of the two
    top-d sets that agree, and averages those agreements with geometrically
    decaying weights (1-p)p^(d-1). Two properties make it the right measure for
    an open-item-set top-N, and no rank correlation has either:

    - it is defined when the lists share few or no items, because it never
      needs to look up the truth's rank of something the truth does not
      contain;
    - it is top-weighted, so an entrant that gets first place right and the
      tail wrong beats one with the same overlap scattered down the list.

    The infinite sum is truncated at `depth` (the list length) and divided by
    the weight actually used, which sums to 1 - p^depth, so identical lists
    score 1 and disjoint ones 0. That normalization is what makes the loss below
    a number in [0, 1] rather than one bounded above by an untidy constant --
    and it is applied identically to entrant and null, so it cannot favour
    either. The divisor is accumulated rather than taken from the closed form,
    which costs nothing and makes a perfect list score exactly 1.0 instead of
    1 - 2e-16: a "loss" of 2e-16 on the leaderboard is the kind of thing a
    reader rightly does not trust.
    """
    d = depth or max(len(predicted), len(truth))
    if d <= 0:
        raise ValueError("rank-biased overlap needs a non-empty list")
    if not (0.0 < p < 1.0):
        raise ValueError(f"rbo p must be strictly between 0 and 1, got {p}")
    seen_p, seen_t = set(), set()
    agreement, weight, hit = 0.0, 0.0, 0
    for i in range(d):
        if i < len(predicted):
            x = predicted[i]
            if x in seen_t:
                hit += 1
            seen_p.add(x)
        if i < len(truth):
            y = truth[i]
            if y in seen_p:
                hit += 1
            seen_t.add(y)
        # `hit` is the running size of the intersection of the two depth-i
        # prefixes, grown one depth at a time: an item entering one list is
        # counted iff the other list already holds it. An item at the same depth
        # in both is counted exactly once, by the second test, since the first
        # ran before `seen_t` had it. Rebuilding the sets each depth would say
        # the same thing more obviously and quadratically; the tests check this
        # against a brute-force intersection at every depth.
        w = (1.0 - p) * (p ** i)
        weight += w
        agreement += w * (hit / (i + 1))
    return agreement / weight


def rbo_loss(predicted, truth, p, depth=None):
    """1 - rank-biased overlap: 0 for a perfect list, 1 for a disjoint one."""
    return 1.0 - rbo_similarity(predicted, truth, p, depth)


def kendall_loss(predicted, truth):
    """Discordant pairs over all pairs, for two permutations of one set.

    Also called the normalized Kemeny distance. 0 when the orders agree, 1 when
    one is the exact reverse of the other, and each intermediate value is a
    count of specific wrong claims: with five queries there are ten pairs, and
    the loss is simply how many of the ten the entrant got the wrong way round.

    Raises unless the two lists are permutations of one another, rather than
    scoring what it can. A pair whose members are not both present has no
    concordance to measure, and silently ignoring such pairs would score a
    submission on a shorter list than the round asked for.
    """
    if sorted(predicted) != sorted(truth):
        missing = sorted(set(truth) - set(predicted))
        extra = sorted(set(predicted) - set(truth))
        raise ValueError(
            "Kendall tau distance needs two permutations of one set; "
            f"missing {missing or 'nothing'}, unexpected {extra or 'nothing'}")
    n = len(truth)
    if n < 2:
        raise ValueError("Kendall tau distance needs at least two items")
    rank = {item: i for i, item in enumerate(truth)}
    discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            if rank[predicted[i]] > rank[predicted[j]]:
                discordant += 1
    pairs = n * (n - 1) // 2
    return discordant / pairs, discordant, pairs


def score_list(items, outcome, spec):
    """{loss, ...} for one ordered list against the truth, by the round's rule."""
    if spec["loss"] == "rbo":
        loss = rbo_loss(items, outcome, spec["rbo_p"], spec["length"])
        shared = len(set(items) & set(outcome))
        exact = sum(1 for a, b in zip(items, outcome) if a == b)
        return {"loss": loss, "overlap": shared, "exact_positions": exact,
                "n": spec["length"]}
    loss, discordant, pairs = kendall_loss(items, outcome)
    exact = sum(1 for a, b in zip(items, outcome) if a == b)
    return {"loss": loss, "discordant_pairs": discordant, "pairs": pairs,
            "exact_positions": exact, "n": spec["length"]}


def score_submission(fc, outcome, spec):
    """The loss for a submitted forecast file. Raises on a malformed one."""
    return score_list(submission_list(fc, spec), outcome, spec)


def ranking_skill(entrant_loss, persistence_loss):
    """1 - loss(entrant) / loss(persistence), the scalar convention verbatim.

    Named rather than left as a call to `scoring.skill` for the reason
    `scoring.profile_skill` gives: the boards have to be readable next to each
    other, and "a tenth of skill" must mean one thing everywhere -- a tenth of
    the null's loss removed.

    One consequence is worth stating because it will happen and will look like a
    bug. When persistence is perfect the denominator is zero, and the convention
    (`scoring.skill`) returns 0 for everyone. On a five-item basket that is an
    ordinary week: the null often gets all ten pairs right. The round is not
    dropped and its zero is not a failure to score, it is the honest answer to
    "how much of the null's loss did you remove" when there was none to remove;
    `persistence_loss` is published beside every round so a reader can see it.
    """
    return scoring.skill(entrant_loss, persistence_loss)
