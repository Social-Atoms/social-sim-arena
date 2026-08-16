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
        "previous_month": "2026-06-01", "previous_value_restated": 92.2}, got


def test_the_base_year_is_what_separates_the_index_from_its_sub_indices():
    """114.9 and 74.7 are written in the identical sentence shape one clause
    later. `(1985=100)` appears only on the headline, so requiring it is what
    stops the Present Situation Index being published as consumer confidence."""
    assert "1985=100" in confboard.HEADLINE.pattern
    without = PAGE.replace("(1985=100) ", "")
    try:
        confboard.parse(without, year=2026)
        assert False, "a reading was taken with no base year to anchor it"
    except RuntimeError as e:
        assert "refusing to publish" in str(e).lower(), e


def test_the_previous_month_is_returned_because_it_is_not_what_was_published():
    """June was published at 91.2 and is quoted here as 92.2. A round that
    resolves against "the CCI for month M" without naming the printing is
    unanswerable: the number changes a month after the round closes."""
    got = confboard.parse(PAGE, year=2026)
    assert got["previous_value_restated"] == 92.2
    assert got["previous_month"] == "2026-06-01"


def test_a_december_release_read_in_january_is_not_dated_in_the_future():
    """The page names the month and never the year."""
    import datetime
    page = PAGE.replace("in July", "in December").replace("in June", "in November")
    got = confboard.parse(page, year=datetime.date.today().year)
    m = int(got["month"][5:7])
    assert m == 12
    assert got["month"] <= datetime.date.today().isoformat(), got["month"]


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
