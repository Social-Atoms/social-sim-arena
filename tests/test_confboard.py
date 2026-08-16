"""The Conference Board release paragraph. No network.

Run: PYTHONPATH=. python tests/test_confboard.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa.adapters import confboard

# The 2026-07 release, as the page actually carries it.
PAGE = (
    '<p><em><strong>Consumer assessments of the present situation softened'
    '</strong></em></p> <p>The Conference Board <strong><em>Consumer '
    'Confidence Index<sup>&reg;</sup></em></strong> decreased by 1.4 points '
    'to 90.8 (1985=100) in July, down from an upwardly revised 92.2 in June. '
    'The <strong><em>Present Situation Index</em></strong>&mdash;based on '
    'consumers&rsquo; assessment of current business and labor market '
    'conditions&mdash;fell by 3.6 points to 114.9, its third consecutive '
    'monthly decline. The <strong><em>Expectations Index</em></strong>&mdash;'
    'based on consumers&rsquo; short-term outlook&mdash;remained unchanged at '
    '74.7. The survey period was July 1&ndash;22.</p>'
)


def test_the_whole_release_comes_out_of_one_paragraph():
    got = confboard.parse(PAGE, year=2026)
    assert got == {
        "month": "2026-07-01", "value": 90.8, "change": -1.4,
        "present_situation": 114.9, "expectations": 74.7,
        "previous_month": "2026-06-01", "previous_value_restated": 92.2,
        "released_on": None}, got


# Three house styles in five years, all still published at the same URL. Each
# moves the pieces somewhere else, and a parser that knew only the newest one
# refused every archived page -- which looked exactly like "the page never
# carried the number", not like a bug.
VINTAGE_2022 = (
    "US Consumer Confidence Declined Again in November "
    "Latest Press Release Updated: Tuesday, November 29, 2022 "
    "The Conference Board <em>Consumer Confidence Index</em> &reg; decreased "
    "in November after also losing ground in October. The Index now stands at "
    "100.2 (1985=100), down from 102.2 in October. The <em>Present Situation "
    "Index</em>&mdash;based on consumers&rsquo; assessment&mdash;decreased to "
    "137.4 from 138.7 last month. The <em>Expectations Index</em>&mdash;based "
    "on the short-term outlook&mdash;declined to 75.4 from 77.9.")

VINTAGE_2024 = (
    "US Consumer Confidence Improved Again in November "
    "Latest Press Release Updated: Tuesday, November 26, 2024 "
    "The Conference Board <em>Consumer Confidence Index</em>&reg; increased in "
    "November to 111.7 (1985=100), up 2.1 points from 109.6 in October. The "
    "<em>Present Situation Index</em>&mdash;based on consumers&rsquo; "
    "assessment&mdash;increased by 4.8 points to 140.9.")


def test_every_house_style_the_page_has_used_still_reads():
    """2022 puts the level in a *later sentence* beside a second month that is
    not the reporting one; 2024 puts the month *before* the level; 2026 puts it
    *after*. Verified against the archived pages, and every value here matches
    what the Conference Board actually published."""
    a = confboard.parse(VINTAGE_2022)
    assert a["month"] == "2022-11-01", a
    assert (a["value"], a["previous_value_restated"]) == (100.2, 102.2), a
    assert a["released_on"] == "2022-11-29"
    assert (a["present_situation"], a["expectations"]) == (137.4, 75.4), a

    b = confboard.parse(VINTAGE_2024)
    assert b["month"] == "2024-11-01", b
    assert (b["value"], b["previous_value_restated"]) == (111.7, 109.6), b
    assert b["change"] == 2.1, b
    assert b["released_on"] == "2024-11-26"


def test_the_month_is_derived_from_the_month_it_compares_itself_to():
    """Reading it out of the text gets two of the three vintages wrong: 2022
    names October in the same breath as November and means November. A release
    always compares itself to the month immediately before, and that clause is
    unambiguous, so the reporting month is derived from it."""
    for page, want in ((VINTAGE_2022, "2022-11-01"), (VINTAGE_2024, "2024-11-01"),
                       (PAGE, "2026-07-01")):
        assert confboard.parse(page, year=2026)["month"] == want


def test_a_december_release_rolls_the_year_back_for_january():
    """The comparison month is December, so the reading is January -- of the
    *next* year. Getting this wrong is an off-by-a-year every January."""
    page = VINTAGE_2024.replace("in November to", "in January to") \
                       .replace("in October", "in December") \
                       .replace("November 26, 2024", "January 28, 2025")
    assert confboard.parse(page)["month"] == "2025-01-01"


def test_the_change_is_derived_and_a_disagreement_is_loud():
    """A bare "X.X points" near the level is as likely to belong to the Present
    Situation Index one clause later -- that is exactly what made the live page
    report the index as having moved 3.6. Two levels subtract exactly, and the
    stated figure becomes a check."""
    assert confboard.parse(VINTAGE_2024)["change"] == 2.1
    lying = VINTAGE_2024.replace("up 2.1 points", "up 9.9 points")
    try:
        confboard.parse(lying)
        assert False, "the stated change and the two levels disagreed silently"
    except RuntimeError as e:
        assert "wrong sentence" in str(e), e


def test_the_base_year_is_what_separates_the_index_from_its_sub_indices():
    """114.9 and 74.7 are written in the identical sentence shape one clause
    later. `(1985=100)` is printed on the headline and on nothing else, so it
    is the anchor -- and it is the only thing that survived three rewrites of
    the surrounding prose."""
    assert "1985" in confboard.LEVEL.pattern
    without = PAGE.replace("(1985=100) ", "")
    try:
        confboard.parse(without, year=2026)
        assert False, "a reading was taken with no base year to anchor it"
    except RuntimeError as e:
        assert "1985=100" in str(e), e


def test_the_previous_month_is_returned_because_it_is_not_what_was_published():
    """June was published at 91.2 and is quoted here as 92.2. A round that
    resolves against "the CCI for month M" without naming the printing is
    unanswerable: the number changes a month after the round closes."""
    got = confboard.parse(PAGE, year=2026)
    assert got["previous_value_restated"] == 92.2
    assert got["previous_month"] == "2026-06-01"


def test_a_page_without_a_release_raises_rather_than_returning_nothing():
    try:
        confboard.parse("<html><body>Membership. Sign in.</body></html>")
        assert False, "an empty page parsed"
    except RuntimeError as e:
        assert "no Conference Board headline" in str(e), e


def test_fetch_and_parse_are_separate_as_every_adapter_must_be():
    assert callable(confboard.fetch_text) and callable(confboard.parse)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
