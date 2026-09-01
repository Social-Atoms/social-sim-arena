"""The Michigan party addenda, from converted PDF text. No network, no poppler.

The fixture below is carved verbatim out of the real `pdftotext -layout` output
for `sources/umichparty/2026-08-14.pdf` -- the header block, one 1980s row, the
2016 -> 2017 and 2024 partisan flips, the 2026 tail, and the trailing stamp and
notes. Column spacing is copied exactly, including the form feed the converter
writes at a page break, because that character is what the first parse of this
document got wrong.

Run: PYTHONPATH=. python tests/test_umichparty.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa import series as series_registry
from ssa.adapters import umichparty

# Verbatim, spacing included. The \f before "October     2012" is a page break
# and is the regression this fixture exists for: the probe's whole-text regex
# could not match after one, so that row and two others vanished silently and
# were written up as months the survey had skipped.
ADDENDA = (
    "          INDEX OF CONSUMER SENTIMENT AND COMPONENTS BY POLITICAL PARTY\n"
    "Note: The data below are also available in PDF and Excel format as Table 5b on the Tables - All Households\n"
    "page of our data website (https://data.sca.isr.umich.edu/tables.php). The links in the Monthly column\n"
    "report three month moving averages, while the links in the Historical column report monthly readings.\n"
    "\n"
    "                           INDEX OF CONSUMER                CURRENT ECONOMIC               INDEX OF CONSUMER\n"
    "                               SENTIMENT                       CONDITIONS                     EXPECTATIONS\n"
    "DATE OF SURVEY              Dem      Ind     Rep             Dem      Ind    Rep              Dem      Ind   Rep\n"
    "June            1980        59.2    60.3     55.1             67.0   72.5    61.5              54.2   52.4   51.1\n"
    "\n"
    "\fOctober     2012   102.3   74.8    69.4    105.0 74.5   84.9   100.6 75.0    59.4\n"
    "\n"
    "October     2016   102.1   84.5    74.4    112.5 102.4 95.1    95.4   73.1   61.1\n"
    "\n"
    "February    2017   77.5    98.4    115.7   111.9 112.8 108.9   55.5   89.2   120.1\n"
    "\n"
    "October       2024      91.4     65.8   53.6            88.8   61.7   41.4          93.1   68.4 61.4\n"
    "November      2024      81.3     63.1   69.1            90.5   55.8   37.9          75.4   67.8 89.2\n"
    "December      2024      69.6     70.2   85.4            98.9   71.4   52.0          50.8   69.5 106.8\n"
    "\n"
    "June          2026       37.3    44.5   86.2           36.6    43.3   80.3         37.8    45.3 89.9\n"
    "July          2026       42.8    49.3   88.2           45.4    49.7   81.2         41.2    49.0 92.8\n"
    "August        2026       39.1    48.5   78.7           41.1    50.3   75.1         37.9    47.4 81.0\n"
    "\n"
    "8/14/2026\n"
    'The Question was:     "Generally speaking, do you usually think of yourself as a\n'
    '                      Republican, a Democrat, an Independent, or what?"\n'
    "Dem = Democrat, Ind = Independent, Rep = Republican\n"
    "\n"
    "Gaps in data result from months in which Political Party question was not asked.\n"
)

# The fixture is a dozen rows, not a hundred, so every call names its own floor.
# The pipeline never does: `parse` defaults to umichparty.MIN_ROWS.
SMALL = dict(min_rows=1)


def rows():
    return umichparty.parse(ADDENDA, **SMALL)


def test_the_whole_table_comes_out_of_the_converted_text():
    got = rows()
    assert [r["date"] for r in got] == [
        "1980-06-01", "2012-10-01", "2016-10-01", "2017-02-01", "2024-10-01",
        "2024-11-01", "2024-12-01", "2026-06-01", "2026-07-01", "2026-08-01"
    ], got
    assert got[0] == {
        "date": "1980-06-01", "preliminary": False,
        "ics": {"dem": 59.2, "ind": 60.3, "rep": 55.1},
        "conditions": {"dem": 67.0, "ind": 72.5, "rep": 61.5},
        "expectations": {"dem": 54.2, "ind": 52.4, "rep": 51.1}}, got[0]


def test_the_three_2026_readings_are_the_ones_the_pdf_actually_carries():
    """Checked here and nowhere in the module: a future addenda extends this
    table, so a runtime assertion on known values would fail on the first month
    that works correctly. Verified by hand against the 2026-08-14 document."""
    ics = {r["date"]: r["ics"] for r in rows()}
    assert ics["2026-06-01"] == {"dem": 37.3, "ind": 44.5, "rep": 86.2}
    assert ics["2026-07-01"] == {"dem": 42.8, "ind": 49.3, "rep": 88.2}
    assert ics["2026-08-01"] == {"dem": 39.1, "ind": 48.5, "rep": 78.7}


def test_the_partisan_flip_survives_the_parse_in_both_directions():
    """The whole reason this series is worth a round. October 2016 to February
    2017 swaps a 27.7-point Dem lead for a 38.2-point Rep lead; October to
    December 2024 swaps a 37.8-point Dem lead for a 15.8-point Rep one, and
    November 2024 catches the two lines crossing. No persistence null sees
    either coming, and both sit on a calendar known years ahead."""
    ics = {r["date"]: r["ics"] for r in rows()}
    assert round(ics["2016-10-01"]["dem"] - ics["2016-10-01"]["rep"], 1) == 27.7
    assert round(ics["2017-02-01"]["dem"] - ics["2017-02-01"]["rep"], 1) == -38.2
    assert ics["2024-10-01"]["dem"] > ics["2024-10-01"]["rep"]
    assert ics["2024-11-01"]["dem"] > ics["2024-11-01"]["rep"]
    assert ics["2024-12-01"]["dem"] < ics["2024-12-01"]["rep"]


def test_a_row_at_a_page_break_is_not_lost():
    """October 2012 is preceded by a form feed. Reading the document as one
    string under re.MULTILINE drops it -- `^` matches after a newline and not
    after a `\\f` -- and drops it without a word, which is how the first pass
    over this PDF reported three rows as months that were never surveyed."""
    assert "\f" in ADDENDA
    assert any(r["date"] == "2012-10-01" for r in rows())


def test_a_row_with_eight_values_raises_instead_of_being_skipped():
    """The failure mode that matters is not a crash, it is a table quietly one
    row short. A line that opens like a data row and does not parse as one is a
    layout change, and the run has to stop."""
    broken = ADDENDA.replace(
        "August        2026       39.1    48.5   78.7           41.1    50.3   75.1         37.9    47.4 81.0",
        "August        2026       39.1    48.5   78.7           41.1    50.3   75.1         37.9    47.4")
    try:
        umichparty.parse(broken, **SMALL)
        assert False, "an eight-value row was silently dropped"
    except RuntimeError as e:
        assert "does not parse as one" in str(e), e


def test_a_tenth_column_raises_too_because_the_row_is_anchored_at_both_ends():
    """Unanchored, a row carrying an extra column matches its first nine values
    and the tenth disappears -- which reads as a successful parse of a table
    whose columns have moved."""
    extra = ADDENDA.replace("47.4 81.0\n", "47.4 81.0   99.9\n")
    try:
        umichparty.parse(extra, **SMALL)
        assert False, "a tenth column was accepted"
    except RuntimeError as e:
        assert "does not parse as one" in str(e), e


def test_a_value_outside_the_index_range_raises():
    """A year, a page number or a footnote marker read as a reading is a number
    nothing downstream can tell from a real one."""
    bad = ADDENDA.replace("39.1    48.5   78.7", "39.1    48.5  278.7")
    try:
        umichparty.parse(bad, **SMALL)
        assert False, "278.7 passed as a sentiment index reading"
    except RuntimeError as e:
        assert "not a sentiment index reading" in str(e), e


def test_a_short_table_raises_at_the_real_floor():
    """The fixture is ten rows. The pipeline's floor is a hundred, and the live
    document carries 156 -- a table that suddenly parses to thirty is a layout
    change, not a revision."""
    assert umichparty.MIN_ROWS >= 100
    try:
        umichparty.parse(ADDENDA)
        assert False, "a ten-row table passed the pipeline's row floor"
    except RuntimeError as e:
        assert "fewer than the" in str(e), e


def test_the_asof_comes_from_the_stamp_inside_the_document():
    assert umichparty.stamp_date(ADDENDA) == "2026-08-14"
    try:
        umichparty.stamp_date(ADDENDA.replace("8/14/2026\n", ""))
        assert False, "a table with no stamp was dated anyway"
    except RuntimeError as e:
        assert "wall clock" in str(e), e


def test_the_staleness_guard_trips_when_the_stamp_outruns_the_table():
    """The Michigan lesson, in one check. A document stamped months after its
    own newest row is a source that has quietly stopped updating, and that is
    exactly what mis-resolved umich-2026-08-prelim -- see ssa/adapters/umich.py.
    Two months of slack, because the party question can go unasked for one."""
    stale = ADDENDA.replace("8/14/2026", "12/10/2026")
    try:
        umichparty.parse(stale, **SMALL)
        assert False, "a table four months behind its own stamp was published"
    except RuntimeError as e:
        assert "months apart" in str(e), e
    # One month behind is a question that was not asked, not an outage.
    assert umichparty.parse(ADDENDA.replace("8/14/2026", "9/12/2026"), **SMALL)


def test_a_body_that_is_not_a_pdf_is_a_hard_error_not_a_quiet_none():
    """data.sca.isr.umich.edu answers 200 with its homepage for anything it does
    not have, so "not a PDF" means a dead link wearing a 200. Treating that as
    "no addenda this month" would let the series go dark without a word."""
    class Reply:
        status_code = 200
        content = b"<!DOCTYPE html><html><head><title>Surveys of Consumers"

        def raise_for_status(self):
            pass

    class Stub:
        def get(self, url, **kw):
            return Reply()

    saved = umichparty.requests
    umichparty.requests = Stub()
    try:
        umichparty.fetch_latest()
        assert False, "an HTML page was archived as an addenda PDF"
    except RuntimeError as e:
        assert "not a" in str(e) and "PDF" in str(e), e
    finally:
        umichparty.requests = saved


def test_a_month_with_no_addenda_yet_is_a_none_rather_than_a_failure():
    """The publication has happened exactly once. A month that never gets one
    must leave the series where it is, not break a refresh."""
    class Reply:
        status_code = 404
        content = b""

        def raise_for_status(self):
            raise AssertionError("404 must be answered before raise_for_status")

    class Stub:
        def get(self, url, **kw):
            return Reply()

    saved = umichparty.requests
    umichparty.requests = Stub()
    try:
        assert umichparty.fetch_latest(docid=99999) is None
    finally:
        umichparty.requests = saved


def test_a_missing_converter_names_the_package_rather_than_skipping():
    """Same contract ssa/stamps.py has with `ots`: a system tool that is not
    installed is a loud failure with the install line in the message. Silently
    skipping would publish a party question backed by nothing."""
    saved = umichparty.shutil.which
    umichparty.shutil.which = lambda name: None
    try:
        umichparty.to_text(b"%PDF-1.4")
        assert False, "a missing converter was tolerated"
    except RuntimeError as e:
        assert "poppler-utils" in str(e) and "brew install poppler" in str(e), e
    finally:
        umichparty.shutil.which = saved


def test_months_the_question_was_not_asked_stay_absent():
    """The PDF's own footer says gaps are months the party question was not
    asked. Filling them would hand every baseline a reading nobody was given."""
    dates = {r["date"] for r in rows()}
    for missing in ("1980-07-01", "2016-11-01", "2016-12-01", "2017-01-01",
                    "2024-09-01", "2026-05-01"):
        assert missing not in dates, missing
    series = umichparty.to_series(rows(), "dem")
    assert [p["date"] for p in series] == sorted(r["date"] for r in rows())
    assert "2016-11-01" not in {p["date"] for p in series}


def test_to_series_reads_one_party_and_refuses_a_name_it_does_not_know():
    got = umichparty.to_series(rows(), "rep")
    assert got[-3:] == [{"date": "2026-06-01", "value": 86.2},
                        {"date": "2026-07-01", "value": 88.2},
                        {"date": "2026-08-01", "value": 78.7}], got[-3:]
    assert umichparty.to_series(rows(), "ind", group="expectations")[-1] == \
        {"date": "2026-08-01", "value": 47.4}
    for bad in ("democrat", "Dem", "other"):
        try:
            umichparty.to_series(rows(), bad)
            assert False, f"{bad!r} was accepted as a party"
        except ValueError:
            pass


def test_the_registry_builds_three_series_from_one_parse():
    """`build_all` takes injected rows so this stays off the disk and off
    poppler, and the three series must come out of a single conversion -- three
    parses of one PDF is three chances to disagree."""
    saved = series_registry.SERIES
    series_registry.SERIES = {k: v for k, v in saved.items()
                              if v["source"] == "umichparty"}
    try:
        assert set(series_registry.SERIES) == {
            "umich_party_dem", "umich_party_ind", "umich_party_rep"}
        built = series_registry.build_all({"umichparty": rows()})
        assert set(built) == set(series_registry.SERIES)
        newest = {k: v[-1]["value"] for k, v in built.items()}
        assert newest == {"umich_party_dem": 39.1, "umich_party_ind": 48.5,
                          "umich_party_rep": 78.7}, newest
        # Three distinct targets, not one number filed three times.
        assert len(set(newest.values())) == 3
        for v in built.values():
            assert [p["date"] for p in v] == sorted(p["date"] for p in v)
    finally:
        series_registry.SERIES = saved


def test_every_party_round_names_a_registered_series_and_a_normal_target():
    import json
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "questions", "season0.json")
    with open(path) as f:
        season = json.load(f)
    party = [r for r in season["rounds"] if r["tracker"] == "umich_party"]
    assert len(party) == 3, party
    prelim = [r for r in season["rounds"]
              if r["round_id"] == "umich-2026-09-prelim"][0]
    for r in party:
        assert r["series"] in series_registry.SERIES, r["round_id"]
        assert r["target_type"] == "continuous_normal", r["round_id"]
        # The addenda ships *with* the national preliminary, so the two share a
        # calendar. Copied from the national round rather than invented, and
        # asserted so a corrected national date cannot leave these behind.
        for key in ("release_at", "lock_at", "release_estimated"):
            assert r[key] == prelim[key], (r["round_id"], key)


def test_the_party_series_refuse_the_persona_arm_by_name():
    """personas.weights_for reweights by population, not by party, so a persona
    run would put the five ICS items to a national panel and report the answer
    as a subgroup. Returning None is what makes the arm skip the series --
    the same refusal civiqs_net_approval_rep already carries."""
    for sid in ("umich_party_dem", "umich_party_ind", "umich_party_rep"):
        assert series_registry.survey(sid) is None, sid
        d = series_registry.describe(sid)
        assert d["unit"] == "index points (1966=100)"
        assert "preliminary" in d["cadence"]
        assert "1966 = 100" in d["methodology"]


def test_the_archive_is_read_and_never_the_network():
    """`load` parses the newest committed vintage. Nothing in the pipeline
    fetches: the addenda has been published once, and a refresh that depended
    on it appearing again would break on the first month it does not."""
    scratch = tempfile.mkdtemp(prefix="ssa-umichparty-")
    saved = umichparty.ARCHIVE
    umichparty.ARCHIVE = scratch
    try:
        assert umichparty.archived_days() == []
        try:
            umichparty.load()
            assert False, "an empty archive parsed to something"
        except RuntimeError as e:
            assert "fetch_latest" in str(e), e
        for day in ("2026-08-14", "2026-09-11"):
            with open(umichparty.archive_path(day), "wb") as f:
                f.write(b"%PDF-1.4\n")
        assert umichparty.archived_days() == ["2026-08-14", "2026-09-11"]
        assert umichparty.newest_archived().endswith("2026-09-11.pdf")
    finally:
        umichparty.ARCHIVE = saved
        shutil.rmtree(scratch, ignore_errors=True)


def test_a_stamp_date_is_write_once_and_a_revision_is_loud():
    """A same-day re-fetch is harmless only when it is the same document.
    Replacing different bytes would silently revise the source behind a lock.
    """
    scratch = tempfile.mkdtemp(prefix="ssa-umichparty-write-once-")
    saved = umichparty.ARCHIVE
    umichparty.ARCHIVE = scratch
    try:
        original = b"%PDF-1.4\noriginal vintage"
        path = umichparty.archive(original, "2026-08-14")
        assert umichparty.archive(original, "2026-08-14") == path
        try:
            umichparty.archive(b"%PDF-1.4\nrevised vintage", "2026-08-14")
            assert False, "different bytes overwrote a source vintage"
        except RuntimeError as e:
            assert "different bytes" in str(e) and "refusing" in str(e), e
        with open(path, "rb") as f:
            assert f.read() == original
        assert not any(name.endswith(".tmp") for name in os.listdir(scratch))
    finally:
        umichparty.ARCHIVE = saved
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
