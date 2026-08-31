"""The MARTS probe, re-run offline from the committed fixture.

The point of this file is that the no-go on Census MARTS is *reproducible*.
The verdict in `ssa/inventory.py` rests on measurements, and a measurement
nobody can re-run is an assertion. Everything below reads
`sources/marts/probe.json` and touches no network.
"""
import importlib.util
import io
import json
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

spec = importlib.util.spec_from_file_location(
    "_marts", os.path.join(ROOT, "tools", "probe_marts.py"))
marts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(marts)

with open(os.path.join(ROOT, "sources", "marts", "probe.json")) as _fh:
    FIXTURE = json.load(_fh)


def test_the_window_is_the_thirty_six_the_issue_asked_for():
    rel = FIXTURE["releases"]
    assert len(rel) == 36, len(rel)
    months = [r["reference_month"] for r in rel]
    assert months == sorted(months), "releases are not in reference-month order"
    assert months == marts.months_back(months[-1], 36)
    for r in rel:
        # The URL names the reference month, which is what lets a round say
        # which artifact will settle it before that artifact exists.
        assert r["url"] == marts.archive_url(r["reference_month"])
        assert len(r["sha256"]) == 64 and r["bytes"] > 10000
    print("ok test_the_window_is_the_thirty_six_the_issue_asked_for")


def test_every_mechanical_gate_passes():
    """The measured half of the verdict. If one of these ever flips, the
    inventory row's evidence is wrong and has to be rewritten, not patched."""
    rows = marts.verdict(FIXTURE)
    failed = [(name, detail) for name, ok, detail in rows if not ok]
    assert not failed, failed
    assert {name for name, _, _ in rows} == {
        "history", "labels", "first_print_pinned", "volatility", "rights",
        "schedule"}
    print("ok test_every_mechanical_gate_passes")


def test_the_wrapped_categories_carry_their_numbers():
    """444, 448 and 451 put the NAICS code on one spreadsheet row and every
    number on the next. A row-at-a-time parser silently drops exactly the
    categories a retail question would ask about."""
    for r in FIXTURE["releases"]:
        for code in ("444", "448", "451"):
            row = r["rows"].get(code)
            assert row and row["sa_advance"], (r["reference_month"], code)
    print("ok test_the_wrapped_categories_carry_their_numbers")


def test_the_advance_is_never_the_number_a_later_release_prints():
    """`resolve from the archived advance, never a revised value` is only a
    real rule if the two are different numbers. They always are: MARTS revises
    source data *and* runs concurrent seasonal adjustment, which moves every
    seasonally adjusted value every month whether or not anything was revised.
    """
    by = {r["reference_month"]: r for r in FIXTURE["releases"]}
    same = pairs = 0
    for ref, r in by.items():
        nxt = by.get(marts._next_month(ref))
        if not nxt:
            continue
        for c in marts.EIGHT:
            a = r["rows"][c]["sa_advance"]
            p = nxt["rows"][c]["sa_preliminary"]
            pairs += 1
            same += (a == p)
    assert pairs >= 280, pairs
    assert same == 0, f"{same}/{pairs} advance values survived unchanged"
    print("ok test_the_advance_is_never_the_number_a_later_release_prints")


def test_the_eight_do_not_contain_one_another():
    """A category and the category that contains it are not two observations.
    `4451 Grocery stores` sits inside `445 Food & beverage stores`; scoring
    both would count one sample twice and make the board look better resolved
    than it is, which is the objection `ssa/series.py` already records against
    registering `Adults` next to `All polls`."""
    assert len(marts.EIGHT) == 8
    for a in marts.EIGHT:
        for b in marts.EIGHT:
            assert a == b or not b.startswith(a), (a, b)
    print("ok test_the_eight_do_not_contain_one_another")


def _strict_workbook():
    """A one-cell xlsx in the strict-OOXML namespaces Census sometimes serves.

    Two of the fifty-four archived workbooks are spelled this way and openpyxl
    reads them as zero sheets without raising, so a pipeline built on it loses
    releases and never says so.
    """
    ns = "http://purl.oclc.org/ooxml/spreadsheetml/main"
    sheet = (f'<worksheet xmlns="{ns}"><sheetData>'
             '<row r="1"><c r="A1" t="s"><v>0</v></c>'
             '<c r="B1"><v>17.5</v></c></row>'
             '</sheetData></worksheet>')
    shared = f'<sst xmlns="{ns}"><si><t>441</t></si></sst>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", sheet)
        z.writestr("xl/sharedStrings.xml", shared)
    return zipfile.ZipFile(io.BytesIO(buf.getvalue()))


def test_strict_ooxml_workbooks_are_read_not_silently_skipped():
    grid = marts._grid(_strict_workbook(), 1)
    assert grid == {1: {"A": "441", "B": "17.5"}}, grid
    print("ok test_strict_ooxml_workbooks_are_read_not_silently_skipped")


def test_a_layout_change_fails_loudly_rather_than_reading_a_revision():
    """The advance column is identified from the workbook's own (a)/(p)/(r)
    markers. Lose them and the parse must stop, because the alternative is
    resolving a round against a revised figure and never noticing."""
    try:
        marts._columns({9: {"E": "(p)", "J": "(p)"}})
    except ValueError as e:
        assert "advance column" in str(e), e
    else:
        raise AssertionError("a workbook with no advance marker parsed anyway")
    print("ok test_a_layout_change_fails_loudly_rather_than_reading_a_revision")


if __name__ == "__main__":
    test_the_window_is_the_thirty_six_the_issue_asked_for()
    test_every_mechanical_gate_passes()
    test_the_wrapped_categories_carry_their_numbers()
    test_the_advance_is_never_the_number_a_later_release_prints()
    test_the_eight_do_not_contain_one_another()
    test_strict_ooxml_workbooks_are_read_not_silently_skipped()
    test_a_layout_change_fails_loudly_rather_than_reading_a_revision()
    print("7 passed")
