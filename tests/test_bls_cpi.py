"""BLS CPI rent of primary residence (card bls-cpi-rent@v2). No network.

Run: PYTHONPATH=. python tests/test_bls_cpi.py

The fixture is one real API capture, committed under sources/bls_cpi/
(2026-11-09T0204Z, newest month 2026-09). Later captures are made from it by
editing its levels, so the parser under test reads the API's real shape.
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

from ssa import baselines, refresh, resolve, season          # noqa: E402
from ssa import series as series_registry                     # noqa: E402
from ssa.adapters import bls_cpi                               # noqa: E402

REAL = os.path.join(ROOT, "sources", "bls_cpi", "2026-11-09T0204Z.json")
CARD = os.path.join(ROOT, "docs", "data-cards", "bls-cpi-rent.json")
ROUNDS = os.path.join(ROOT, "docs", "data-cards", "bls-cpi-rent.rounds.json")
CARD_SHA = "9a5c42d2fc293ea433cd78590908b95dc017a7b76817ff6fbd2bdfa7b390d4e7"


def real_body():
    with open(REAL, "rb") as f:
        return f.read()


def edited(levels=None, add=None, status=None):
    """The real body with levels replaced ({"YYYY-MM": "value"}) or months
    added ({"YYYY-MM": "value"}, newest first as the API orders them)."""
    doc = json.loads(real_body())
    data = doc["Results"]["series"][0]["data"]
    for p in data:
        key = f"{p['year']}-{p['period'][1:]}"
        if levels and key in levels:
            p["value"] = levels[key]
    for key, value in sorted((add or {}).items()):
        data.insert(0, {"year": key[:4], "period": "M" + key[5:],
                        "periodName": "", "value": value, "footnotes": [{}]})
    if status:
        doc["status"] = status
    return json.dumps(doc).encode()


def _raises(fn, needle, kinds=(RuntimeError, ValueError)):
    try:
        fn()
    except kinds as e:
        assert needle in str(e), (needle, str(e))
        return
    raise AssertionError(f"expected an error containing {needle!r}")


class Sandbox:
    """Point the adapter's archive at a temp dir for one test."""

    def __enter__(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = (bls_cpi.ROOT, bls_cpi.ARCHIVE)
        bls_cpi.ROOT = self.tmp
        bls_cpi.ARCHIVE = os.path.join(self.tmp, "sources", "bls_cpi")
        return self

    def __exit__(self, *exc):
        bls_cpi.ROOT, bls_cpi.ARCHIVE = self.saved
        shutil.rmtree(self.tmp)

    def file(self, stamp, body, releases=None):
        """Capture `body` at `stamp` through the real capture path."""
        now = datetime.strptime(stamp, "%Y-%m-%dT%H%MZ").replace(tzinfo=timezone.utc)
        return bls_cpi.capture(now=now, fetch=lambda day: ((body, bls_cpi.URL),
                                                           bls_cpi.request_body(day)),
                               releases=releases or {}, record=False)


# --- parse ---------------------------------------------------------------------

def test_the_real_capture_parses_to_the_cards_numbers():
    p = bls_cpi.parse(real_body())
    assert p["newest"] == "2026-09"
    assert p["levels"]["2026-09"] == 450.429 and p["levels"]["2026-08"] == 449.321
    # Card: September 2026 = 100 x (450.429 / 449.321 - 1) = 0.2466.
    assert abs(bls_cpi.change(p["levels"], "2026-09") - 0.2466) < 5e-5
    assert p["levels"]["2025-10"] is None
    assert "lapse in appropriations" in p["missing"]["2025-10"]
    print("ok test_the_real_capture_parses_to_the_cards_numbers")


def test_october_and_november_2025_have_no_change_and_are_skipped():
    out = bls_cpi.changes(bls_cpi.parse(real_body()))
    months = [r["date"][:7] for r in out]
    assert "2025-10" not in months and "2025-11" not in months
    assert "2025-09" in months and "2025-12" in months
    assert out[-1] == {"date": "2026-09-01",
                       "value": bls_cpi.change(bls_cpi.parse(real_body())["levels"],
                                               "2026-09")}
    print("ok test_october_and_november_2025_have_no_change_and_are_skipped")


def test_parse_refuses_what_it_cannot_read():
    _raises(lambda: bls_cpi.parse(edited(status="REQUEST_NOT_PROCESSED")),
            "REQUEST_NOT_PROCESSED")
    _raises(lambda: bls_cpi.parse(edited(levels={"2026-09": "n/a"})),
            "not an index level")
    _raises(lambda: bls_cpi.parse(edited(levels={"2026-09": "4.5"})),
            "not a CPI index")
    _raises(lambda: bls_cpi.parse(b"<html>Access Denied</html>"), "not JSON")
    doc = json.loads(real_body())
    doc["Results"]["series"][0]["seriesID"] = "CUUR0000SEHA"
    _raises(lambda: bls_cpi.parse(json.dumps(doc).encode()), "0 CUSR0000SEHA")
    doc = json.loads(real_body())
    doc["Results"]["series"][0]["data"] = doc["Results"]["series"][0]["data"][:5]
    _raises(lambda: bls_cpi.parse(json.dumps(doc).encode()), "truncated")
    print("ok test_parse_refuses_what_it_cannot_read")


# --- when it asks ---------------------------------------------------------------

def _row(at, newest):
    return {"fetched_at": at, "newest": newest}


def test_one_capture_a_day_and_a_post_release_capture_on_release_day():
    rel = bls_cpi.release_instants(season_path=os.devnull)
    assert rel["2026-12-10"] == datetime(2026, 12, 10, 13, 30, tzinfo=timezone.utc)
    t = lambda s: datetime.strptime(s, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)  # noqa: E731
    assert bls_cpi.due(t("2026-12-09T06:17Z"), [], rel)[0]
    held = [_row("2026-12-09T06:17:00Z", "2026-10")]
    assert not bls_cpi.due(t("2026-12-09T18:17Z"), held, rel)[0]
    morning = [_row("2026-12-10T06:17:00Z", "2026-10")]
    ok, why = bls_cpi.due(t("2026-12-10T12:45Z"), morning, rel)
    assert not ok and "13:30" in why                     # before the release
    assert bls_cpi.due(t("2026-12-10T13:45Z"), morning, rel)[0]
    late = morning + [_row("2026-12-10T13:45:00Z", "2026-10")]
    assert bls_cpi.due(t("2026-12-10T18:17Z"), late, rel)[0]   # still old: retry
    done = morning + [_row("2026-12-10T13:45:00Z", "2026-11")]
    assert not bls_cpi.due(t("2026-12-10T18:17Z"), done, rel)[0]
    capped = [_row(f"2026-12-10T{14 + i}:00:00Z", "2026-10")
              for i in range(bls_cpi.MAX_PER_DAY)]
    assert not bls_cpi.due(t("2026-12-10T22:00Z"), capped, rel)[0]
    print("ok test_one_capture_a_day_and_a_post_release_capture_on_release_day")


def test_capture_files_body_and_meta_write_once_and_parses_first():
    with Sandbox() as box:
        row = box.file("2026-11-09T0204Z", real_body())
        assert row["newest"] == "2026-09" and row["file"] == \
            "sources/bls_cpi/2026-11-09T0204Z.json"
        assert row["sha256"] == hashlib.sha256(real_body()).hexdigest()
        assert row["request"]["seriesid"] == ["CUSR0000SEHA"]
        assert row["request"]["startyear"] == "2023"
        # The same day again: no request.
        calls = []
        again = bls_cpi.capture(
            now=datetime(2026, 11, 9, 18, 17, tzinfo=timezone.utc),
            fetch=lambda day: calls.append(day), releases={}, record=False)
        assert "skipped" in again and calls == []
        # A malformed body is not filed.
        _raises(lambda: box.file("2026-11-10T0617Z", b"<html>"), "not JSON")
        assert [r["fetched_at"] for r in bls_cpi.captures()] == \
            ["2026-11-09T02:04:00Z"]
        # And a tampered file is refused on read.
        with open(os.path.join(box.tmp, row["file"]), "ab") as f:
            f.write(b" ")
        _raises(lambda: bls_cpi.read(bls_cpi.captures()[0]), "sha256")
    print("ok test_capture_files_body_and_meta_write_once_and_parses_first")


# --- the lock rule and the resolution ----------------------------------------------

NOV = {"2026-10": "451.300"}
NOV_RELEASE = {"2026-10": "451.300", "2026-11": "452.400"}


def test_resolution_is_the_first_capture_after_the_release_carrying_m():
    rel = {"2026-12-10": datetime(2026, 12, 10, 13, 30, tzinfo=timezone.utc)}
    with Sandbox() as box:
        box.file("2026-12-10T0617Z", edited(add=NOV), rel)           # before
        box.file("2026-12-10T1345Z", edited(add=NOV_RELEASE), rel)   # the release
        # A later capture whose M-1 was revised (February-style) must not move
        # the answer: both levels come from the first response.
        box.file("2026-12-11T0617Z", edited(levels={"2026-10": "451.000"},
                                            add=NOV_RELEASE), rel)
        got = bls_cpi.resolution("2026-11", "2026-12-10T13:30:00Z")
        assert got["file"] == "sources/bls_cpi/2026-12-10T1345Z.json"
        assert got["value"] == 100.0 * (452.4 / 451.3 - 1.0)
        assert (got["level"], got["level_prev"]) == (452.4, 451.3)
    print("ok test_resolution_is_the_first_capture_after_the_release_carrying_m")


def test_resolution_waits_then_cancels_and_never_uses_a_two_month_change():
    with Sandbox() as box:
        box.file("2026-12-10T0617Z", edited(add=NOV))
        _raises(lambda: bls_cpi.resolution(
            "2026-11", "2026-12-10T13:30:00Z",
            now=datetime(2026, 12, 12, tzinfo=timezone.utc)), "the round waits")
        _raises(lambda: bls_cpi.resolution(
            "2026-11", "2026-12-10T13:30:00Z",
            now=datetime(2027, 1, 30, tzinfo=timezone.utc)), "cancelled")
    with Sandbox() as box:
        box.file("2026-12-10T1345Z", edited(add={"2026-10": "-", "2026-11": "452.400"}))
        _raises(lambda: bls_cpi.resolution("2026-11", "2026-12-10T13:30:00Z"),
                "no one-month change exists")
    print("ok test_resolution_waits_then_cancels_and_never_uses_a_two_month_change")


def test_the_lock_capture_must_be_fresh_and_carry_m_minus_one():
    lock = "2026-12-08T13:30:00Z"
    ok = [_row("2026-12-08T06:17:00Z", "2026-10")]
    assert bls_cpi.lock_capture(lock, "2026-11", ok) == ok[0]
    stale = [_row("2026-12-07T06:17:00Z", "2026-10")]
    _raises(lambda: bls_cpi.lock_capture(lock, "2026-11", stale),
            "posts no reference", (baselines.NotPostable,))
    behind = [_row("2026-12-08T06:17:00Z", "2026-09")]
    _raises(lambda: bls_cpi.lock_capture(lock, "2026-11", behind),
            "carries 2026-10", (baselines.NotPostable,))
    assert bls_cpi.reference_month("2026-12-10T13:30:00Z") == "2026-11"
    assert bls_cpi.reference_month("2027-01-13T13:30:00Z") == "2026-12"
    print("ok test_the_lock_capture_must_be_fresh_and_carry_m_minus_one")


# --- the round, end to end -------------------------------------------------------

def _pilot():
    with open(ROUNDS) as f:
        return json.load(f)["rounds"][0]


def test_replay_the_first_pilot_reference_lock_rule_and_resolution():
    """cpi-rent-2026-11 on synthetic November captures built from the real
    body: mean6 from the history frozen at lock, the 24-hour lock rule in
    refresh, and resolve.resolve_round taking the release-day capture."""
    r = _pilot()
    assert r["round_id"] == "cpi-rent-2026-11"
    rel = {"2026-12-10": datetime(2026, 12, 10, 13, 30, tzinfo=timezone.utc)}
    with Sandbox() as box:
        box.file("2026-12-08T0617Z", edited(add=NOV), rel)       # at lock: M-1 = Oct
        hist = bls_cpi.history(today="2026-12-08")
        assert hist[-1]["date"] == "2026-10-01"
        row = {"baselines": baselines.all_baselines(hist, r["release_at"][:10])}
        after_lock = datetime(2026, 12, 9, tzinfo=timezone.utc)
        refresh.attach_zero_point(row, r, hist, after_lock)
        ref = row["baselines"]["mean6"]
        assert ref["window"] == ["2026-05", "2026-06", "2026-07", "2026-08",
                                 "2026-09", "2026-10"]
        assert ref["spread"] == "rms_12" and ref["sd"] > 0
        # Without a capture in the 24 hours before the lock: no reference.
        stale_row = {"baselines": dict(row["baselines"])}
        with Sandbox():
            refresh.attach_zero_point(stale_row, r, hist, after_lock)
        assert stale_row["scoreable"] is False
        assert "posts no reference" in stale_row["baseline_note"]
        # Resolution, through the season resolver.
        box.file("2026-12-10T1345Z", edited(add=NOV_RELEASE), rel)
        res, why = resolve.resolve_round(
            r, {}, datetime(2026, 12, 10, 18, 17, tzinfo=timezone.utc))
        assert why is None, why
        assert res["value"] == 100.0 * (452.4 / 451.3 - 1.0)
        assert res["observed_date"] == "2026-11-01"
        assert res["vintage"] == "sources/bls_cpi/2026-12-10T1345Z.json"
    print("ok test_replay_the_first_pilot_reference_lock_rule_and_resolution")


def test_mean6_on_the_real_capture_matches_the_cards_measurement():
    """Card, Baseline: mean6 MAE about 0.061 over 2023-07..2026-09 (on a
    longer capture). On the committed capture's 24 postable targets the MAE
    is 0.059 and every postable reference has a positive measured spread."""
    with open(ROUNDS) as f:
        spec = json.load(f)["rounds"][0]["zero_point"]
    s = {p["date"][:7]: p["value"] for p in bls_cpi.changes(bls_cpi.parse(real_body()))}
    errs = []
    for t in sorted(s):
        try:
            ref = baselines.reference(spec, s, t)
        except baselines.NotPostable:
            continue
        assert ref["sd"] > 0
        errs.append(abs(s[t] - ref["mean"]))
    assert len(errs) == 24 and 0.05 < sum(errs) / len(errs) < 0.07
    print("ok test_mean6_on_the_real_capture_matches_the_cards_measurement")


def test_the_registry_reads_the_archive_never_the_network():
    saved = series_registry.SERIES
    series_registry.SERIES = {"cpi_rent_sa_mom": saved["cpi_rent_sa_mom"]}
    try:
        built = series_registry.build_all()
    finally:
        series_registry.SERIES = saved
    assert built["cpi_rent_sa_mom"][-1]["date"] >= "2026-09-01"
    assert series_registry.SERIES["cpi_rent_sa_mom"]["source"] == "bls_cpi"
    print("ok test_the_registry_reads_the_archive_never_the_network")


# --- the card, the rounds, the workflows ---------------------------------------------

def test_the_committed_card_is_the_approved_revision():
    with open(CARD, "rb") as f:
        body = f.read()
    assert hashlib.sha256(body).hexdigest() == CARD_SHA
    assert json.loads(body)["status"] == "approved"
    print("ok test_the_committed_card_is_the_approved_revision")


def test_the_proposed_rounds_pass_the_season_validator():
    with open(ROUNDS) as f:
        doc = json.load(f)
    every = doc["rounds"]
    assert [season.is_pilot(r) for r in every] == [True, True, False]
    assert season.validate_document({"rounds": every}) == []
    for r in every:
        assert r["zero_point"]["name"] == "mean6"
        assert r["zero_point"]["target"] == bls_cpi.reference_month(r["release_at"])
        assert r["release_at"][:10] in bls_cpi.RELEASES.values()
        assert r["release_at"].endswith("T13:30:00Z")      # 08:30 EST
    print("ok test_the_proposed_rounds_pass_the_season_validator")


def test_the_release_day_workflow_runs_at_0845_eastern_and_commits_only_its_archive():
    with open(os.path.join(ROOT, ".github", "workflows", "bls-cpi.yml")) as f:
        body = f.read()
    assert '"45 12,13 * * *"' in body
    assert "python -m ssa.adapters.bls_cpi capture --no-manifest" in body
    assert "git add -A sources/bls_cpi\n" in body
    assert "continue-on-error: true" in body
    with open(os.path.join(ROOT, ".github", "workflows", "refresh.yml")) as f:
        refresh_yml = f.read()
    step = refresh_yml.split("capture BLS CPI rent (non-blocking)", 1)[1][:200]
    assert "continue-on-error: true" in step
    assert "python -m ssa.adapters.bls_cpi capture" in step
    print("ok test_the_release_day_workflow_runs_at_0845_eastern_and_commits_only_its_archive")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
