"""The Harvard-Harris topline, from converted PDF text. No network, no poppler.

The fixture is carved verbatim out of the real `pdftotext -layout` output for
`sources/hhpoll/2026-07-16.pdf` (the July 2026 topline): the personal-finances
table before the anchor, the complete M3ALT approval table, and the head of
the `M3A_ISS ... Summary Of Strongly/Somewhat Approve` table after it --
fifteen issue-approval percentages in the identical two-line shape, which is
exactly what a parser anchored on anything looser than the question code
would read instead. Column spacing is copied exactly; the form feed before
one page header is the character the umichparty probe once lost three rows to.

Run: PYTHONPATH=. python tests/test_hhpoll.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa.adapters import hhpoll

PAGE_HEAD = (
    "Fielding Period: July 10 - 12, 2026\n"
    "HCAPS (Filtered on Registered Voters)\n"
    "Weighted To The U.S. General Adult Population\n"
    "                                                                                                                                         16 Jul 2026\n"
)

# Table 13, the table *before* the anchor: percents in the same two-line shape.
FINANCES = (
    PAGE_HEAD +
    "                                                                                                                                            Table 13\n"
    "                                                I4 Would you say that your personal financial situation is improving or getting worse?\n"
    "\n"
    "Base: All Respondents\n"
    "\n"
    "                                       Total\n"
    "\n"
    "Unweighted Base                        1776\n"
    "\n"
    "Weighted Base                          1776\n"
    "\n"
    "Improving                               538\n"
    "                                         30%\n"
    "\n"
    "Getting worse                            821\n"
    "                                          46%\n"
    "\n"
    "Just as well off                        417\n"
    "                                         23%\n"
    "\n"
    "Sigma                                  1776\n"
    "                                        100%\n"
    "\n"
    "                                                                                                                                               Page 16\n"
    "\n"
)

# Table 14, the one table the arena scores. The \f opens the page it sits on.
APPROVAL = (
    "\f" + PAGE_HEAD +
    "                                                                                                                                               Table 14\n"
    "                                                M3ALT Do you disapprove or approve of the job Donald J. Trump is doing as President of the United States?\n"
    "\n"
    "\n"
    "Base: All Respondents\n"
    "\n"
    "\n"
    "\n"
    "                                       Total\n"
    "\n"
    "\n"
    "Unweighted Base                         1776\n"
    "\n"
    "Weighted Base                           1776\n"
    "\n"
    "\n"
    "\n"
    "Strongly/Somewhat Approve                747\n"
    "(Net)                                     42%\n"
    "\n"
    "  Strongly approve                       401\n"
    "                                          23%\n"
    "\n"
    "  Somewhat approve                       345\n"
    "                                          19%\n"
    "\n"
    "Strongly/Somewhat Disapprove             953\n"
    "(Net)                                     54%\n"
    "\n"
    "  Somewhat disapprove                    241\n"
    "                                          14%\n"
    "\n"
    "  Strongly disapprove                    711\n"
    "                                          40%\n"
    "\n"
    "Don't Know/Not Sure                      76\n"
    "                                          4%\n"
    "\n"
    "Sigma                                   1776\n"
    "                                         100%\n"
    "\n"
    "                                                                                                                                      Page 17\n"
    "\n"
)

# Table 15, the trap: issue approvals in the identical two-line shape, under a
# title that even contains "Strongly/Somewhat Approve".
ISSUES = (
    PAGE_HEAD +
    "                                                                                                                                      Table 15\n"
    "                                                M3A_ISS Do you disapprove or approve of the job Donald J. Trump is doing on ...?\n"
    "\n"
    "                                                                      Summary Of Strongly/Somewhat Approve\n"
    "\n"
    "\n"
    "Base: All Respondents\n"
    "\n"
    "\n"
    "\n"
    "                                       Total\n"
    "\n"
    "\n"
    "Unweighted Base                        1776\n"
    "\n"
    "Weighted Base                          1776\n"
    "\n"
    "\n"
    "\n"
    "Immigration                             864\n"
    "                                         49%\n"
    "\n"
    "Fighting crime in America's              835\n"
    "cities                                    47%\n"
    "\n"
    "Administering the Government            752\n"
    "                                         42%\n"
    "\n"
    "Foreign Affairs                         714\n"
    "                                         40%\n"
)

TOPLINE = FINANCES + APPROVAL + ISSUES

EXPECTED = {"date": "2026-07-12", "approve": 42.0, "disapprove": 54.0,
            "dk": 4.0, "unweighted_n": 1776, "stamp": "2026-07-16",
            "question": ("Do you disapprove or approve of the job Donald J. "
                         "Trump is doing as President of the United States?")}


def test_the_approval_record_comes_out_of_the_real_layout():
    got = hhpoll.parse(TOPLINE)
    assert got == EXPECTED, got


def test_the_anchor_is_the_question_code_not_the_word_approve():
    """Without Table 14 the file still carries 'Summary Of Strongly/Somewhat
    Approve' and a page of percents in the identical two-line shape. That must
    read as 'the poll dropped the question', never as 49% approval."""
    try:
        hhpoll.parse(FINANCES + ISSUES)
        assert False, "an issue-approval percent passed for the topline"
    except RuntimeError as e:
        assert "no M3ALT table" in str(e), e


def test_a_changed_question_wording_is_loud():
    """A different question scored as the same series is the quietest possible
    failure; the constant is updated by a person, on purpose, or not at all."""
    reworded = TOPLINE.replace("the job Donald J. Trump is doing as President",
                               "the job President Trump is doing as President")
    try:
        hhpoll.parse(reworded)
        assert False, "a reworded question parsed as the registered one"
    except RuntimeError as e:
        assert "wording changed" in str(e), e


def test_two_anchor_tables_are_loud():
    try:
        hhpoll.parse(TOPLINE + APPROVAL)
        assert False, "two M3ALT tables parsed as one poll"
    except RuntimeError as e:
        assert "2 M3ALT tables" in str(e), e


def test_nets_that_do_not_sum_to_a_hundred_are_loud():
    doctored = TOPLINE.replace("(Net)                                     42%",
                               "(Net)                                     22%")
    try:
        hhpoll.parse(doctored)
        assert False, "approve+disapprove+dk of 80 went unremarked"
    except RuntimeError as e:
        assert "not ~100" in str(e), e


def test_a_net_that_disagrees_with_its_own_count_is_loud():
    """42% is also 747 over the weighted base of 1776; a percent read off the
    wrong line fails the sum check, a count read out of the wrong table fails
    this one. Together they pin the number to its own table."""
    doctored = TOPLINE.replace("Strongly/Somewhat Approve                747",
                               "Strongly/Somewhat Approve                947")
    try:
        hhpoll.parse(doctored)
        assert False, "a count from the wrong table went unremarked"
    except RuntimeError as e:
        assert "wrong table" in str(e), e


def test_a_subgroup_base_cannot_pass_for_the_topline():
    doctored = TOPLINE.replace("Unweighted Base                         1776",
                               "Unweighted Base                          176")
    try:
        hhpoll.parse(doctored)
        assert False, "a 176-person base passed for a national wave"
    except RuntimeError as e:
        assert "subgroup" in str(e), e


def test_the_fielding_period_dates_the_row_by_its_end_day():
    assert hhpoll.fielding_end(TOPLINE) == "2026-07-12"
    # The April and May shapes, and a span that crosses a month boundary.
    assert hhpoll.fielding_end(
        "Fielding Period: April 23 - 26, 2026\n") == "2026-04-26"
    assert hhpoll.fielding_end(
        "Fielding Period: June 28 - July 1, 2026\n") == "2026-07-01"
    try:
        hhpoll.fielding_end("Fielding Period: July 10 - 12, 2026\n"
                            "Fielding Period: July 11 - 13, 2026\n")
        assert False, "two fielding periods in one document went unremarked"
    except RuntimeError as e:
        assert "different" in str(e), e
    try:
        hhpoll.fielding_end("Fielding Period: Summer 2026\n")
        assert False, "an unreadable fielding period parsed"
    except RuntimeError as e:
        assert "unreadable fielding period" in str(e), e


def test_the_stamp_names_the_vintage_never_the_download_day():
    assert hhpoll.stamp_date(TOPLINE) == "2026-07-16"
    try:
        hhpoll.stamp_date("no dates here\n")
        assert False, "a document with no stamp was dated anyway"
    except RuntimeError as e:
        assert "guesswork" in str(e), e
    try:
        hhpoll.stamp_date("   16 Jul 2026\n   2 Jun 2026\n")
        assert False, "two production stamps went unremarked"
    except RuntimeError as e:
        assert "different" in str(e), e


def test_a_missing_converter_names_the_package_rather_than_skipping():
    saved = hhpoll.BINARY
    hhpoll.BINARY = "pdftotext-that-does-not-exist"
    try:
        hhpoll.to_text(b"%PDF-1.7 ...")
        assert False, "a missing converter went unnoticed"
    except RuntimeError as e:
        assert "poppler" in str(e), e
    finally:
        hhpoll.BINARY = saved


def test_the_archive_is_write_once_and_never_overwrites():
    d = tempfile.mkdtemp(prefix="ssa-hh-")
    saved = hhpoll.ARCHIVE
    hhpoll.ARCHIVE = d
    try:
        p = hhpoll.archive(b"%PDF-1.7 the poll", "2026-07-16")
        assert hhpoll.archive(b"%PDF-1.7 the poll", "2026-07-16") == p
        try:
            hhpoll.archive(b"%PDF-1.7 a different poll", "2026-07-16")
            assert False, "an archived vintage was overwritten"
        except RuntimeError as e:
            assert "different document" in str(e), e
        with open(p, "rb") as f:
            assert f.read() == b"%PDF-1.7 the poll"
        assert hhpoll.archived_days() == ["2026-07-16"]
    finally:
        hhpoll.ARCHIVE = saved
        shutil.rmtree(d, ignore_errors=True)


def test_an_empty_archive_refuses_to_invent_a_series():
    d = tempfile.mkdtemp(prefix="ssa-hh-")
    saved = hhpoll.ARCHIVE
    hhpoll.ARCHIVE = d
    try:
        hhpoll.load()
        assert False, "an empty archive produced a series"
    except RuntimeError as e:
        assert "no Harvard-Harris topline" in str(e), e
    finally:
        hhpoll.ARCHIVE = saved
        shutil.rmtree(d, ignore_errors=True)


def test_records_sort_by_fielding_date_and_refuse_duplicates():
    a = dict(EXPECTED)
    b = dict(EXPECTED, date="2026-05-31", approve=43.0, stamp="2026-06-02")
    assert [r["date"] for r in hhpoll.to_records([a, b])] == \
        ["2026-05-31", "2026-07-12"]
    try:
        hhpoll.to_records([a, dict(a, stamp="2026-07-17")])
        assert False, "one poll filed under two stamps went unremarked"
    except RuntimeError as e:
        assert "two archived toplines" in str(e), e


def test_to_series_dates_by_stamp_and_keeps_skipped_months_absent():
    """The series is dated by the production stamp -- the day the number
    became public -- never the fielding day the record keeps. Fielding-dated
    rows would let a late-published wave slide into pre-lock history after
    the lock froze it (see the to_series docstring)."""
    rows = hhpoll.to_series([
        dict(EXPECTED, date="2026-04-26", approve=42.0, stamp="2026-04-28"),
        dict(EXPECTED, date="2026-05-31", approve=43.0, stamp="2026-06-02"),
        # no June poll exists, and no June row is invented
        dict(EXPECTED, date="2026-07-12", approve=42.0, stamp="2026-07-16")])
    assert rows == [{"date": "2026-04-28", "value": 42.0},
                    {"date": "2026-06-02", "value": 43.0},
                    {"date": "2026-07-16", "value": 42.0}], rows


def test_fetching_the_next_poll_requires_a_maintainer_and_a_url():
    """No calendar, no derivable slug, no default URL: `fetch()` without one
    is a programming error, not a request."""
    try:
        hhpoll.fetch()
        assert False, "fetch invented a URL"
    except TypeError:
        pass
    assert callable(hhpoll.parse) and callable(hhpoll.to_text)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
