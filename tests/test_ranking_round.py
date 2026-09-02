"""The ranking round, end to end. Plain asserts, no pytest, no network.

Run: PYTHONPATH=. python tests/test_ranking_round.py

Covers both trackers the round type serves and the whole path each takes: the
adapter's exclusion and week rules, the archive that makes resolution
reproducible, the two losses, the schema, the validator, the harness parse and
call, the freeze, the resolution and the board.

**Every source is a fixture and no request is ever made.** The Wikipedia daily
top lists are written into a temp archive in the shape the real endpoint's
`parse_top` emits, and the Google Trends basket snapshots into another -- Trends
in particular must never be called from a test, because the host rate-limits by
address and a suite that hits it would cost the repository its real archive
fetches. One test reads the *committed* wikitop archive instead, on purpose:
that a real week resolves from the repository with no network is the claim the
archive exists to support, and it should fail loudly if the archive is ever
pruned.
"""
import importlib.util
import io
import contextlib
import glob
import json
import os
import random
import shutil
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa import harness, ranking_round, refresh                   # noqa: E402
from ssa.adapters import trends, wikipedia                        # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --- the two rounds under test ----------------------------------------------
#
# Both sit in the past relative to any run, so the board (which reads the wall
# clock to decide whether a round is due) actually scores them.

WIKI_ROUND = {
    "round_id": "wiki-top10-test",
    "tracker": "wikipedia",
    "series": "wiki_top10_en",
    "question": "The ordered top-10 en.wikipedia articles for the week",
    "unit": "ordered list of 10 article titles",
    "release_at": "2026-08-17T14:00:00Z",
    "release_estimated": False,
    "lock_at": "2026-08-07T14:00:00Z",
    "resolve": "seven archived daily top lists, summed",
    "target_type": "ranking_list",
    "ranking": {
        "kind": "wiki_top10", "length": 10, "loss": "rbo", "rbo_p": 0.9,
        "week_start": "2026-08-10", "week_end": "2026-08-16",
        "exclusions": "main_page_and_namespaces_v1",
    },
}

BASKET = ["Tesla", "iPhone", "Samsung", "Netflix", "Disney"]

TRENDS_ROUND = {
    "round_id": "trends-basket-test",
    "tracker": "google_trends",
    "series": "trends_basket_us5",
    "question": "Rank the five basket queries by weekly search interest",
    "unit": "ordered list of the 5 basket queries",
    "release_at": "2026-08-17T14:00:00Z",
    "release_estimated": True,
    "lock_at": "2026-08-06T14:00:00Z",
    "resolve": "first archived comparison snapshot carrying the completed week",
    "target_type": "ranking_list",
    "ranking": {
        "kind": "trends_basket", "length": 5, "loss": "kendall",
        "items": BASKET, "geo": "US",
        "week_start": "2026-08-09", "week_end": "2026-08-15",
    },
}

# Twelve eligible titles, two of which carry a colon that is *not* a namespace,
# so a blanket colon test cannot pass this suite.
TITLES = ["Roblox", "Jason_Arday", "Spider-Man:_Brand_New_Day", ".xxx",
          "The_Odyssey_(2026_film)", "Deaths_in_2026", "Awarapan_2",
          "Joshua_Kushner", ".xyz", "The_Last_House", "Avengers:_Doomsday",
          "Prichard_Colon"]

# The measured week's true order differs from the week before it by an adjacent
# swap at the top and one substitution at the tail, so persistence is neither
# perfect (skill would be undefined) nor hopeless.
WIKI_WEEKS = {
    "2026-08-16": TITLES[1::-1] + TITLES[2:9] + [TITLES[10]],
    "2026-08-09": TITLES[:10],
    "2026-08-02": TITLES[:10],
    "2026-07-26": TITLES[:10],
    "2026-07-19": TITLES[:10],
    "2026-07-12": TITLES[:10],
    "2026-07-05": TITLES[:10],
}

# Week ending 2026-08-15 (the measured one) reverses the top pair and the bottom
# pair against the week before: two discordant pairs out of ten.
BASKET_WEEKS = {
    "2026-08-15": {"Tesla": 70, "iPhone": 88, "Samsung": 40, "Netflix": 12,
                   "Disney": 25},
    "2026-08-08": {"Tesla": 80, "iPhone": 61, "Samsung": 44, "Netflix": 30,
                   "Disney": 18},
    "2026-08-01": {"Tesla": 90, "iPhone": 62, "Samsung": 41, "Netflix": 33,
                   "Disney": 20},
    "2026-07-25": {"Tesla": 85, "iPhone": 60, "Samsung": 40, "Netflix": 30,
                   "Disney": 20},
}


class Archives:
    """Temp wikitop/ and trends/ archives, populated and torn down."""

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="ssa-ranking-")
        self.saved = (wikipedia.TOP_ARCHIVE, trends.ARCHIVE)
        wikipedia.TOP_ARCHIVE = os.path.join(self.dir, "wikitop")
        trends.ARCHIVE = os.path.join(self.dir, "trends")
        for end, order in WIKI_WEEKS.items():
            self.wiki_week(end, order)
        self.basket_snapshot("2026-08-17", sorted(BASKET_WEEKS))
        return self

    def __exit__(self, *a):
        wikipedia.TOP_ARCHIVE, trends.ARCHIVE = self.saved
        shutil.rmtree(self.dir, ignore_errors=True)

    def wiki_week(self, week_end, order, days=None, extra_daily=None):
        """Seven daily top lists whose weekly sum ranks exactly `order`.

        Each title's daily count is flat and strictly decreasing down the order,
        so the weekly ranking is legible by eye and no tie-break is exercised
        unless a test asks for one. Main_Page and two namespace pages are added
        to every day, far above everything else, because that is what the real
        endpoint serves and dropping them is the round's contract.
        """
        for d in (days if days is not None else wikipedia.week_days(week_end)):
            arts = {"Main_Page": 9_000_000, "Special:Search": 800_000,
                    "Wikipedia:Featured_pictures": 700_000,
                    "Portal:Current_events": 300_000, "Talk:Roblox": 250_000}
            for i, title in enumerate(order):
                arts[title] = 100_000 - 1_000 * i
            for title in TITLES:
                arts.setdefault(title, 5_000)
            arts.update(extra_daily or {})
            wikipedia.write_top_snapshot(
                wikipedia.top_archive_key(),
                wikipedia.build_top_snapshot(arts, d, wikipedia.PROJECT,
                                             wikipedia.ACCESS, _AT))

    def basket_snapshot(self, fetch_day, week_ends, values=None, partial=(),
                        queries=None):
        qs = queries or BASKET
        rows = []
        for end in week_ends:
            e = date.fromisoformat(end)
            vals = (values or BASKET_WEEKS)[end]
            rows.append({"week_start": (e - timedelta(days=6)).isoformat(),
                         "week_end": end,
                         "values": {q: vals[q] for q in qs},
                         "partial": end in partial})
        snap = trends.build_basket_snapshot(rows, qs, "US", _AT)
        trends.write_snapshot(trends.basket_key(qs, "US"), snap,
                              date.fromisoformat(fetch_day))


class _At:
    def strftime(self, fmt):
        return "2026-08-18T00:00:00Z"


_AT = _At()


# --- the exclusion rule ------------------------------------------------------

