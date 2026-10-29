"""The European Commission consumer survey adapter. No network.

Run: PYTHONPATH=. python tests/test_ecbcs.py

Two kinds of fixture. The committed releases under `sources/ecbcs/` are real
bodies as the Commission served them, and the first-print rules are checked
against them, because the cases that matter (a revised aggregate, a country
missing from its own release) are things the publisher actually did. The
failure modes are checked against small workbooks built in memory with
zipfile, so the code under test is the code the pipeline runs.
"""
import io
import os
import shutil
import sys
import tempfile
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

from ssa import series as series_registry  # noqa: E402
from ssa.adapters import ecbcs  # noqa: E402

M = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"

# The committed releases, fixed before any test repoints the adapter.
COMMITTED = ecbcs.ARCHIVE

# Excel serials for month-end dates, which is how the MONTHLY sheet dates rows.
JAN_1985 = 31078


def _serial(i):
    """Month-end serial for the i-th month after January 1985."""
    from datetime import date, timedelta
    y, m = 1985 + (i + 1) // 12, (i + 1) % 12 + 1
    last = date(y, m, 1) - timedelta(days=1)
    return (last - date(1899, 12, 30)).days


def _col(n):
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def workbook(headers, rows, sheet="MONTHLY"):
    """headers: ['EU.CONS', ...]; rows: [[serial, v1, v2, ...]] -> release zip."""
    strings = list(headers) + ["NA"]
    cells = ['<row r="1">' + "".join(
        f'<c r="{_col(i + 1)}1" t="s"><v>{i}</v></c>'
        for i in range(len(headers))) + "</row>"]
    for r, row in enumerate(rows, start=2):
        cs = [f'<c r="A{r}"><v>{row[0]}</v></c>']
        for i, v in enumerate(row[1:], start=1):
            if v is None:
                continue
            if v == "NA":
                cs.append(f'<c r="{_col(i)}{r}" t="s"><v>{len(headers)}</v></c>')
            else:
                cs.append(f'<c r="{_col(i)}{r}"><v>{v}</v></c>')
        cells.append(f'<row r="{r}">' + "".join(cs) + "</row>")
    sheet_xml = (f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{M}">'
                 "<sheetData>" + "".join(cells) + "</sheetData></worksheet>")
    ss = (f'<?xml version="1.0" encoding="UTF-8"?><sst xmlns="{M}">'
          + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")
    wb = (f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="{M}" '
          f'xmlns:r="{R}"><sheets><sheet name="INFO" sheetId="1" r:id="rId1"/>'
          f'<sheet name="{sheet}" sheetId="2" r:id="rId2"/></sheets></workbook>')
    rels = (f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{PR}">'
            f'<Relationship Id="rId1" Type="{R}/worksheet" Target="worksheets/sheet1.xml"/>'
            f'<Relationship Id="rId2" Type="{R}/worksheet" Target="worksheets/sheet2.xml"/>'
            "</Relationships>")
    empty = (f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{M}">'
             "<sheetData/></worksheet>")
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/sharedStrings.xml", ss)
        z.writestr("xl/worksheets/sheet1.xml", empty)
        z.writestr("xl/worksheets/sheet2.xml", sheet_xml)
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w") as z:
        z.writestr("main_indicators_nace2.xlsx", inner.getvalue())
    return outer.getvalue()


def synthetic(months=3, last=None, decoy=True, shuffle=True, drop=None,
              value=None):
    """A release whose aggregates run from January 1985 for `months` months.

    By default the CONS columns are not in workbook order and a decoy
    `EU.INDU` column sits first, so a parser reading by position would return
    the industry indicator, not fail.
    """
    geos = list(ecbcs.REQUIRED)
    if shuffle:
        geos = geos[::-1]
    headers = (["EU.INDU"] if decoy else []) + [f"{g}.CONS" for g in geos]
    if drop:
        headers.remove(f"{drop}.CONS")
    rows = []
    for i in range(months):
        vals = ([5.0] if decoy else [])
        for h in headers[1 if decoy else 0:]:
            g = h.split(".")[0]
            base = -10.0 - ecbcs.REQUIRED.index(g)
            vals.append(value if value is not None and i == months - 1 else base - i / 10)
        rows.append([_serial(i)] + vals)
    if last is not None:
        rows[-1][0] = last
    return workbook(headers, rows)


def expect(fn, *needles):
    try:
        fn()
    except Exception as e:                                       # noqa: BLE001
        msg = str(e)
        for n in needles:
            assert n in msg, (n, msg)
        return e
    raise AssertionError(f"expected an error containing {needles}")


class TempArchive:
    """Point the adapter at a scratch archive, optionally seeded."""

    def __init__(self, *vintages):
        self.vintages = vintages

    def __enter__(self):
        self.saved = ecbcs.ARCHIVE
        self.dir = tempfile.mkdtemp()
        ecbcs.ARCHIVE = self.dir
        for v in self.vintages:
            shutil.copy(os.path.join(COMMITTED, f"{v}.zip"),
                        os.path.join(self.dir, f"{v}.zip"))
        return self.dir

    def __exit__(self, *exc):
        ecbcs.ARCHIVE = self.saved
        shutil.rmtree(self.dir)


def real(vintage):
    with open(os.path.join(COMMITTED, f"{vintage}.zip"), "rb") as f:
        return f.read()


# --- the committed releases --------------------------------------------------

def test_the_committed_releases_parse_and_end_on_their_own_month():
    for v in ecbcs.archived_vintages():
        rows = ecbcs.parse(real(v), vintage=v)
        for geo in ecbcs.ANCHORS:
            assert rows[geo][0]["date"] == "1985-01-01"
            assert rows[geo][-1]["date"] == ecbcs.month_of(v), (v, geo)
        assert set(ecbcs.REQUIRED) <= set(rows)
    assert {"2609", "2610"} <= set(ecbcs.archived_vintages())
    print("ok test_the_committed_releases_parse_and_end_on_their_own_month")


def test_the_october_2026_release_reads_what_the_commission_published():
    """The headline numbers of the 29 October 2026 release, as printed in
    its flash and full press releases: EU -17.3, euro area -17.9."""
    rows = ecbcs.parse(real("2610"), vintage="2610")
    assert rows["EU"][-1] == {"date": "2026-10-01", "value": -17.3}
    assert rows["EA"][-1] == {"date": "2026-10-01", "value": -17.9}
    assert len(rows["EU"]) == 502
    print("ok test_the_october_2026_release_reads_what_the_commission_published")


def test_the_first_print_is_pinned_and_a_later_revision_is_not_it():
    """September 2026 printed -15.8 for the EU; October's release revised it
    to -16.1 when Italy's data re-entered the aggregate. The round resolves on
    -15.8, which is the number public at the time."""
    assert ecbcs.parse(real("2610"))["EU"][-2] == {"date": "2026-09-01",
                                                   "value": -16.1}
    assert ecbcs.first_print("EU", "2026-09-01") == -15.8
    assert ecbcs.first_print("EU", "2026-10-01") == -17.3
    # A month whose release is not archived has no first print here, rather
    # than one borrowed from a later vintage.
    assert ecbcs.first_print("EU", "2026-08-01") is None
    print("ok test_the_first_print_is_pinned_and_a_later_revision_is_not_it")


def test_a_country_missing_from_its_own_release_has_no_first_print():
    """Italy's September 2026 value was not in the September release; it
    first appeared in October's. A row that stops short of the release month
    is data, and the first print for that month is absent, not backfilled."""
    sep = ecbcs.parse(real("2609"), vintage="2609")
    assert sep["IT"][-1]["date"] == "2026-08-01"
    assert ecbcs.first_print("IT", "2026-09-01") is None
    assert any(r["date"] == "2026-09-01"
               for r in ecbcs.parse(real("2610"))["IT"])
    print("ok test_a_country_missing_from_its_own_release_has_no_first_print")


# --- the parser's refusals ---------------------------------------------------

def test_columns_are_read_by_header_not_position():
    rows = ecbcs.parse(synthetic())
    assert rows["EU"][-1]["value"] == -10.2 and rows["IE"][-1]["value"] == -21.2
    assert "EU.INDU" not in rows and all(
        r["value"] != 5.0 for r in rows["EU"])
    print("ok test_columns_are_read_by_header_not_position")


def test_a_release_for_another_month_is_refused():
    expect(lambda: ecbcs.parse(real("2609"), vintage="2610"),
           "release 2610", "not that month's release")
    print("ok test_a_release_for_another_month_is_refused")


def test_a_missing_required_column_is_refused():
    expect(lambda: ecbcs.parse(synthetic(drop="PL")), "no CONS column",
           "PL")
    print("ok test_a_missing_required_column_is_refused")


def test_a_value_outside_the_balance_range_is_refused():
    expect(lambda: ecbcs.parse(synthetic(value=100.5)), "not a balance")
    print("ok test_a_value_outside_the_balance_range_is_refused")


def test_a_gap_in_an_aggregate_is_refused():
    body = synthetic(months=3, last=_serial(4))
    expect(lambda: ecbcs.parse(body), "history jumps from")
    print("ok test_a_gap_in_an_aggregate_is_refused")


def test_a_country_not_surveyed_that_month_is_skipped_not_zeroed():
    headers = [f"{g}.CONS" for g in ecbcs.REQUIRED]
    rows = [[_serial(0)] + [-10.0] * len(headers),
            [_serial(1)] + [-11.0] * 4 + ["NA"] + [-11.0] * (len(headers) - 5)]
    out = ecbcs.parse(workbook(headers, rows))
    geo = ecbcs.REQUIRED[4]
    assert [r["date"] for r in out[geo]] == ["1985-01-01"], out[geo]
    print("ok test_a_country_not_surveyed_that_month_is_skipped_not_zeroed")


def test_bodies_that_are_not_a_release_are_refused():
    expect(lambda: ecbcs.parse(b"<!DOCTYPE html><html>"), "not a readable zip")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.txt", "nothing")
    expect(lambda: ecbcs.parse(buf.getvalue()), "holds 0 workbooks")
    expect(lambda: ecbcs.parse(
        workbook(["EU.CONS"], [[JAN_1985, -1.0]], sheet="QUARTERLY")),
        "no sheet named 'MONTHLY'")
    print("ok test_bodies_that_are_not_a_release_are_refused")


# --- fetching ----------------------------------------------------------------

class Resp:
    def __init__(self, status, body=b""):
        self.status_code, self.content = status, body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def test_an_unpublished_release_redirects_and_is_not_followed():
    seen = {}

    def get(url, **kw):
        seen.update(kw, url=url)
        return Resp(301)
    with mock.patch.object(ecbcs.requests, "get", get):
        expect(lambda: ecbcs.fetch_bytes("2611"), "HTTP 301",
               "not published")
    assert seen["allow_redirects"] is False
    assert seen["url"].endswith("/nace2_ecfin_2611/main_indicators_sa_nace2.zip")
    assert "social-simulation-arena" in seen["headers"]["User-Agent"]
    with mock.patch.object(ecbcs.requests, "get",
                           lambda url, **kw: Resp(200, b"<html>")):
        expect(lambda: ecbcs.fetch_bytes("2610"), "not a zip")
    expect(lambda: ecbcs.url_for("2613"), "not a YYMM release id")
    print("ok test_an_unpublished_release_redirects_and_is_not_followed")


def test_a_held_release_costs_no_request():
    calls = []
    with TempArchive("2609", "2610"):
        rows = ecbcs.history(today="2026-10-30",
                             fetcher=lambda v: calls.append(v))
    assert calls == [] and rows["EU"][-1]["date"] == "2026-10-01"
    print("ok test_a_held_release_costs_no_request")


def test_a_new_release_is_validated_then_filed_write_once():
    calls = []

    def fetcher(v):
        calls.append(v)
        if v == "2611":
            raise ecbcs.NotPublished(v)
        return real("2610")
    with TempArchive("2609") as d:
        # Early November: November's release is not out, October's is not held.
        rows = ecbcs.history(today="2026-11-03", fetcher=fetcher)
        assert calls == ["2611", "2610"], calls
        assert sorted(os.listdir(d)) == ["2609.zip", "2610.zip"]
        assert rows["EU"][-1] == {"date": "2026-10-01", "value": -17.3}
        before = os.stat(os.path.join(d, "2610.zip")).st_mtime_ns
        ecbcs.archive(b"PK\x03\x04 something else", "2610")
        assert os.stat(os.path.join(d, "2610.zip")).st_mtime_ns == before
        assert ecbcs.first_print("EU", "2026-09-01") == -15.8
    print("ok test_a_new_release_is_validated_then_filed_write_once")


def test_a_release_that_does_not_parse_is_never_filed():
    with TempArchive("2609") as d:
        expect(lambda: ecbcs.history(today="2026-10-30",
                                     fetcher=lambda v: real("2609")),
               "release 2610", "not that month's release")
        assert os.listdir(d) == ["2609.zip"]
    print("ok test_a_release_that_does_not_parse_is_never_filed")


def test_a_dropped_connection_serves_the_archive_with_a_diagnostic():
    def fetcher(v):
        raise requests.ConnectionError("connection reset")
    diagnostics = []
    with TempArchive("2609"):
        rows = ecbcs.history(today="2026-10-30", fetcher=fetcher,
                             diagnostics=diagnostics)
    assert rows["EU"][-1]["date"] == "2026-09-01"
    assert diagnostics and diagnostics[0]["source"] == "ecbcs"
    assert "2609.zip" in diagnostics[0]["archive_evidence"]
    with TempArchive():
        expect(lambda: ecbcs.history(today="2026-10-30", fetcher=fetcher),
               "connection reset")
    print("ok test_a_dropped_connection_serves_the_archive_with_a_diagnostic")


def test_a_stale_archive_raises():
    def fetcher(v):
        raise ecbcs.NotPublished(v)
    with TempArchive("2609"):
        expect(lambda: ecbcs.history(today="2027-01-15", fetcher=fetcher),
               "months behind today")
        # Early January with November's release is normal life: December's
        # survey is published around 7 January.
        ecbcs.history(today="2026-12-05", fetcher=fetcher)
    print("ok test_a_stale_archive_raises")


def test_a_malformed_release_is_terminal_not_retried():
    """A body that arrived and does not parse must not be retried as if the
    network had dropped it; `ssa/reliability.py` classifies by wording."""
    from ssa import reliability
    errors = [
        expect(lambda: ecbcs.parse(b"<html>"), "not a readable zip"),
        expect(lambda: ecbcs.parse(real("2609"), vintage="2610"),
               "not that month's release"),
        expect(lambda: ecbcs.parse(synthetic(drop="PL")), "no CONS column"),
        expect(lambda: ecbcs.parse(synthetic(value=100.5)), "not a balance"),
        expect(lambda: ecbcs.parse(synthetic(last=_serial(4))),
               "history jumps from"),
    ]
    with mock.patch.object(ecbcs.requests, "get",
                           lambda url, **kw: Resp(200, b"<html>")):
        errors.append(expect(lambda: ecbcs.fetch_bytes("2610"), "not a zip"))
    for error in errors:
        assert reliability.terminal_error(error, source=True), error
    assert not reliability.terminal_error(
        requests.ConnectionError("connection reset"), source=True)
    print("ok test_a_malformed_release_is_terminal_not_retried")


# --- the registry ------------------------------------------------------------

def test_the_registry_builds_twelve_series_from_one_release():
    saved = series_registry.SERIES
    series_registry.SERIES = {k: v for k, v in saved.items()
                              if v["source"] == "ecbcs"}
    saved_history = ecbcs.history
    ecbcs.history = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("an injected source must not fetch"))
    try:
        built = series_registry.build_all(
            {"ecbcs": ecbcs.parse(real("2610"), vintage="2610")})
    finally:
        series_registry.SERIES = saved
        ecbcs.history = saved_history
    assert set(built) == set(series_registry.EC_CCI_SERIES)
    assert built["ec_cci_eu"][-1] == {"date": "2026-10-01", "value": -17.3}
    assert built["ec_cci_ea"][-1] == {"date": "2026-10-01", "value": -17.9}
    assert len({v[-1]["value"] for v in built.values()}) > 6
    print("ok test_the_registry_builds_twelve_series_from_one_release")


