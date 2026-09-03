"""The NY Fed SCE workbook adapter. No network.

Run: PYTHONPATH=. python tests/test_sce.py

The fixtures are real xlsx files built in memory with zipfile -- an xlsx is a
zip of XML, which is exactly how the adapter reads it -- so the code under
test is the code the pipeline runs, shared-string resolution and all.
"""
import io
import os
import shutil
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa.adapters import sce

M = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

# The real sheet carries six numeric siblings of the two medians (percentiles,
# point predictions) in the same shape. The fixture keeps a decoy column *and*
# puts the two medians in a different order than the live workbook uses
# (1y in D, 3y in C), so a parser that mapped columns by position or by letter
# would produce numbers -- wrong ones -- rather than fail.
HEADERS = (
    ("B", "25th Percentile one-year ahead expected inflation rate"),
    ("C", "Median three-year ahead expected inflation rate"),
    ("D", "Median one-year ahead expected inflation rate"),
)


def _sheet_xml(cells_rows):
    rows = []
    for i, cells in enumerate(cells_rows, start=1):
        cs = "".join(
            f'<c r="{ref}{i}" t="s"><v>{val}</v></c>' if shared else
            f'<c r="{ref}{i}"><v>{val}</v></c>'
            for ref, val, shared in cells)
        rows.append(f'<row r="{i}">{cs}</row>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<worksheet xmlns="{M}"><sheetData>'
            + "".join(rows) + "</sheetData></worksheet>")


def _package(sheets, strings):
    """sheets: [(tab name, member path under xl/, sheet xml)] -> xlsx bytes."""
    ss = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          f'<sst xmlns="{M}">'
          + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")
    tabs = "".join(
        f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>'
        for i, (name, _, _) in enumerate(sheets, start=1))
    wb = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          f'<workbook xmlns="{M}" xmlns:r="{R}"><sheets>{tabs}</sheets>'
          '</workbook>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/'
            'package/2006/relationships">'
            + "".join(
                f'<Relationship Id="rId{i}" Type="{R}/worksheet" '
                f'Target="{member}"/>'
                for i, (_, member, _) in enumerate(sheets, start=1))
            + "</Relationships>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/sharedStrings.xml", ss)
        for _, member, xml in sheets:
            z.writestr("xl/" + member, xml)
    return buf.getvalue()


def workbook(data_rows, headers=HEADERS, sheet_name="Inflation expectations"):
    """A minimal but real SCE workbook.

    `data_rows`: [(yyyymm, {"B": .., "C": .., "D": ..})]. A decoy tab sits
    first and the target sheet lives in a member whose number matches neither
    tab position, so only name-based resolution can find it.
    """
    strings = ["decoy", sheet_name] + [h for _, h in headers]
    rows = [
        [("A", 1, True)],                                     # title row
        [],                                                   # blank row
        [(col, 2 + i, True) for i, (col, _) in enumerate(headers)],
    ]
    for yyyymm, cells in data_rows:
        rows.append([("A", yyyymm, False)]
                    + [(col, cells[col], False) for col in sorted(cells)])
    decoy = _sheet_xml([[("A", 0, True)]])
    return _package(
        [("Disclaimer", "worksheets/sheet1.xml", decoy),
         (sheet_name, "worksheets/sheet7.xml", _sheet_xml(rows))],
        strings)


def months(n, start=(2013, 6)):
    y, m = start
    out = []
    for _ in range(n):
        out.append(y * 100 + m)
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def full_history(n=158, start=(2013, 6)):
    """A consecutive n-month history with deterministic, in-range values."""
    return workbook([
        (ym, {"B": 1.5, "C": round(2.9 + (i % 5) * 0.1, 1),
              "D": round(3.0 + (i % 7) * 0.1, 1)})
        for i, ym in enumerate(months(n, start))])


TINY = workbook([
    (201306, {"B": 1.9, "C": 3.4, "D": 3.1}),
    (201307, {"B": 1.6, "C": 3.3, "D": 3.2}),
    (201308, {"B": 2.0, "C": 3.8, "D": 3.4}),
])