def test_the_exclusion_rule_drops_the_machinery_and_keeps_the_articles():
    for bad in ("Main_Page", "Special:Search", "Wikipedia:Featured_pictures",
                "Portal:Current_events", "Help:Contents", "File:Example.jpg",
                "Template:Infobox", "Category:Living_people", "Draft:Foo",
                "User:Example", "Talk:Roblox", "Wikipedia_talk:Foo",
                "User_talk:Example", "Category_talk:Foo"):
        assert wikipedia.is_excluded(bad), bad
    for ok in ("Roblox", "Spider-Man:_Brand_New_Day", "Avengers:_Doomsday",
               "X-Men:_Days_of_Future_Past", ".xxx", "Deaths_in_2026",
               "The_Odyssey_(2026_film)", "Mission:_Impossible"):
        assert not wikipedia.is_excluded(ok), ok


def test_a_blanket_colon_test_would_be_wrong_and_this_rule_is_not_one():
    """The measured week of 2026-08-10 had a colon-titled article in third
    place. A 'contains a colon' rule is the obvious implementation and would
    have deleted it, along with thirty others, while catching nothing the
    prefix list misses."""
    colon_articles = [t for t in TITLES if ":" in t]
    assert colon_articles, "fixture sanity"
    assert not any(wikipedia.is_excluded(t) for t in colon_articles)


def test_an_unknown_exclusion_rule_id_is_refused_not_approximated():
    try:
        wikipedia.is_excluded("Roblox", "some_future_rule_v2")
    except ValueError as e:
        assert "main_page_and_namespaces_v1" in str(e), str(e)
    else:
        raise AssertionError("an unknown rule must not fall back to the current one")


def test_titles_canonicalize_the_way_mediawiki_does():
    assert wikipedia.canonical_title("donald trump") == "Donald_trump"
    assert wikipedia.canonical_title("  Donald   Trump  ") == "Donald_Trump"
    assert wikipedia.canonical_title("Donald_Trump") == "Donald_Trump"
    # case beyond the first letter is significant on en.wikipedia and is kept
    assert wikipedia.canonical_title("Donald_TRUMP") == "Donald_TRUMP"
    for bad in ("", "   ", 7, None, "x" * 300):
        try:
            wikipedia.canonical_title(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"junk title accepted: {bad!r}")


# --- the daily endpoint's payload -------------------------------------------

def top_payload(day, articles):
    y, m, d = day.split("-")
    return {"items": [{"project": "en.wikipedia", "access": "all-access",
                       "year": y, "month": m, "day": d,
                       "articles": [{"article": a, "views": v, "rank": i + 1}
                                    for i, (a, v) in enumerate(articles)]}]}


def test_a_days_payload_parses_and_the_three_bad_ones_raise():
    got = wikipedia.parse_top(top_payload("2026-08-16", [("Roblox", 10),
                                                         ("Main_Page", 99)]),
                              "2026-08-16")
    assert got == {"Roblox": 10, "Main_Page": 99}

    for payload, why in (
            ({"items": []}, "empty items"),
            ({"items": [{"year": "2026", "month": "08", "day": "15",
                         "articles": [{"article": "A", "views": 1}]}]},
             "wrong day"),
            (top_payload("2026-08-16", [("A", 1), ("A", 2)]), "duplicate"),
            (top_payload("2026-08-16", []), "no articles")):
        try:
            wikipedia.parse_top(payload, "2026-08-16")
        except RuntimeError:
            pass
        else:
            raise AssertionError(f"{why} must not parse")


# --- weeks and the archive ---------------------------------------------------

def test_a_week_runs_monday_to_sunday_and_refuses_any_other_end():
    days = wikipedia.week_days("2026-08-16")
    assert [d.isoformat() for d in days] == \
        [f"2026-08-{n}" for n in range(10, 17)]
    assert days[0].weekday() == 0 and days[-1].weekday() == 6
    for not_sunday in ("2026-08-15", "2026-08-17"):
        try:
            wikipedia.week_days(not_sunday)
        except ValueError as e:
            assert "Sunday" in str(e), str(e)
        else:
            raise AssertionError(f"{not_sunday} is not a Sunday")


def test_a_week_needs_all_seven_days_and_names_the_ones_it_lacks():
    with Archives():
        assert wikipedia.weekly_top("2026-08-16", 10, fetch=False)[0] == \
            WIKI_WEEKS["2026-08-16"]
        os.remove(wikipedia.top_archive_path(wikipedia.top_archive_key(),
                                             "2026-08-12"))
        try:
            wikipedia.weekly_totals("2026-08-16", fetch=False)
        except RuntimeError as e:
            assert "2026-08-12" in str(e) and "1 of 7" in str(e), str(e)
        else:
            raise AssertionError("a six-day week must not be ranked")
        assert "2026-08-16" not in [d.isoformat()
                                    for d in wikipedia.archived_weeks()]


def test_the_weekly_ranking_sums_the_days_and_applies_the_exclusions():
    with Archives():
        items, totals = wikipedia.weekly_top("2026-08-16", 10, fetch=False)
        assert items == WIKI_WEEKS["2026-08-16"]
        # the sum is over all seven days, and the machinery is gone from the
        # ranking while remaining in the archive
        assert totals["Main_Page"] == 7 * 9_000_000
        assert "Main_Page" not in items and "Special:Search" not in items
        assert totals[items[0]] == 7 * 100_000


def test_ties_are_broken_by_title_not_by_dictionary_order():
    with Archives() as ar:
        ar.wiki_week("2026-06-28", TITLES[:10],
                     extra_daily={"Zebra": 100_000, "Alpaca": 100_000})
        items, totals = wikipedia.weekly_top("2026-06-28", 12, fetch=False)
        # Alpaca, Roblox and Zebra all reach 700,000 for the week; the order
        # among them is the tie-break's, not the dictionary's
        assert totals["Alpaca"] == totals["Zebra"] == totals["Roblox"]
        assert items[:3] == ["Alpaca", "Roblox", "Zebra"], items[:3]


def test_a_day_is_written_once_and_never_overwritten():
    """The archive is the resolution source. A later write could move a number a
    round has already been scored against."""
    with Archives():
        key = wikipedia.top_archive_key()
        path = wikipedia.top_archive_path(key, "2026-08-16")
        before = open(path).read()
        wikipedia.write_top_snapshot(key, wikipedia.build_top_snapshot(
            {"Roblox": 1}, date(2026, 8, 16), wikipedia.PROJECT,
            wikipedia.ACCESS, _AT))
        assert open(path).read() == before


def test_a_day_that_is_still_settling_is_never_fetched_or_archived():
    """Two costs avoided. A day the logs have not finished is a 404, and asking
    for the whole in-progress week every six hours spends dozens of requests to
    learn that. Worse, the archive is write-once: a count captured while it was
    still settling would be wrong here forever."""
    from datetime import datetime, timezone
    with Archives():
        called = []

        def fake(u, *a, **k):
            day = "-".join(u.rsplit("/", 3)[1:])
            called.append(day)
            return top_payload(day, [(t, 1000) for t in TITLES])

        saved = wikipedia._get_json
        wikipedia._get_json = fake
        try:
            now = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
            for day in ("2026-08-20", "2026-08-19"):
                assert wikipedia.top_snapshot(day, fetch=True, now=now) is None
            assert called == [], "a settling day must cost no request"
            # ...and the week holding them is not rankable rather than wrong:
            # the two settled days are fetched and archived, the five that are
            # not yet final are reported missing
            try:
                wikipedia.weekly_totals("2026-08-23", fetch=True, now=now)
            except RuntimeError as e:
                assert "missing 5 of 7" in str(e) and "2026-08-19" in str(e), str(e)
            else:
                raise AssertionError("an in-progress week must not rank")
            assert called == ["2026-08-17", "2026-08-18"], called
        finally:
            wikipedia._get_json = saved


