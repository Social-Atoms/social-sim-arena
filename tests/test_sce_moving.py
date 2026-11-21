"""NY Fed SCE moving expectations: the ten-cell profile (card nyfed-sce-moving@v2).

Run: PYTHONPATH=. python tests/test_sce_moving.py

No network. The synthetic workbooks are real xlsx files built with
tests/test_sce.py's packager; the replay reads the real archived vintages
under sources/sce/ (2026-10-05, 10-07 and 10-08), which is where the card's
claims about the release-day capture were measured.
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from ssa import profile_round, refresh, scoring, season  # noqa: E402
from ssa import series as series_registry                 # noqa: E402
from ssa.adapters import sce                               # noqa: E402
import test_sce as fx                                      # noqa: E402

CARD = os.path.join(ROOT, "docs", "data-cards", "nyfed-sce-moving.json")
ROUNDS = os.path.join(ROOT, "docs", "data-cards", "nyfed-sce-moving.rounds.json")
CARD_SHA = "bd4130d2434db06cd3620aa3d6cef9e92165e08c081c0fe38b31f1f0f2faf676"
REAL = os.path.join(ROOT, "sources", "sce")

COLS = "BCDEFGHIJKLMNOP"
DEMO_HEADERS = (
    "Age Under 40", "Age 40-60", "Age Over 60",
    "Education High School or Less", "Education Some College",
    "Education BA or Higher", "Income under 50k", "Income 50-100k",
    "Income Over 100k", "Numeracy Low", "Numeracy High", "Region West",
    "Region Midwest", "Region South", "Region Northeast")


def _months(n, start=(2013, 6)):
    y, m = start
    out = []
    for _ in range(n):
        out.append(f"{y:04d}{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def moving_workbook(months, value=lambda i, j: 10.0 + j + i / 100.0,
                    demo_headers=DEMO_HEADERS, national_months=None,
                    raw=None):
    """Both moving sheets, plus a decoy tab first so position cannot work."""
    strings = ["Moving expectations",
               "Mean probability of changing primary residence over the next "
               "12 months"] + list(demo_headers)
    sid = {s: i for i, s in enumerate(strings)}
    nat_rows = [[("A", sid["Moving expectations"], True)], [],
                [("B", sid[strings[1]], True)]]
    for i, ym in enumerate(national_months or months):
        nat_rows.append([("A", ym, False), ("B", 14.0 + i / 100.0, False)])
    demo_rows = [[("A", sid["Moving expectations"], True)], [],
                 [(COLS[j], sid[h], True) for j, h in enumerate(demo_headers)]]
    for i, ym in enumerate(months):
        cells = [("A", ym, False)]
        for j in range(len(demo_headers)):
            v = (raw or {}).get((ym, j), value(i, j))
            cells.append((COLS[j], v, False))
        demo_rows.append(cells)
    return fx._package([
        ("Inflation expectations", "worksheets/sheet1.xml",
         fx._sheet_xml([[("A", "201306", False)]])),
        ("Moving expectations Demo", "worksheets/sheet9.xml",
         fx._sheet_xml(demo_rows)),
        ("Moving expectations", "worksheets/sheet8.xml",
         fx._sheet_xml(nat_rows)),
    ], strings)


def _raises(fn, needle):
    try:
        fn()
    except (RuntimeError, ValueError) as e:
        assert needle in str(e), (needle, str(e))
        return
    raise AssertionError(f"expected an error containing {needle!r}")


# --- the reader --------------------------------------------------------------

def test_the_cells_are_read_by_header_and_kept_at_full_precision():
    body = moving_workbook(_months(3), raw={("201308", 0): "25.86676597595215"})
    rows = sce.parse_moving(body, min_rows=3)
    assert [r["date"] for r in rows] == ["2013-06-01", "2013-07-01", "2013-08-01"]
    assert rows[-1]["Age Under 40"] == 25.86676597595215
    # Income under 50k is column H, the seventh header: read by name, not by
    # its position among the ten scored cells.
    assert rows[0]["Income under 50k"] == 10.0 + 6
    assert rows[0]["Region Northeast"] == 10.0 + 14
    assert rows[1]["national"] == 14.01
    assert set(rows[0]) == {"date", "national", *sce.MOVING_CELLS}
    print("ok test_the_cells_are_read_by_header_and_kept_at_full_precision")


def test_a_missing_or_renamed_cell_header_is_loud():
    renamed = tuple("Region Pacific" if h == "Region West" else h
                    for h in DEMO_HEADERS)
    _raises(lambda: sce.parse_moving(moving_workbook(_months(3),
                                                     demo_headers=renamed),
                                     min_rows=3), "Region West")
    print("ok test_a_missing_or_renamed_cell_header_is_loud")


def test_a_value_outside_zero_to_one_hundred_or_not_a_number_is_refused():
    _raises(lambda: sce.parse_moving(moving_workbook(
        _months(3), raw={("201307", 12): "101.5"}), min_rows=3), "0.0 to 100.0")
    _raises(lambda: sce.parse_moving(moving_workbook(
        _months(3), raw={("201307", 13): "-0.5"}), min_rows=3), "0.0 to 100.0")
    _raises(lambda: sce.parse_moving(moving_workbook(
        _months(3), raw={("201307", 2): "n/a"}), min_rows=3), "not a number")
    print("ok test_a_value_outside_zero_to_one_hundred_or_not_a_number_is_refused")


def test_the_two_sheets_must_cover_the_same_months():
    _raises(lambda: sce.parse_moving(moving_workbook(
        _months(4), national_months=_months(3)), min_rows=3), "disagree")
    print("ok test_the_two_sheets_must_cover_the_same_months")


def test_the_real_workbook_parses_to_the_cards_numbers():
    """The card's verified values on the 2026-11-08 capture: 160 rows from
    2013-06 to 2026-09, and September's under-40 cell 25.86676597595215."""
    with open(os.path.join(REAL, "2026-11-08.xlsx"), "rb") as f:
        rows = sce.parse_moving(f.read())
    assert len(rows) == 160
    assert rows[0]["date"] == "2013-06-01" and rows[-1]["date"] == "2026-09-01"
    assert rows[-1]["Age Under 40"] == 25.86676597595215
    assert rows[-1]["national"] == 14.24825763702393
    # The inflation reader is untouched by the second one.
    with open(os.path.join(REAL, "2026-11-08.xlsx"), "rb") as f:
        assert sce.parse(f.read())[-1]["date"] == "2026-09-01"
    print("ok test_the_real_workbook_parses_to_the_cards_numbers")