def test_the_two_medians_are_read_by_header_not_by_position():
    """The fixture holds 1y in D and 3y in C -- the opposite order to the live
    workbook -- with a percentile decoy in B. Position or letter mapping would
    return numbers; only the header strings return the right ones."""
    rows = sce.parse(TINY, min_rows=1)
    assert rows == [
        {"date": "2013-06-01", "infl_1y": 3.1, "infl_3y": 3.4},
        {"date": "2013-07-01", "infl_1y": 3.2, "infl_3y": 3.3},
        {"date": "2013-08-01", "infl_1y": 3.4, "infl_3y": 3.8}], rows


def test_the_sheet_is_found_by_name_never_by_member_number():
    """The target tab is second in the workbook and stored as sheet7.xml;
    a parser that opened sheet2.xml -- or the first tab -- finds a decoy."""
    rows = sce.parse(TINY, min_rows=1)
    assert len(rows) == 3, rows


def test_a_workbook_without_the_sheet_is_loud():
    doctored = workbook([(201306, {"B": 1.9, "C": 3.4, "D": 3.1})],
                        sheet_name="Inflation expectations (rebased)")
    try:
        sce.parse(doctored, min_rows=1)
        assert False, "a missing sheet parsed"
    except RuntimeError as e:
        assert "no sheet named" in str(e), e
        assert "rebased" in str(e), e          # the real names are listed


def test_a_renamed_median_column_is_loud():
    renamed = workbook(
        [(201306, {"B": 1.9, "C": 3.4, "D": 3.1})],
        headers=(("B", "25th Percentile one-year ahead expected inflation rate"),
                 ("C", "Median three-year ahead expected inflation rate"),
                 ("D", "Median one-year ahead expected inflation rate (rebased)")))
    try:
        sce.parse(renamed, min_rows=1)
        assert False, "a renamed median column parsed"
    except RuntimeError as e:
        assert "renamed or split" in str(e) or "before naming" in str(e), e


def test_a_median_that_is_not_a_number_is_loud():
    """A data row is detected by its YYYYMM key and must then parse in full --
    the umichparty lesson: a filter that only ever matches turns a row it
    cannot read into a row that was never there. Here one row's 1y median is
    the shared string "n/a", and a second is missing outright."""
    strings = ["n/a", "title"] + [h for _, h in HEADERS]
    header_row = [(col, 2 + i, True) for i, (col, _) in enumerate(HEADERS)]
    for hole in ([("A", 201306, False), ("C", 3.4, False), ("D", 0, True)],
                 [("A", 201306, False), ("C", 3.4, False)]):
        bad = _package(
            [("Inflation expectations", "worksheets/sheet7.xml",
              _sheet_xml([[("A", 1, True)], header_row, hole]))], strings)
        try:
            sce.parse(bad, min_rows=1)
            assert False, "a month with no number parsed"
        except RuntimeError as e:
            assert "not a number" in str(e), e


def test_a_value_that_cannot_be_an_inflation_median_is_refused():
    """A YYYYMM, a year, or a mispointed column produces values like 201306
    or 55.0; the bounds make them raise instead of publish."""
    for lie in (201307.0, 55.0, -12.0):
        doctored = workbook([(201306, {"B": 1.9, "C": 3.4, "D": lie})])
        try:
            sce.parse(doctored, min_rows=1)
            assert False, f"{lie} passed for a median inflation expectation"
        except RuntimeError as e:
            assert "wrong place" in str(e), e


def test_a_gap_or_duplicate_month_is_a_misparse():
    """The survey has fielded every month since June 2013; the workbook is the
    full history. A hole is a row the parser dropped, not a month off."""
    for seq in ([201306, 201308], [201306, 201306], [201307, 201306]):
        doctored = workbook([(ym, {"B": 1.9, "C": 3.4, "D": 3.1})
                             for ym in seq])
        try:
            sce.parse(doctored, min_rows=1)
            assert False, f"a history running {seq} parsed"
        except RuntimeError as e:
            assert "jumps from" in str(e), e


def test_a_truncated_history_is_refused_at_the_default_threshold():
    try:
        sce.parse(TINY)                       # default min_rows
        assert False, "a three-row history passed for thirteen years"
    except RuntimeError as e:
        assert "parsed to 3 rows" in str(e), e


def test_a_full_length_history_must_still_start_in_june_2013():
    late = full_history(158, start=(2014, 1))
    try:
        sce.parse(late)
        assert False, "a history starting 2014 passed for the full workbook"
    except RuntimeError as e:
        assert "starts at 2014-01-01" in str(e), e
    rows = sce.parse(full_history())
    assert rows[0]["date"] == "2013-06-01" and rows[-1]["date"] == "2026-07-01"
    assert len(rows) == 158