def test_reading_the_archive_never_touches_the_network():
    """`fetch=False` is what CI and every rerun use, and it must be able to
    answer a whole week from committed files alone."""
    with Archives():
        called = []
        saved = wikipedia._get_json
        wikipedia._get_json = lambda *a, **k: called.append(a) or {}
        try:
            items, _ = wikipedia.weekly_top("2026-08-16", 10, fetch=False)
        finally:
            wikipedia._get_json = saved
        assert items and called == []


def test_the_committed_archive_resolves_a_real_week_with_no_network():
    """Not a fixture: the repository's own wikitop/ for the week of
    2026-08-10, fetched from Wikimedia and committed. If this fails, either the
    archive was pruned or the week rule changed -- and either way a resolved
    round is no longer reproducible from the repository, which is the whole
    reason the archive exists."""
    ends = [d.isoformat() for d in wikipedia.archived_weeks()]
    assert "2026-08-16" in ends, ends
    items, totals = wikipedia.weekly_top("2026-08-16", 10, fetch=False)
    assert len(items) == 10 and len(set(items)) == 10
    assert not any(wikipedia.is_excluded(t) for t in items)
    assert totals["Main_Page"] > totals[items[0]], \
        "Main_Page still dominates the raw archive; only the ranking drops it"
    assert all(totals[a] >= totals[b] for a, b in zip(items, items[1:]))


# --- the Trends basket -------------------------------------------------------

def test_a_basket_holds_two_to_five_distinct_queries():
    assert trends.check_basket(BASKET) == tuple(BASKET)
    for bad in ([], ["Tesla"], BASKET + ["Ford"], ["Tesla", "Tesla"],
                ["Tesla", ""], ["Tesla", 7]):
        try:
            trends.check_basket(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"basket accepted: {bad!r}")


def basket_body(rows):
    data = []
    for t, vals, partial in rows:
        row = {"time": str(t), "formattedTime": "wk", "value": list(vals),
               "hasData": [True] * len(vals)}
        if partial:
            row["isPartial"] = True
        data.append(row)
    return ")]}',\n" + json.dumps({"default": {"timelineData": data}})


def test_the_basket_parser_wants_one_value_per_query_and_weekly_rows():
    rows = trends.parse_basket_timeline(
        basket_body([(1785628800, [76, 60, 40, 30, 20], False),
                     (1786233600, [74, 61, 41, 31, 21], True)]), BASKET)
    assert rows[0]["values"] == {"Tesla": 76, "iPhone": 60, "Samsung": 40,
                                 "Netflix": 30, "Disney": 20}
    assert rows[0]["partial"] is False and rows[1]["partial"] is True
    assert rows[0]["week_start"] == "2026-08-02" and rows[0]["week_end"] == "2026-08-08"

    for body, why in (
            (basket_body([(1785628800, [76, 60], False)]), "two of five"),
            (basket_body([(1785628800, [76, 60, 40, 30, 20], False),
                          (1785628800 + 86400, [1, 2, 3, 4, 5], False)]), "daily rows"),
            (basket_body([]), "empty")):
        try:
            trends.parse_basket_timeline(body, BASKET)
        except RuntimeError:
            pass
        else:
            raise AssertionError(f"{why} must not parse")


def test_the_single_query_parser_is_untouched_and_still_refuses_a_comparison():
    """The basket parser is written beside `parse_timeline`, not instead of it:
    that function's refusal of a multi-value row is what keeps every registered
    single-keyword series on its own 0-100 scale."""
    try:
        trends.parse_timeline(basket_body([(1785628800, [76, 60], False)]))
    except RuntimeError as e:
        assert "one keyword per request" in str(e), str(e)
    else:
        raise AssertionError("parse_timeline must still refuse two values")
    one = trends.parse_timeline(basket_body([(1785628800, [76], False)]))
    assert one[0]["value"] == 76


def test_the_basket_key_is_order_sensitive():
    """The order is the column order in every archived row and the round's
    declared tie-break, so two orders are two archives."""
    a = trends.basket_key(BASKET)
    b = trends.basket_key(list(reversed(BASKET)))
    assert a != b and a.startswith("basket.") and a.endswith(".geo-US")


def test_the_earliest_snapshot_carrying_a_week_wins_forever():
    with Archives() as ar:
        moved = dict(BASKET_WEEKS)
        moved["2026-08-15"] = {q: 1 for q in BASKET}
        ar.basket_snapshot("2026-08-18", sorted(BASKET_WEEKS), values=moved)
        weeks = {w["date"]: w["values"] for w in trends.basket_weeks(BASKET)}
        assert weeks["2026-08-15"] == BASKET_WEEKS["2026-08-15"], \
            "a later re-read must not move a completed week"


def test_an_in_progress_week_never_enters_the_history():
    with Archives() as ar:
        shutil.rmtree(trends.archive_dir(trends.basket_key(BASKET)))
        ar.basket_snapshot("2026-08-17", sorted(BASKET_WEEKS),
                           partial=("2026-08-15",))
        dates = [w["date"] for w in trends.basket_weeks(BASKET)]
        assert "2026-08-15" not in dates and "2026-08-08" in dates


def test_a_snapshot_whose_columns_are_another_basket_is_refused():
    """Reading one basket's archive positionally as another's would answer the
    round with the wrong ordering and look entirely normal."""
    with Archives() as ar:
        other = ["Tesla", "iPhone", "Samsung", "Netflix", "Ford"]
        vals = {d: {**v, "Ford": v.pop("Disney")}
                for d, v in {k: dict(x) for k, x in BASKET_WEEKS.items()}.items()}
        ar.basket_snapshot("2026-08-18", sorted(vals), values=vals,
                           queries=other)
        # written under the other basket's key, so this one is unaffected...
        assert [w["date"] for w in trends.basket_weeks(BASKET)]
        # ...and a hand-edited file under this key is caught
        key = trends.basket_key(BASKET)
        path = os.path.join(trends.archive_dir(key), "2026-08-17.json")
        snap = json.load(open(path))
        snap["queries"] = other
        with open(path, "w") as f:
            json.dump(snap, f)
        try:
            trends.basket_weeks(BASKET)
        except RuntimeError as e:
            assert "one basket's archive as another's" in str(e), str(e)
        else:
            raise AssertionError("a column mismatch must be refused")


def test_ties_in_the_index_fall_back_to_the_declared_basket_order():
    """0-100 integers over five queries tie often. The rule has to be fixed
    before the lock and equally unknown to everyone; the declared order is
    both, and it is written in the round the entrants read."""
    order = trends.basket_order({"Tesla": 50, "iPhone": 70, "Samsung": 50,
                                 "Netflix": 10, "Disney": 10}, BASKET)
    assert order == ["iPhone", "Tesla", "Samsung", "Netflix", "Disney"]
    try:
        trends.basket_order({"Tesla": 1}, BASKET)
    except ValueError as e:
        assert "missing 4 of 5" in str(e), str(e)
    else:
        raise AssertionError("a short week must not be ordered")


