"""Which rows of the Silver Bulletin file a series is allowed to read.

Run: PYTHONPATH=. python tests/test_silverbulletin.py

Both properties here exist because a publisher changed something about its own
reporting without changing what it measured, and the registry had to be able
to say so. On 2026-09-08 Economist/YouGov moved its whole wave from an adult
base to a registered-voter one: the `Voters` cut it had filed the RV reading
under stopped, the headline `All polls` cut became RV, and cuts like `Strong`
went with it -- leaving one stray 2025 row on the new base and then nineteen
months of nothing.

So a series can name more than one subgroup label, and it can name the day a
cut became the published one. Neither widens which *population* is read, which
is the line that must not move: the arena refuses to answer an adults question
with a voters number.
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa.adapters import silverbulletin as sb


def row(subgroup, population, end, approve, pollster="YouGov",
        sponsors="Economist"):
    """One poll row in the file's own shape. `population` keeps the leading
    space the file really carries -- the adapter strips it, and a test that
    tidied it up would stop covering that."""
    return {"subgroup": subgroup, "pollster": pollster, "sponsors": sponsors,
            "population": " " + population, "startdate": end, "enddate": end,
            "approve": str(approve), "disapprove": "50", "net": "0",
            "samplesize": "1500"}


ROWS = [
    row("All polls", "A", "08/29/2026", 36),      # the adult headline, stopped
    row("Voters", "RV", "08/29/2026", 38),        # the RV cut, stopped
    row("All polls", "RV", "09/06/2026", 37),     # the RV headline, from here
    row("All polls", "RV", "09/19/2026", 35),
    row("Strong", "RV", "02/03/2025", 22),        # the stray
    row("Strong", "RV", "09/06/2026", 19),
    row("Strong", "RV", "09/19/2026", 18),
]


def dates(recs):
    return sorted(r["date"] for r in recs)


def test_a_series_may_name_more_than_one_subgroup_label():
    """`yougov_rv_approval` is one measurement under two labels: 82 waves as
    `Voters`, then `All polls` once the headline itself went RV. Reading only
    the old label ended the series while the publisher kept publishing it."""
    one = sb.approval_polls(subgroup="Voters", pollster="YouGov",
                            population="RV", rows=ROWS, sponsor="Economist")
    assert dates(one) == [dt.date(2026, 8, 29)]

    both = sb.approval_polls(subgroup=("Voters", "All polls"),
                             pollster="YouGov", population="RV", rows=ROWS,
                             sponsor="Economist")
    assert dates(both) == [dt.date(2026, 8, 29), dt.date(2026, 9, 6),
                           dt.date(2026, 9, 19)]


def test_naming_two_labels_does_not_widen_the_population():
    """The adult headline is filed under one of those same labels. If the
    widening leaked into the population filter it would splice the two bases,
    and the RV reading runs about three points above the adult one -- 82
    overlapping waves, mean +2.87, sd 1.26."""
    recs = sb.approval_polls(subgroup=("Voters", "All polls"),
                             pollster="YouGov", population="RV", rows=ROWS,
                             sponsor="Economist")
    assert 36 not in [r["approve"] for r in recs], \
        "the adult-base headline was read as a registered-voter observation"


def test_since_drops_waves_from_before_a_cut_became_the_published_one():
    """`Strong` on the RV base carries one 2025 row from a week the publisher
    happened to report it, then nothing until 2026-09-06. Three points behind
    a nineteen-month hole is a series persistence survives and trend and ewma
    do not, so the floor is stated rather than inferred."""
    loose = sb.approval_polls(subgroup="Strong", pollster="YouGov",
                              population="RV", rows=ROWS, sponsor="Economist")
    assert dates(loose)[0] == dt.date(2025, 2, 3), "the stray should be there"
    assert len(loose) == 3

    floored = sb.approval_polls(subgroup="Strong", pollster="YouGov",
                                population="RV", rows=ROWS,
                                sponsor="Economist", since="09/06/2026")
    assert dates(floored) == [dt.date(2026, 9, 6), dt.date(2026, 9, 19)]


def test_the_floor_is_inclusive_of_its_own_day():
    """The floor names the wave the cut became published, so that wave is in
    the series rather than the first casualty of the rule."""
    recs = sb.approval_polls(subgroup="Strong", pollster="YouGov",
                             population="RV", rows=ROWS, sponsor="Economist",
                             since="09/06/2026")
    assert dt.date(2026, 9, 6) in dates(recs)


def test_the_registered_series_read_what_they_say_they_read():
    """The two filters above are set on real series, and a registry that
    silently lost one would leave a round pointing at nothing."""
    from ssa import series as S
    rv = S.SERIES["yougov_rv_approval"]["filters"]
    assert rv["subgroup"] == ("Voters", "All polls"), rv
    assert rv["population"] == "RV", rv

    strong = S.SERIES["yougov_rv_strong_approval"]["filters"]
    assert strong["population"] == "RV" and strong["since"] == "09/06/2026", strong

    # And the adult-base series it sits beside is left exactly as it was, for
    # the rounds that locked against it.
    old = S.SERIES["yougov_strong_approval"]["filters"]
    assert old["population"] == "A" and "since" not in old, old


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_")]
    for test in tests:
        test()
        print("ok", test.__name__)
    print(f"{len(tests)} passed")