# --- the registry ------------------------------------------------------------

def test_the_registry_holds_the_ten_cells_in_the_cards_order():
    cells = series_registry.SCE_MOVING_CELLS
    assert len(cells) == 10
    assert [series_registry.SERIES[c]["sce_moving"]["cell"] for c in cells] \
        == list(sce.MOVING_CELLS)
    assert {series_registry.SERIES[c]["source"] for c in cells} == {"sce_moving"}
    assert series_registry.SERIES["sce_moving_national"]["sce_moving"]["cell"] \
        == sce.MOVING_NATIONAL
    print("ok test_the_registry_holds_the_ten_cells_in_the_cards_order")


def test_build_all_reads_injected_rows_and_never_fetches():
    saved_series, saved_hist = series_registry.SERIES, sce.history
    series_registry.SERIES = {k: v for k, v in saved_series.items()
                              if v["source"] == "sce_moving"}
    sce.history = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("the moving cells must not fetch"))
    try:
        with open(os.path.join(REAL, "2026-11-08.xlsx"), "rb") as f:
            rows = sce.parse_moving(f.read())
        built = series_registry.build_all({"sce_moving": rows})
    finally:
        series_registry.SERIES, sce.history = saved_series, saved_hist
    assert len(built) == 11
    assert built["sce_moving_age_under_40"][-1] == \
        {"date": "2026-09-01", "value": 25.86676597595215}
    assert built["sce_moving_national"][-1]["value"] == 14.24825763702393
    print("ok test_build_all_reads_injected_rows_and_never_fetches")


# --- resolution: the first archived workbook carrying month M ----------------