# --- the two losses ----------------------------------------------------------

def brute_rbo(pred, truth, p, d):
    total = sum((1 - p) * (p ** (i - 1)) * len(set(pred[:i]) & set(truth[:i])) / i
                for i in range(1, d + 1))
    return total / sum((1 - p) * (p ** (i - 1)) for i in range(1, d + 1))


def test_rank_biased_overlap_matches_a_brute_force_implementation():
    rng = random.Random(11)
    universe = [f"A{i}" for i in range(24)]
    for _ in range(300):
        truth = rng.sample(universe, 10)
        pred = rng.sample(universe, 10)
        for p in (0.5, 0.9, 0.98):
            assert abs(ranking_round.rbo_similarity(pred, truth, p, 10)
                       - brute_rbo(pred, truth, p, 10)) < 1e-12


def test_rbo_is_zero_loss_when_perfect_and_one_when_disjoint():
    ten = TITLES[:10]
    assert ranking_round.rbo_loss(ten, ten, 0.9, 10) == 0.0
    assert ranking_round.rbo_loss(TITLES[:10], [f"Z{i}" for i in range(10)],
                                  0.9, 10) == 1.0


def test_rbo_is_top_weighted_so_the_first_rank_costs_more_than_the_last():
    """The property that makes it the right measure for a top-N: an entrant that
    gets first place right and tenth wrong beats one with the same overlap the
    other way round."""
    truth = TITLES[:10]
    swap_top = [truth[1], truth[0]] + truth[2:]
    swap_bottom = truth[:8] + [truth[9], truth[8]]
    a = ranking_round.rbo_loss(swap_top, truth, 0.9, 10)
    b = ranking_round.rbo_loss(swap_bottom, truth, 0.9, 10)
    assert a > b > 0, (a, b)


def test_rbo_handles_lists_that_share_nothing_which_is_why_it_is_used():
    """Kendall and Spearman are undefined here. Seven of ten entries turn over
    in a typical real week, so this is the ordinary case, not the corner."""
    truth = TITLES[:10]
    half = TITLES[:5] + [f"Z{i}" for i in range(5)]
    got = ranking_round.rbo_loss(half, truth, 0.9, 10)
    assert 0.0 < got < 1.0, got


def test_kendall_counts_the_pairs_you_got_backwards():
    five = BASKET
    assert ranking_round.kendall_loss(five, five) == (0.0, 0, 10)
    assert ranking_round.kendall_loss(list(reversed(five)), five) == (1.0, 10, 10)
    one_swap = [five[1], five[0]] + five[2:]
    assert ranking_round.kendall_loss(one_swap, five) == (0.1, 1, 10)
    # a swap at the bottom costs exactly what a swap at the top costs: the
    # basket has no privileged position, and nothing about the question says
    # otherwise
    bottom = five[:3] + [five[4], five[3]]
    assert ranking_round.kendall_loss(bottom, five)[0] == 0.1


def test_kendall_refuses_two_lists_that_are_not_one_permutation():
    try:
        ranking_round.kendall_loss(["Tesla", "iPhone", "Ford"],
                                   ["Tesla", "iPhone", "Samsung"])
    except ValueError as e:
        assert "Samsung" in str(e) and "Ford" in str(e), str(e)
    else:
        raise AssertionError("a non-permutation must not be scored")


def test_skill_is_the_scalar_convention_verbatim():
    assert ranking_round.ranking_skill(0.2, 0.4) == 0.5
    assert ranking_round.ranking_skill(0.4, 0.4) == 0.0
    assert ranking_round.ranking_skill(0.8, 0.4) == -1.0
    # a perfect null gives everyone zero rather than an infinity or a crash
    assert ranking_round.ranking_skill(0.3, 0.0) == 0.0


# --- the round definition ----------------------------------------------------

def test_a_well_formed_round_produces_a_spec_for_each_kind():
    w = ranking_round.spec_for(WIKI_ROUND)
    assert w["kind"] == "wiki_top10" and w["length"] == 10 and w["rbo_p"] == 0.9
    assert w["exclusions"] == wikipedia.EXCLUSION_RULE_ID
    assert w["closed_set"] is False
    t = ranking_round.spec_for(TRENDS_ROUND)
    assert t["items"] == BASKET and t["loss"] == "kendall" and t["geo"] == "US"
    assert t["closed_set"] is True


def test_a_round_definition_that_could_never_be_scored_is_refused_at_build_time():
    def broken(base, **kw):
        r = dict(base)
        r["ranking"] = dict(base["ranking"], **kw)
        return r

    cases = [
        (broken(WIKI_ROUND, kind="nonsense"), "unknown ranking kind"),
        (broken(WIKI_ROUND, loss="kendall"), "scored with rbo"),
        (broken(WIKI_ROUND, length=1), "integer >= 2"),
        (broken(WIKI_ROUND, rbo_p=1.0), "rbo_p"),
        (broken(WIKI_ROUND, items=["A"]), "must not name `items`"),
        # a Sunday-to-Saturday week is seven days and still the wrong seven
        (broken(WIKI_ROUND, week_start="2026-08-09", week_end="2026-08-15"),
         "week starts on a Monday"),
        (broken(WIKI_ROUND, week_start="2026-08-03"), "days; a ranking week"),
        (broken(TRENDS_ROUND, items=BASKET[:4]), "basket holds 4"),
        (broken(TRENDS_ROUND, loss="rbo"), "scored with kendall"),
        (broken(TRENDS_ROUND, week_start="2026-08-10", week_end="2026-08-16"),
         "week starts on a Sunday"),
        (dict(WIKI_ROUND, ranking=None), "needs a `ranking` block"),
        (broken(WIKI_ROUND, exclusions="future_rule_v9"), "unknown Wikipedia"),
    ]
    for rnd, why in cases:
        try:
            ranking_round.spec_for(rnd)
        except ValueError as e:
            assert why in str(e), f"expected {why!r} in {e}"
        else:
            raise AssertionError(f"a round with {why} must not build")


# --- the freeze, the null and the resolution ---------------------------------

def test_the_null_is_the_last_completed_week_before_the_lock_never_the_answer():
    with Archives():
        obs = ranking_round.observations(WIKI_ROUND)
        assert [o["date"] for o in obs][-1] == "2026-08-16"
        hist = ranking_round.frozen_history(WIKI_ROUND, obs)
        assert all(o["date"] < "2026-08-07" for o in hist)
        assert hist[-1]["date"] == "2026-08-02"
        null = ranking_round.persistence_list(hist, ranking_round.spec_for(WIKI_ROUND))
        # the week before the lock, not the measured week: the answer is not
        # in the null
        assert null["items"] == TITLES[:10] != WIKI_WEEKS["2026-08-16"]

        obs = ranking_round.observations(TRENDS_ROUND)
        hist = ranking_round.frozen_history(TRENDS_ROUND, obs)
        assert [o["date"] for o in hist][-1] == "2026-08-01"
        null = ranking_round.persistence_list(
            hist, ranking_round.spec_for(TRENDS_ROUND))
        assert null["items"] == ["Tesla", "iPhone", "Samsung", "Netflix", "Disney"]