def test_registered_rows_say_what_resolves_and_carry_the_licence():
    assert series_registry.EC_CCI_CELLS == tuple(
        f"ec_cci_{g.lower()}" for g in ecbcs.REQUIRED[2:])
    for sid in series_registry.EC_CCI_SERIES:
        spec = series_registry.SERIES[sid]
        text = spec["methodology"]
        assert "first published" in text and "not rescored" in text, sid
        assert "2011/833/EU" in text and "CC BY 4.0" in text, sid
        assert "Brussels time" in spec["cadence"], sid
        # The persona panel is a US panel; this arm must refuse by name.
        assert series_registry.survey(sid) is None, sid
    print("ok test_registered_rows_say_what_resolves_and_carry_the_licence")


if __name__ == "__main__":
    test_the_committed_releases_parse_and_end_on_their_own_month()
    test_the_october_2026_release_reads_what_the_commission_published()
    test_the_first_print_is_pinned_and_a_later_revision_is_not_it()
    test_a_country_missing_from_its_own_release_has_no_first_print()
    test_columns_are_read_by_header_not_position()
    test_a_release_for_another_month_is_refused()
    test_a_missing_required_column_is_refused()
    test_a_value_outside_the_balance_range_is_refused()
    test_a_gap_in_an_aggregate_is_refused()
    test_a_country_not_surveyed_that_month_is_skipped_not_zeroed()
    test_bodies_that_are_not_a_release_are_refused()
    test_an_unpublished_release_redirects_and_is_not_followed()
    test_a_held_release_costs_no_request()
    test_a_new_release_is_validated_then_filed_write_once()
    test_a_release_that_does_not_parse_is_never_filed()
    test_a_dropped_connection_serves_the_archive_with_a_diagnostic()
    test_a_stale_archive_raises()
    test_a_malformed_release_is_terminal_not_retried()
    test_the_registry_builds_twelve_series_from_one_release()
    test_registered_rows_say_what_resolves_and_carry_the_licence()
    print("20 passed")