def test_the_release_day_file_is_skipped_and_the_next_one_resolves():
    """Real archive: the September survey was released 2026-10-07, the file
    named for that day still holds August, and 2026-10-08 is the first that
    carries September (card, Resolution)."""
    vintages = [os.path.join(REAL, f) for f in
                ("2026-10-05.xlsx", "2026-10-07.xlsx", "2026-10-08.xlsx",
                 "2026-10-09.xlsx")]
    with open(vintages[1], "rb") as f:
        assert sce.parse_moving(f.read())[-1]["date"] == "2026-08-01"
    got = sce.moving_resolution("2026-09-01", "2026-10-07T15:00:00Z",
                                vintages=vintages)
    assert os.path.basename(got["vintage"]) == "2026-10-08.xlsx"
    assert got["values"]["Age Under 40"] == 25.86676597595215
    with open(vintages[2], "rb") as f:
        assert got["sha256"] == hashlib.sha256(f.read()).hexdigest()
    print("ok test_the_release_day_file_is_skipped_and_the_next_one_resolves")


def test_a_post_release_capture_resolves_on_the_release_day():
    tmp = tempfile.mkdtemp()
    try:
        old = moving_workbook(_months(3))
        new = moving_workbook(_months(4))
        paths = []
        for name, body in (("2013-10-01.xlsx", old),
                           ("2013-10-01.post.xlsx", new),
                           ("2013-10-02.xlsx", new)):
            p = os.path.join(tmp, name)
            with open(p, "wb") as f:
                f.write(body)
            paths.append(p)
        parse = lambda b: sce.parse_moving(b, min_rows=3)   # noqa: E731
        path, rows = sce.first_vintage_carrying("2013-09-01", "2013-10-01",
                                                paths, parse=parse)
        assert os.path.basename(path) == "2013-10-01.post.xlsx"
        # A vintage dated before the release day never resolves, even if it
        # somehow carried the month.
        path, _ = sce.first_vintage_carrying("2013-09-01", "2013-10-02",
                                             paths, parse=parse)
        assert os.path.basename(path) == "2013-10-02.xlsx"
        # A first file that already holds a later month: a whole release was
        # never archived, and M is not read out of a later file.
        _raises(lambda: sce.first_vintage_carrying(
            "2013-08-01", "2013-10-01", paths[1:], parse=parse),
            "no archived file holds the release")
    finally:
        shutil.rmtree(tmp)
    print("ok test_a_post_release_capture_resolves_on_the_release_day")


def test_no_vintage_waits_then_names_the_cancellation():
    vintages = [os.path.join(REAL, "2026-10-07.xlsx")]
    _raises(lambda: sce.moving_resolution(
        "2026-09-01", "2026-10-07T15:00:00Z", vintages=vintages,
        now=datetime(2026, 10, 9, tzinfo=timezone.utc)), "the round waits")
    _raises(lambda: sce.moving_resolution(
        "2026-09-01", "2026-10-07T15:00:00Z", vintages=vintages,
        now=datetime(2026, 10, 15, tzinfo=timezone.utc)),
        "cancel the round")
    print("ok test_no_vintage_waits_then_names_the_cancellation")


def test_the_survey_month_is_the_month_before_the_release():
    assert sce.survey_month("2026-12-07T16:00:00Z") == "2026-11-01"
    assert sce.survey_month("2027-01-08T16:00:00Z") == "2026-12-01"
    print("ok test_the_survey_month_is_the_month_before_the_release")


# --- the round, end to end ---------------------------------------------------

def _round(month="2026-09", lock="2026-10-05T15:00:00Z",
           release="2026-10-07T15:00:00Z"):
    return {"round_id": f"sce-{month}-moving", "tracker": "ny_fed_sce",
            "series": "sce_moving_age_under_40",
            "cells": list(series_registry.SCE_MOVING_CELLS),
            "lock_at": lock, "release_at": release,
            "target_type": "profile_energy"}


def _series_from(path):
    with open(path, "rb") as f:
        rows = sce.parse_moving(f.read())
    return {c: sce.moving_series(rows, series_registry.SERIES[c]["sce_moving"]["cell"])
            for c in series_registry.SCE_MOVING_CELLS}