def test_a_round_with_no_pre_lock_week_has_no_denominator_and_says_so():
    with Archives():
        early = dict(WIKI_ROUND, lock_at="2026-01-01T00:00:00Z")
        obs = ranking_round.observations(early)
        try:
            ranking_round.ranking_baselines(early, obs)
        except ValueError as e:
            assert "no completed week before the lock" in str(e), str(e)
        else:
            raise AssertionError("a round with no history must refuse a null")


def test_resolution_reads_the_round_s_own_week_from_the_archive():
    with Archives():
        got = ranking_round.resolution(WIKI_ROUND)
        assert got["items"] == WIKI_WEEKS["2026-08-16"]
        assert got["week_start"] == "2026-08-10" and got["week_end"] == "2026-08-16"
        assert len(got["views"]) == 10

        got = ranking_round.resolution(TRENDS_ROUND)
        assert got["items"] == ["iPhone", "Tesla", "Samsung", "Disney", "Netflix"]
        assert got["index"] == BASKET_WEEKS["2026-08-15"]


def test_resolution_refuses_a_missing_week_and_a_week_that_predates_the_freeze():
    with Archives():
        os.remove(wikipedia.top_archive_path(wikipedia.top_archive_key(),
                                             "2026-08-14"))
        try:
            ranking_round.resolution(WIKI_ROUND)
        except RuntimeError as e:
            assert "2026-08-14" in str(e), str(e)
        else:
            raise AssertionError("a missing day must refuse the resolution")

    with Archives():
        late = dict(WIKI_ROUND, lock_at="2026-08-20T14:00:00Z")
        try:
            ranking_round.resolution(late)
        except ValueError as e:
            assert "before the freeze" in str(e), str(e)
        else:
            raise AssertionError("a week that ended before the lock cannot be scored")

    with Archives():
        missing = dict(TRENDS_ROUND)
        missing["ranking"] = dict(TRENDS_ROUND["ranking"],
                                  week_start="2026-08-16", week_end="2026-08-22")
        try:
            ranking_round.resolution(missing)
        except ValueError as e:
            assert "2026-08-22" in str(e), str(e)
        else:
            raise AssertionError("an unarchived week must refuse the resolution")


# --- what a submission may say ----------------------------------------------

def test_a_submitted_list_is_canonicalized_but_never_corrected():
    w = ranking_round.spec_for(WIKI_ROUND)
    loose = ["roblox"] + TITLES[1:10]
    assert ranking_round.normalize(loose, w)[0] == "Roblox"
    t = ranking_round.spec_for(TRENDS_ROUND)
    assert ranking_round.normalize(["TESLA", "iphone", "Samsung", "Netflix",
                                    "Disney"], t) == BASKET
    # a near-miss is a different article, not a typo to fix: an open-set round
    # takes any title, and turning Roblux into Roblox would score an entrant on
    # a pick it did not make
    assert ranking_round.normalize(["Roblux"] + TITLES[1:10], w)[0] == "Roblux"


def test_the_shapes_a_ranking_answer_may_not_take():
    w = ranking_round.spec_for(WIKI_ROUND)
    t = ranking_round.spec_for(TRENDS_ROUND)
    for items, spec, why in (
            (TITLES[:9], w, "exactly 10"),
            (TITLES[:11], w, "exactly 10"),
            (["Roblox"] * 10, w, "each item once"),
            (["Main_Page"] + TITLES[1:10], w, "excluded"),
            (["Special:Search"] + TITLES[1:10], w, "excluded"),
            ([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], w, "string"),
            ("not a list", w, "JSON array"),
            (BASKET[:4], t, "exactly 5"),
            (["Tesla", "iPhone", "Samsung", "Netflix", "Ford"], t, "not one of"),
            (["Tesla"] * 5, t, "each item once")):
        try:
            ranking_round.normalize(items, spec)
        except ValueError as e:
            assert why in str(e), f"expected {why!r} in {e}"
        else:
            raise AssertionError(f"{why} must be refused: {items!r}")


# --- the schema --------------------------------------------------------------

def load_schema():
    with open(os.path.join(ROOT, "schema", "forecast.schema.json")) as f:
        return json.load(f)


def accepts(doc):
    import jsonschema
    try:
        jsonschema.validate(doc, load_schema())
        return True
    except Exception:
        return False


def test_the_schema_takes_a_ranking_and_still_takes_the_other_two():
    base = {"round_id": "wiki-top10-test", "entrant": "some-model"}
    assert accepts({**base, "ranking": TITLES[:10]})
    assert accepts({**base, "ranking": BASKET})
    assert accepts({**base, "topline": {"mean": 41.0, "sd": 2.0}})
    assert accepts({**base, "profile": {"civiqs_net_approval_dem": {"mean": 1.0, "sd": 1.0},
                                        "civiqs_net_approval_ind": {"mean": 2.0, "sd": 1.0}}})


def test_the_schema_refuses_the_ranking_shapes_that_would_not_score():
    base = {"round_id": "wiki-top10-test", "entrant": "some-model"}
    # exactly one answer, and the schema is still closed
    assert not accepts({**base, "ranking": BASKET,
                        "topline": {"mean": 1.0, "sd": 1.0}})
    assert not accepts({**base, "ranking": BASKET, "extra": 1})
    # a list of one, a repeat, a non-string, a bare string
    assert not accepts({**base, "ranking": ["Tesla"]})
    assert not accepts({**base, "ranking": ["Tesla", "Tesla"]})
    assert not accepts({**base, "ranking": ["Tesla", 3]})
    assert not accepts({**base, "ranking": "Tesla,iPhone"})


# --- the validator -----------------------------------------------------------

def load_validator():
    path = os.path.join(ROOT, "tools", "validate_submission.py")
    spec = importlib.util.spec_from_file_location("validate_submission_rank", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeRepo:
    """A throwaway repo root the validator can be pointed at."""

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="ssa-validate-rank-")
        os.makedirs(os.path.join(self.dir, "questions"))
        os.makedirs(os.path.join(self.dir, "schema"))
        shutil.copy(os.path.join(ROOT, "schema", "forecast.schema.json"),
                    os.path.join(self.dir, "schema", "forecast.schema.json"))
        scalar = {"round_id": "wiki-scalar-test", "tracker": "wikipedia",
                  "series": "wiki_views_trump", "question": "q", "unit": "u",
                  "release_at": "2099-01-03T22:00:00Z", "release_estimated": False,
                  "lock_at": "2099-01-01T22:00:00Z", "resolve": "r",
                  "target_type": "continuous_normal"}
        rounds = [scalar]
        for base in (WIKI_ROUND, TRENDS_ROUND):
            rounds.append(dict(base, release_at="2099-01-03T22:00:00Z",
                               lock_at="2099-01-01T22:00:00Z"))
        with open(os.path.join(self.dir, "questions", "season0.json"), "w") as f:
            json.dump({"season": 0, "rounds": rounds}, f)
        self.vs = load_validator()
        self.vs.ROOT = self.dir
        return self

    def __exit__(self, *a):
        shutil.rmtree(self.dir, ignore_errors=True)

    def check(self, round_id, entrant, body):
        """(ok, message). The validator exits rather than returning."""
        d = os.path.join(self.dir, "forecasts", round_id)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, entrant + ".json")
        with open(p, "w") as f:
            json.dump(body, f)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                self.vs.validate(p)
        except SystemExit:
            return False, buf.getvalue()
        return True, buf.getvalue()