def test_a_body_that_is_not_an_xlsx_is_loud():
    try:
        sce.parse(b"<html>Access Denied</html>", min_rows=1)
        assert False, "an HTML error page parsed as a workbook"
    except RuntimeError as e:
        assert "not a readable xlsx" in str(e), e


def test_the_archive_is_write_once_per_day():
    d = tempfile.mkdtemp(prefix="ssa-sce-")
    saved = sce.ARCHIVE
    sce.ARCHIVE = d
    try:
        first = sce.archive(b"morning capture", day="2026-08-26")
        again = sce.archive(b"afternoon capture", day="2026-08-26")
        assert first == again
        with open(first, "rb") as f:
            assert f.read() == b"morning capture", \
                "an afternoon fetch rewrote the morning's vintage"
        sce.archive(b"next day", day="2026-08-27")
        assert sce.archived_days() == ["2026-08-26", "2026-08-27"]
        assert sce.newest_archived() == sce.archive_path("2026-08-27")
    finally:
        sce.ARCHIVE = saved
        shutil.rmtree(d, ignore_errors=True)


def test_an_empty_archive_refuses_to_invent_a_series():
    d = tempfile.mkdtemp(prefix="ssa-sce-")
    saved = sce.ARCHIVE
    sce.ARCHIVE = d
    try:
        sce.load()
        assert False, "an empty archive produced a series"
    except RuntimeError as e:
        assert "no SCE workbook has been archived" in str(e), e
    finally:
        sce.ARCHIVE = saved
        shutil.rmtree(d, ignore_errors=True)


def test_history_serves_the_archive_when_the_fetch_fails():
    d = tempfile.mkdtemp(prefix="ssa-sce-")
    saved_archive, saved_fetch = sce.ARCHIVE, sce.fetch_bytes
    sce.ARCHIVE = d

    def refused(*a, **k):
        raise RuntimeError("403 Forbidden")

    sce.fetch_bytes = refused
    try:
        sce.archive(full_history(), day="2026-08-20")
        diagnostics = []
        rows = sce.history(fetch=True, today="2026-08-26",
                           diagnostics=diagnostics)
        assert rows[-1] == {"date": "2026-07-01", "infl_1y": 3.3,
                            "infl_3y": 3.1}, rows[-1]
        assert diagnostics[0]["source"] == "sce"
        assert diagnostics[0]["scope"] == "live"
        assert isinstance(diagnostics[0]["error"], RuntimeError)
        assert str(diagnostics[0]["error"]) == "403 Forbidden"
        assert "2026-08-20.xlsx" in diagnostics[0]["archive_evidence"]
        assert "newest reference month 2026-07-01" in \
            diagnostics[0]["archive_evidence"]
    finally:
        sce.ARCHIVE, sce.fetch_bytes = saved_archive, saved_fetch
        shutil.rmtree(d, ignore_errors=True)


def test_history_does_not_hide_a_malformed_live_workbook_behind_the_archive():
    d = tempfile.mkdtemp(prefix="ssa-sce-")
    saved_archive, saved_fetch = sce.ARCHIVE, sce.fetch_bytes
    sce.ARCHIVE = d
    sce.fetch_bytes = lambda *a, **k: b"<html>not an xlsx</html>"
    try:
        sce.archive(full_history(), day="2026-08-20")
        try:
            sce.history(fetch=True, today="2026-08-26", diagnostics=[])
        except RuntimeError as e:
            assert "not a readable xlsx" in str(e), e
        else:
            raise AssertionError("a malformed live workbook used the archive")
    finally:
        sce.ARCHIVE, sce.fetch_bytes = saved_archive, saved_fetch
        shutil.rmtree(d, ignore_errors=True)


def test_http_200_html_from_requests_cannot_hide_behind_the_archive():
    """Exercise the production request -> magic validation -> history seam."""
    d = tempfile.mkdtemp(prefix="ssa-sce-")
    saved_archive, saved_get = sce.ARCHIVE, sce.requests.get
    sce.ARCHIVE = d

    class HtmlResponse:
        content = b"<html>NY Fed access interstitial</html>"

        @staticmethod
        def raise_for_status():
            return None

    sce.requests.get = lambda *_a, **_k: HtmlResponse()
    try:
        sce.archive(full_history(), day="2026-08-20")
        try:
            sce.history(fetch=True, today="2026-08-26", diagnostics=[])
        except RuntimeError as error:
            assert "not an xlsx" in str(error), error
        else:
            raise AssertionError("HTTP 200 HTML was hidden by the SCE archive")
    finally:
        sce.ARCHIVE, sce.requests.get = saved_archive, saved_get
        shutil.rmtree(d, ignore_errors=True)