def test_replay_september_2026_lock_reference_resolution_and_score():
    """The September survey as a round: the reference from the vintage held
    at lock (2026-10-05, newest month August), the outcome from the first
    vintage carrying September, the persistence profile scored on it."""
    r = _round()
    cells = profile_round.cells_for(r)
    at_lock = _series_from(os.path.join(REAL, "2026-10-05.xlsx"))
    hist = profile_round.frozen_history(r, at_lock, cells)
    ref = profile_round.reference_profile(r, hist, cells)
    assert ref["sce_moving_age_under_40"] == {
        "mean": round(22.15603637695312, 2), "sd": 1.5, "method": "persistence"}
    saved = sce.archived_vintages
    sce.archived_vintages = lambda: [os.path.join(REAL, f) for f in
                                     ("2026-10-07.xlsx", "2026-10-08.xlsx")]
    try:
        res = profile_round.resolution(r, {}, cells)
    finally:
        sce.archived_vintages = saved
    assert res["vintage"] == "sources/sce/2026-10-08.xlsx"
    assert res["values"]["sce_moving_age_under_40"] == 25.86676597595215
    assert res["observed_dates"]["sce_moving_region_west"] == "2026-09-01"
    energy = profile_round.score_cells(
        profile_round.persistence_cells(ref, cells), res["vector"])["energy"]
    assert energy > 0
    print(f"ok test_replay_september_2026_lock_reference_resolution_and_score "
          f"(persistence energy {energy:.3f})")


def test_a_reference_that_already_holds_the_survey_month_is_refused():
    """The date filter alone lets a month-labelled release into the null once
    it lands (2026-09-01 < the 2026-10-05 lock). The reference check refuses
    it rather than scoring the outcome against itself."""
    r = _round()
    cells = profile_round.cells_for(r)
    after = _series_from(os.path.join(REAL, "2026-10-08.xlsx"))
    hist = profile_round.frozen_history(r, after, cells)
    _raises(lambda: profile_round.reference_profile(r, hist, cells),
            "must end at 2026-08")
    row = {}
    refresh.attach_profile(row, r, after, now=None)
    assert row["scoreable"] is False and "must end at 2026-08" in row["baseline_note"]
    # And the same round frozen on the lock-time vintage is scoreable.
    row = {}
    refresh.attach_profile(row, r, _series_from(os.path.join(REAL, "2026-10-05.xlsx")))
    assert row["scoreable"] is True
    print("ok test_a_reference_that_already_holds_the_survey_month_is_refused")


def test_other_profile_sources_are_untouched_by_the_month_hook():
    assert profile_round.month_labelled(series_registry.PROFILE_CELLS) is None
    assert profile_round.month_labelled(series_registry.YOUGOV_XTAB_CELLS) is None
    print("ok test_other_profile_sources_are_untouched_by_the_month_hook")


# --- the card and the proposed rounds ----------------------------------------

def test_the_committed_card_is_the_approved_revision():
    with open(CARD, "rb") as f:
        body = f.read()
    assert hashlib.sha256(body).hexdigest() == CARD_SHA
    card = json.loads(body)
    assert card["status"] == "approved" and card["revision"] == 2
    print("ok test_the_committed_card_is_the_approved_revision")


def test_the_proposed_rounds_pass_the_season_validator():
    with open(ROUNDS) as f:
        doc = json.load(f)
    assert season.validate_document(doc) == []
    (r,) = doc["rounds"]
    assert r["round_id"] == "sce-2026-11-moving"
    assert r["lock_at"] == "2026-12-05T16:00:00Z"        # 11:00 EST
    assert r["release_at"] == "2026-12-07T16:00:00Z"
    assert r["cells"] == list(series_registry.SCE_MOVING_CELLS)
    assert sce.survey_month(r["release_at"]) == "2026-11-01"
    # A release day of a ny_fed_sce round is what makes sce.archive keep the
    # post-release capture; the proposed round must read as one.
    tmp = tempfile.mkdtemp()
    try:
        p = os.path.join(tmp, "season.json")
        with open(p, "w") as f:
            json.dump(doc, f)
        inst = sce.release_instants(p)
        assert inst["2026-12-07"] == datetime(2026, 12, 7, 16, tzinfo=timezone.utc)
    finally:
        shutil.rmtree(tmp)
    print("ok test_the_proposed_rounds_pass_the_season_validator")


def test_the_question_prints_every_cells_noise_floor():
    with open(ROUNDS) as f:
        (r,) = json.load(f)["rounds"]
    for c in r["cells"]:
        nf = series_registry.SERIES[c]["noise_floor"]
        assert f"{nf['m']:.2f}/{nf['s']:.2f}" in r["question"], c
    print("ok test_the_question_prints_every_cells_noise_floor")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