def test_the_validator_pairs_each_answer_with_its_own_round_type():
    with FakeRepo() as repo:
        ok, msg = repo.check("wiki-top10-test", "good",
                             {"round_id": "wiki-top10-test", "entrant": "good",
                              "ranking": TITLES[:10]})
        assert ok, msg
        assert "sha256" in msg, msg

        ok, msg = repo.check("wiki-top10-test", "scalar",
                             {"round_id": "wiki-top10-test", "entrant": "scalar",
                              "topline": {"mean": 300.0, "sd": 40.0}})
        assert not ok and "ranking round" in msg, msg

        ok, msg = repo.check("wiki-scalar-test", "lister",
                             {"round_id": "wiki-scalar-test", "entrant": "lister",
                              "ranking": TITLES[:10]})
        assert not ok and "not a ranking round" in msg, msg


def test_the_validator_holds_a_list_to_the_rounds_own_ranking_block():
    with FakeRepo() as repo:
        for entrant, items, why in (
                ("short", TITLES[:9], "exactly 10"),
                ("long", TITLES[:11], "exactly 10"),
                # a repeat the JSON schema's uniqueItems cannot see, because
                # the two spellings are one article only after canonicalization
                ("dupe", ["roblox"] + TITLES[:9], "each item once"),
                ("frontdoor", ["Main_Page"] + TITLES[1:10], "excluded"),
                ("namespace", ["Help:Contents"] + TITLES[1:10], "excluded")):
            ok, msg = repo.check("wiki-top10-test", entrant,
                                 {"round_id": "wiki-top10-test",
                                  "entrant": entrant, "ranking": items})
            assert not ok and why in msg, (entrant, msg)

        # the basket round is a permutation, not a free choice
        ok, msg = repo.check("trends-basket-test", "outsider",
                             {"round_id": "trends-basket-test",
                              "entrant": "outsider",
                              "ranking": ["Tesla", "iPhone", "Samsung",
                                          "Netflix", "Ford"]})
        assert not ok and "not in round" in msg, msg
        # ...and its case does not have to match
        ok, msg = repo.check("trends-basket-test", "shouty",
                             {"round_id": "trends-basket-test",
                              "entrant": "shouty",
                              "ranking": ["TESLA", "IPHONE", "SAMSUNG",
                                          "NETFLIX", "DISNEY"]})
        assert ok, msg


def test_the_validator_still_enforces_the_lock_on_a_ranking_round():
    with FakeRepo() as repo:
        past = dict(WIKI_ROUND)
        with open(os.path.join(repo.dir, "questions", "season0.json")) as f:
            season = json.load(f)
        season["rounds"] = [past if r["round_id"] == past["round_id"] else r
                            for r in season["rounds"]]
        with open(os.path.join(repo.dir, "questions", "season0.json"), "w") as f:
            json.dump(season, f)
        ok, msg = repo.check("wiki-top10-test", "late",
                             {"round_id": "wiki-top10-test", "entrant": "late",
                              "ranking": TITLES[:10]})
        assert not ok and "locked at" in msg, msg


# --- the harness parse -------------------------------------------------------

def test_a_reply_parses_from_an_object_a_bare_array_or_prose_around_either():
    spec = ranking_round.spec_for(TRENDS_ROUND)
    want = ["iPhone", "Tesla", "Samsung", "Disney", "Netflix"]
    for text in (json.dumps({"ranking": want}),
                 json.dumps(want),
                 "Here you go:\n```json\n" + json.dumps({"ranking": want}) + "\n```\n",
                 "My answer is " + json.dumps(want) + " -- hope that helps.",
                 '{"reasoning": "iPhone launch week", "ranking": '
                 + json.dumps(want) + "}"):
        assert harness.parse_ranking(text, spec) == want, text[:40]


def test_junk_short_lists_and_inventions_are_refused_loudly():
    spec = ranking_round.spec_for(TRENDS_ROUND)
    for text in ("I'm afraid I can't help with that.",
                 "",
                 "{",
                 '{"mean": 41.5, "sd": 2}',
                 json.dumps({"ranking": ["Tesla", "iPhone"]}),
                 json.dumps({"ranking": ["Tesla", "iPhone", "Samsung",
                                         "Netflix", "Ford"]}),
                 json.dumps({"ranking": ["Tesla"] * 5}),
                 json.dumps({"ranking": [1, 2, 3, 4, 5]})):
        try:
            harness.parse_ranking(text, spec)
        except ValueError:
            pass
        else:
            raise AssertionError(f"junk parsed: {text[:60]}")


def test_the_other_two_parsers_are_untouched():
    assert harness.parse_forecast('{"mean": 41.5, "sd": 2}') == \
        {"mean": 41.5, "sd": 2.0}
    from ssa import profile_round
    cells = list(profile_round.CELLS)
    reply = json.dumps({c: {"mean": -20.0, "sd": 3.0} for c in cells})
    assert len(harness.parse_profile(reply, cells)) == len(cells)


# --- the harness call --------------------------------------------------------

class Scratch:
    """Temp forecasts/, locks/ and reply log for the duration."""

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="ssa-ranking-fc-")
        self.saved = (refresh.FORECASTS, refresh.LOCKS,
                      os.environ.get("SSA_REPLIES_DIR"), harness.ALLOW_MOCK)
        refresh.FORECASTS = os.path.join(self.dir, "forecasts")
        refresh.LOCKS = os.path.join(self.dir, "locks")
        os.environ["SSA_REPLIES_DIR"] = os.path.join(self.dir, "replies")
        harness.ALLOW_MOCK = False
        return self

    def __exit__(self, *a):
        (refresh.FORECASTS, refresh.LOCKS, log, harness.ALLOW_MOCK) = self.saved
        if log is None:
            os.environ.pop("SSA_REPLIES_DIR", None)
        else:
            os.environ["SSA_REPLIES_DIR"] = log
        shutil.rmtree(self.dir, ignore_errors=True)

    def file(self, round_id, entrant, body):
        d = os.path.join(refresh.FORECASTS, round_id)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, entrant + ".json"), "w") as f:
            json.dump(body, f)


class Keys:
    NAMES = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPEN_ROUTER")

    def __init__(self, **present):
        self.present = present

    def __enter__(self):
        self.saved = {k: os.environ.get(k) for k in self.NAMES}
        for k in self.NAMES:
            os.environ.pop(k, None)
        os.environ.update(self.present)
        return self

    def __exit__(self, *a):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class Provider:
    """Stands in for call_provider and counts what it was asked."""

    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def __call__(self, entrant, prompt, with_usage=False, context=None, via=None):
        self.prompts.append(prompt)
        text = self.reply(prompt) if callable(self.reply) else self.reply
        usage = {"input_tokens": 700, "output_tokens": 120}
        return (text, usage) if with_usage else text

    def __enter__(self):
        harness.forget_dead_routes()
        self.saved = harness.call_provider
        harness.call_provider = self
        return self

    def __exit__(self, *a):
        harness.call_provider = self.saved
        harness.forget_dead_routes()