def test_history_reraises_when_the_fetch_fails_and_nothing_is_archived():
    """A fetch failure over an empty archive must stay a failure: an empty
    answer served quietly would look exactly like a quiet week."""
    d = tempfile.mkdtemp(prefix="ssa-sce-")
    saved_archive, saved_fetch = sce.ARCHIVE, sce.fetch_bytes
    sce.ARCHIVE = d

    def refused(*a, **k):
        raise RuntimeError("403 Forbidden")

    sce.fetch_bytes = refused
    try:
        sce.history(fetch=True, today="2026-08-26")
        assert False, "a failed fetch over an empty archive produced a series"
    except RuntimeError as e:
        assert "403" in str(e), e
    finally:
        sce.ARCHIVE, sce.fetch_bytes = saved_archive, saved_fetch
        shutil.rmtree(d, ignore_errors=True)


def test_history_files_a_successful_fetch_and_serves_it():
    d = tempfile.mkdtemp(prefix="ssa-sce-")
    saved_archive, saved_fetch = sce.ARCHIVE, sce.fetch_bytes
    sce.ARCHIVE = d
    sce.fetch_bytes = lambda *a, **k: full_history()
    try:
        rows = sce.history(fetch=True, today="2026-08-26")
        assert len(rows) == 158
        assert len(sce.archived_days()) == 1, sce.archived_days()
        # The vintage on disk is byte-identical to what was fetched.
        body, _ = sce.read_archived()
        assert body == full_history()
    finally:
        sce.ARCHIVE, sce.fetch_bytes = saved_archive, saved_fetch
        shutil.rmtree(d, ignore_errors=True)


def test_history_raises_on_a_series_that_quietly_stopped_moving():
    """Whether the workbook came from the network or the archive, a newest
    reference month far behind today must be loud -- a warning on the
    fallback path does not cover a live file that stopped being updated."""
    d = tempfile.mkdtemp(prefix="ssa-sce-")
    saved = sce.ARCHIVE
    sce.ARCHIVE = d
    try:
        sce.archive(full_history(), day="2026-08-20")   # newest ref: 2026-07
        assert sce.history(fetch=False, today="2026-11-30")[-1]["date"] == \
            "2026-07-01"                                 # 4 months: tolerated
        try:
            sce.history(fetch=False, today="2026-12-01")
            assert False, "a five-month-stale series was served without a word"
        except RuntimeError as e:
            assert "months behind today" in str(e), e
    finally:
        sce.ARCHIVE = saved
        shutil.rmtree(d, ignore_errors=True)


def test_history_never_touches_the_network_when_told_not_to():
    d = tempfile.mkdtemp(prefix="ssa-sce-")
    saved_archive, saved_fetch = sce.ARCHIVE, sce.fetch_bytes
    sce.ARCHIVE = d

    def networked(*a, **k):
        raise AssertionError("fetch=False reached the network")

    sce.fetch_bytes = networked
    try:
        sce.archive(full_history(), day="2026-08-20")
        rows = sce.history(fetch=False, today="2026-08-26")
        assert len(rows) == 158
    finally:
        sce.ARCHIVE, sce.fetch_bytes = saved_archive, saved_fetch
        shutil.rmtree(d, ignore_errors=True)


def test_to_series_splits_the_horizons_and_refuses_unknown_ones():
    rows = sce.parse(TINY, min_rows=1)
    assert sce.to_series(rows, "1y") == [
        {"date": "2013-06-01", "value": 3.1},
        {"date": "2013-07-01", "value": 3.2},
        {"date": "2013-08-01", "value": 3.4}]
    assert sce.to_series(rows, "3y")[0] == {"date": "2013-06-01", "value": 3.4}
    try:
        sce.to_series(rows, "5y")
        assert False, "an unregistered horizon produced a series"
    except ValueError as e:
        assert "unknown horizon" in str(e), e


def test_fetch_and_parse_are_separate_as_every_adapter_must_be():
    assert callable(sce.fetch_bytes) and callable(sce.parse)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
