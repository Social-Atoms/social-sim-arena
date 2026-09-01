"""YouGov workbook relationship mapping. Plain asserts, no network.

Run: PYTHONPATH=. python tests/test_yougov_xtab.py
"""
import html
import io
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa.adapters import yougov_xtab

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def string_cell(value):
    return f'<c t="inlineStr"><is><t>{html.escape(str(value))}</t></is></c>'


def number_cell(value):
    return f"<c><v>{value}</v></c>"


def worksheet(approve):
    rows = [
        ("Question text", "2026-08-31", True),
        ("Approve", approve, False),
        ("Disapprove", 0.5, False),
        ("Not sure", 0.1, False),
        ("Unweighted base", 480, False),
        ("Base", 500, False),
    ]
    xml_rows = []
    for label, value, is_string in rows:
        second = string_cell(value) if is_string else number_cell(value)
        xml_rows.append(f"<row>{string_cell(label)}{second}</row>")
    return (f'<worksheet xmlns="{MAIN}"><sheetData>'
            + "".join(xml_rows) + "</sheetData></worksheet>").encode()


def workbook(missing=None, unsafe=None):
    """A minimal OOXML book whose part numbers run opposite its sheet order."""
    names = [yougov_xtab.TOPLINE] + list(yougov_xtab.SCORED_CELLS)
    n = len(names)
    sheet_nodes, relationships, parts = [], [], {}
    expected = {}
    for i, name in enumerate(names, start=1):
        rid = f"rId{i}"
        part_number = n - i + 1
        target = f"worksheets/sheet{part_number}.xml"
        sheet_nodes.append(
            f'<sheet name="{html.escape(name)}" sheetId="{i}" r:id="{rid}"/>')
        if name != missing:
            if name == unsafe:
                target = "../docProps/core.xml"
            relationships.append(
                f'<Relationship Id="{rid}" Type="{yougov_xtab.WORKSHEET_REL}" '
                f'Target="{target}"/>')
        approve = round(0.20 + i / 100.0, 2)
        expected[name] = round(approve * 100, 4)
        parts[f"xl/worksheets/sheet{part_number}.xml"] = worksheet(approve)

    book = (f'<workbook xmlns="{MAIN}" xmlns:r="{OFFICE_REL}"><sheets>'
            + "".join(sheet_nodes) + "</sheets></workbook>").encode()
    rels = (f'<Relationships xmlns="{PACKAGE_REL}">'
            + "".join(relationships) + "</Relationships>").encode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/workbook.xml", book)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        for path, body in parts.items():
            z.writestr(path, body)
    return out.getvalue(), expected


def test_sheet_names_follow_relationship_ids_not_part_ordinals():
    blob, expected = workbook()
    waves = yougov_xtab.parse(blob)
    assert len(waves) == 1, waves
    wave = waves[0]
    assert wave["date"] == "2026-08-31"
    assert wave["question"] == "Question text"
    for name, approve in expected.items():
        assert wave["cells"][name]["approve"] == approve, (
            name, wave["cells"][name]["approve"], approve)


def test_a_sheet_with_no_relationship_is_refused():
    blob, _ = workbook(missing="Democrat")
    try:
        yougov_xtab.parse(blob)
        assert False, "a named sheet was guessed without its relationship"
    except ValueError as e:
        assert "Democrat" in str(e) and "relationship" in str(e), e


def test_a_relationship_cannot_escape_the_worksheet_directory():
    blob, _ = workbook(unsafe=yougov_xtab.TOPLINE)
    try:
        yougov_xtab.parse(blob)
        assert False, "an unsafe OOXML relationship target was read"
    except ValueError as e:
        assert "escapes xl/worksheets" in str(e), e


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print("ok", test.__name__)
    print(f"{len(tests)} passed")