TRUE_BASKET = json.dumps({"ranking": ["iPhone", "Tesla", "Samsung", "Disney",
                                      "Netflix"]})


def frozen(round_def):
    return ranking_round.frozen_history(
        round_def, ranking_round.observations(round_def))


def test_one_call_buys_the_list_and_the_hash_stops_the_second():
    with Archives(), Scratch(), Keys(ANTHROPIC_API_KEY="k"), \
            Provider(TRUE_BASKET) as prov:
        hist = frozen(TRENDS_ROUND)
        body = harness.forecast("claude-opus", TRENDS_ROUND, ranking_history=hist)
        assert len(prov.prompts) == 1
        assert body["ranking"][0] == "iPhone" and len(body["ranking"]) == 5
        assert "topline" not in body and "profile" not in body
        assert "in=" in body["notes"] and "filed=" in body["notes"]
        assert "ranking 5 items" in body["notes"]

        again = harness.forecast("claude-opus", TRENDS_ROUND,
                                 ranking_history=hist, previous=body)
        assert again == body
        assert len(prov.prompts) == 1, "a cached ranking round must cost nothing"


def test_the_prompt_carries_the_week_the_items_and_the_exclusions():
    with Archives(), Scratch(), Keys(ANTHROPIC_API_KEY="k"), \
            Provider(TRUE_BASKET) as prov:
        harness.forecast("claude-opus", TRENDS_ROUND,
                         ranking_history=frozen(TRENDS_ROUND))
        p = prov.prompts[0]
        for q in BASKET:
            assert q in p, q
        assert "2026-08-09" in p and "2026-08-15" in p
        assert "exactly 5 items" in p
        assert "week ending 2026-08-01" in p, "the pre-lock weeks are shown"
        assert "week ending 2026-08-15" not in p, \
            "the measured week is never in the history an entrant is shown"

    with Archives(), Scratch(), Keys(ANTHROPIC_API_KEY="k"), Provider(
            json.dumps({"ranking": WIKI_WEEKS["2026-08-16"]})) as prov:
        harness.forecast("claude-opus", WIKI_ROUND,
                         ranking_history=frozen(WIKI_ROUND))
        p = prov.prompts[0]
        assert "Main_Page" in p and "Special:" in p
        assert "Spider-Man:_Brand_New_Day is an article and is eligible" in p
        assert "exactly 10 items" in p


def test_a_ranking_round_is_never_mocked_even_with_mock_enabled():
    """The scalar path may file a labelled placeholder without keys. Here the
    only placeholder available is last week's list, which is a competitive
    answer with no visible seam."""
    with Archives(), Scratch(), Keys():
        harness.ALLOW_MOCK = True
        try:
            try:
                harness.forecast("claude-opus", TRENDS_ROUND,
                                 ranking_history=frozen(TRENDS_ROUND))
            except RuntimeError as e:
                assert "never mocked" in str(e), str(e)
            else:
                raise AssertionError("a keyless ranking round must raise")
        finally:
            harness.ALLOW_MOCK = False


def test_a_bad_reply_is_a_failure_not_a_filed_forecast():
    with Archives(), Scratch(), Keys(ANTHROPIC_API_KEY="k"), Provider(
            json.dumps({"ranking": ["Tesla", "iPhone"]})):
        try:
            harness.forecast("claude-opus", TRENDS_ROUND,
                             ranking_history=frozen(TRENDS_ROUND))
        except RuntimeError as e:
            assert "exactly 5" in str(e), str(e)
        else:
            raise AssertionError("a two-item reply must not be filed")


def test_the_reply_log_replays_a_ranking_without_paying_again():
    with Archives(), Scratch(), Keys(ANTHROPIC_API_KEY="k"):
        hist = frozen(TRENDS_ROUND)
        with Provider(TRUE_BASKET) as prov:
            harness.forecast("claude-opus", TRENDS_ROUND, ranking_history=hist)
            assert len(prov.prompts) == 1
        with Provider(lambda p: (_ for _ in ()).throw(
                AssertionError("must not call the provider"))) as prov:
            body = harness.forecast("claude-opus", TRENDS_ROUND,
                                    ranking_history=hist)
            assert prov.prompts == []
            assert "replayed from the reply log" in body["notes"]
            assert len(body["ranking"]) == 5


# --- the freeze, the resolution and the board through refresh ----------------

def season(*rounds):
    return {"season": 0, "rounds": list(rounds)}


def obs_for(*rounds):
    return {r["round_id"]: ranking_round.observations(r) for r in rounds}


def test_build_rounds_attaches_the_block_and_clears_the_scalar_null():
    with Archives(), Scratch():
        now = refresh.parse_iso("2026-08-18T00:00:00Z")
        rows, hist = refresh.build_rounds(season(WIKI_ROUND, TRENDS_ROUND),
                                          {}, {}, now,
                                          obs_for(WIKI_ROUND, TRENDS_ROUND))
        for row in rows:
            assert row["baselines"] is None, "a list round has no scalar null"
            assert row["scoreable"] is True
            assert row["ranking"]["baselines"]["persistence"]["items"]
            assert hist[row["round_id"]] == []
        assert rows[0]["ranking"]["length"] == 10
        assert rows[1]["ranking"]["items"] == BASKET
        # ...and no empty lock snapshot is left behind claiming a freeze
        assert not glob.glob(os.path.join(refresh.LOCKS, "*.json"))


def test_a_round_whose_sources_cannot_answer_is_named_not_crashed():
    """One unreachable source must not stop a refresh: every other round's
    forecasts are unfiled and their locks are still coming."""
    with Archives(), Scratch():
        now = refresh.parse_iso("2026-08-18T00:00:00Z")
        rows, _ = refresh.build_rounds(season(TRENDS_ROUND), {}, {}, now,
                                       {TRENDS_ROUND["round_id"]: []})
        assert rows[0]["scoreable"] is False
        assert "no completed week before the lock" in rows[0]["baseline_note"]
        assert rows[0]["ranking"]["baselines"] is None
        # and the loader itself swallows a dead source rather than raising
        shutil.rmtree(trends.ARCHIVE)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            got = refresh.ranking_observations(season(TRENDS_ROUND), fetch=False)
        assert got == {TRENDS_ROUND["round_id"]: []}
        assert "no observations" in buf.getvalue()


def test_the_filed_null_is_a_list_in_the_submission_format():
    """The null is scored by exactly the code an entrant's file goes through, so
    it has to be filed in exactly the shape an entrant files."""
    with Archives(), Scratch():
        open_round = dict(TRENDS_ROUND, lock_at="2099-01-01T00:00:00Z",
                          release_at="2099-01-03T00:00:00Z")
        now = refresh.parse_iso("2026-08-18T00:00:00Z")
        rows, hist = refresh.build_rounds(season(open_round), {}, {}, now,
                                          obs_for(open_round))
        assert rows[0]["status"] == "open"
        written, failures = refresh.file_baseline_forecasts(
            rows, hist, now, {}, obs_for(open_round))
        paths = glob.glob(os.path.join(refresh.FORECASTS,
                                       open_round["round_id"], "*.json"))
        assert paths, (written, failures)
        assert os.path.basename(paths[0]) == "persistence.json"
        with open(paths[0]) as f:
            body = json.load(f)
        null = rows[0]["ranking"]["baselines"]["persistence"]["items"]
    assert "topline" not in body and body["ranking"] == null
    assert sorted(body["ranking"]) == sorted(BASKET)
    assert accepts(body), "the filed null must satisfy the submission schema"


def test_the_board_scores_the_list_and_ranks_the_better_one_first():
    with Archives(), Scratch() as sc:
        now = refresh.parse_iso("2026-08-18T00:00:00Z")
        rows, _ = refresh.build_rounds(season(TRENDS_ROUND), {}, {}, now,
                                       obs_for(TRENDS_ROUND))
        truth = ["iPhone", "Tesla", "Samsung", "Disney", "Netflix"]
        sc.file(TRENDS_ROUND["round_id"], "sharp",
                {"round_id": TRENDS_ROUND["round_id"], "entrant": "sharp",
                 "ranking": truth})
        sc.file(TRENDS_ROUND["round_id"], "backwards",
                {"round_id": TRENDS_ROUND["round_id"], "entrant": "backwards",
                 "ranking": list(reversed(truth))})
        sc.file(TRENDS_ROUND["round_id"], "persistence",
                {"round_id": TRENDS_ROUND["round_id"], "entrant": "persistence",
                 "ranking": BASKET})
        board = refresh.build_ranking_leaderboard(rows, {},
                                                  obs_for(TRENDS_ROUND))

    assert board["scored_rounds"] == 1, board["skipped"]
    rnd = board["rounds"][0]
    assert rnd["outcome"] == truth
    assert rnd["persistence_loss"] == 0.2, rnd
    entries = {e["entrant"]: e for e in rnd["entries"]}
    assert entries["sharp"]["loss"] == 0.0 and entries["sharp"]["skill"] == 1.0
    assert entries["backwards"]["loss"] == 1.0
    assert entries["backwards"]["skill"] == -4.0
    assert entries["persistence"]["skill"] == 0.0
    assert entries["sharp"]["discordant_pairs"] == 0
    assert entries["sharp"]["exact_positions"] == 5
    assert board["board"][0]["entrant"] == "sharp"
    assert {e["entrant"] for e in board["matched"]} == set(entries)


def test_the_wiki_board_uses_rank_biased_overlap_and_the_frozen_null():
    with Archives(), Scratch() as sc:
        now = refresh.parse_iso("2026-08-18T00:00:00Z")
        rows, _ = refresh.build_rounds(season(WIKI_ROUND), {}, {}, now,
                                       obs_for(WIKI_ROUND))
        truth = WIKI_WEEKS["2026-08-16"]
        sc.file(WIKI_ROUND["round_id"], "sharp",
                {"round_id": WIKI_ROUND["round_id"], "entrant": "sharp",
                 "ranking": truth})
        # the same ten titles as the truth, in the pre-lock week's order
        sc.file(WIKI_ROUND["round_id"], "persistence",
                {"round_id": WIKI_ROUND["round_id"], "entrant": "persistence",
                 "ranking": TITLES[:10]})
        sc.file(WIKI_ROUND["round_id"], "elsewhere",
                {"round_id": WIKI_ROUND["round_id"], "entrant": "elsewhere",
                 "ranking": [f"Z{i}" for i in range(10)]})
        board = refresh.build_ranking_leaderboard(rows, {}, obs_for(WIKI_ROUND))

    rnd = board["rounds"][0]
    assert rnd["loss_rule"] == "rbo" and rnd["kind"] == "wiki_top10"
    # the null is the pre-lock week, and it is wrong in exactly the two ways
    # the fixture makes it wrong
    assert rnd["persistence_items"] == TITLES[:10]
    assert 0 < rnd["persistence_loss"] < 1
    entries = {e["entrant"]: e for e in rnd["entries"]}
    assert entries["sharp"]["loss"] == 0.0 and entries["sharp"]["overlap"] == 10
    assert entries["elsewhere"]["loss"] == 1.0 and entries["elsewhere"]["overlap"] == 0
    assert entries["persistence"]["skill"] == 0.0
    assert entries["sharp"]["skill"] == 1.0


def test_a_malformed_submission_is_named_and_excluded_never_repaired():
    with Archives(), Scratch() as sc:
        now = refresh.parse_iso("2026-08-18T00:00:00Z")
        rows, _ = refresh.build_rounds(season(TRENDS_ROUND), {}, {}, now,
                                       obs_for(TRENDS_ROUND))
        rid = TRENDS_ROUND["round_id"]
        sc.file(rid, "good", {"round_id": rid, "entrant": "good",
                              "ranking": BASKET})
        sc.file(rid, "short", {"round_id": rid, "entrant": "short",
                               "ranking": BASKET[:4]})
        sc.file(rid, "scalar", {"round_id": rid, "entrant": "scalar",
                                "topline": {"mean": 40.0, "sd": 5.0}})
        board = refresh.build_ranking_leaderboard(rows, {}, obs_for(TRENDS_ROUND))

    named = {e["entrant"] for e in board["rounds"][0]["entries"]}
    assert named == {"good"}, named
    why = " ".join(s[1] for s in board["skipped"])
    assert "exactly 5" in why and "needs a `ranking` list" in why


def test_an_explicit_resolution_wins_over_the_recomputed_one():
    with Archives(), Scratch() as sc:
        now = refresh.parse_iso("2026-08-18T00:00:00Z")
        rid = TRENDS_ROUND["round_id"]
        rows, _ = refresh.build_rounds(season(TRENDS_ROUND), {}, {}, now,
                                       obs_for(TRENDS_ROUND))
        sc.file(rid, "good", {"round_id": rid, "entrant": "good",
                              "ranking": BASKET})
        hand = {rid: {"items": list(reversed(BASKET)),
                      "method": "hand-checked against the Trends UI"}}
        board = refresh.build_ranking_leaderboard(rows, hand, obs_for(TRENDS_ROUND))
    rnd = board["rounds"][0]
    assert rnd["outcome"] == list(reversed(BASKET))
    assert rnd["resolution"]["source"] == "resolved.json"


def test_a_ranking_round_is_refused_by_the_scalar_resolver():
    """Without the refusal it would take the 'no series in the pipeline' path
    and be reported as awaiting a human on every single run."""
    from ssa import resolve as resolver
    now = refresh.parse_iso("2026-08-18T00:00:00Z")
    res, why = resolver.resolve_round(WIKI_ROUND, {}, now)
    assert res is None and "ranking round" in why, why
    new, _ = resolver.resolve_all(season(WIKI_ROUND, TRENDS_ROUND), {}, {}, now)
    assert new == {}


def test_the_board_publishes_nothing_when_no_round_is_due():
    with Archives(), Scratch():
        now = refresh.parse_iso("2026-08-18T00:00:00Z")
        future = dict(TRENDS_ROUND, release_at="2099-01-03T00:00:00Z",
                      lock_at="2099-01-01T00:00:00Z")
        rows, _ = refresh.build_rounds(season(future), {}, {}, now,
                                       obs_for(future))
        board = refresh.build_ranking_leaderboard(rows, {}, obs_for(future))
    assert board["scored_rounds"] == 0 and board["rounds"] == []
    assert board["board"] == [] and board["skipped"] == []


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
