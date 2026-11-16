"""Austin mixed-beverage receipts (card texas-mixed-beverage-austin@v3): parse, capture, reference, resolution.

No network. tests/fixtures/tx_mixbev/2026-11-16.json is one real answer to the
card's grouped request, fetched once on 2026-11-16T03:13:30Z with the arena's
honest User-Agent (HTTP 200, 3,521 bytes, sha256 9dd2508f...bf6e), unmodified.
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

from ssa import baselines, inventory, provenance, season      # noqa: E402
from ssa.adapters import tx_mixbev as t                        # noqa: E402

PASSED = []
FIX = os.path.join(ROOT, "tests", "fixtures", "tx_mixbev", "2026-11-16.json")
BODY = open(FIX, "rb").read()
MONTHS = t.parse(BODY)


def ok(fn):
    PASSED.append(fn.__name__)
    print("ok", fn.__name__)


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


class Sandbox:
    def __enter__(self):
        self.dir = tempfile.mkdtemp()
        self.saved = (provenance.ROOT, provenance.ARCHIVE, provenance.MANIFEST,
                      t.ROOT, t.ARCHIVE, t.FETCHES)
        provenance.ROOT = t.ROOT = self.dir
        provenance.ARCHIVE = os.path.join(self.dir, "sources")
        provenance.MANIFEST = os.path.join(provenance.ARCHIVE, "manifest.json")
        t.ARCHIVE = os.path.join(provenance.ARCHIVE, t.NAME)
        t.FETCHES = os.path.join(t.ARCHIVE, "fetches.json")
        return self

    def __exit__(self, *exc):
        (provenance.ROOT, provenance.ARCHIVE, provenance.MANIFEST,
         t.ROOT, t.ARCHIVE, t.FETCHES) = self.saved
        shutil.rmtree(self.dir)


def _fake(captures):
    """{day: (fetched_at, months)} -> (fetches, reader) without files."""
    fetches = {d: {"fetched_at": at, "file": f"sources/tx_mixbev/{d}.json",
                   "sha256": d} for d, (at, _) in captures.items()}

    def reader(day, rows=None):
        return captures[day][1], fetches[day]
    return fetches, reader


def test_the_card_is_committed_byte_identical():
    body = open(os.path.join(ROOT, "docs", "data-cards",
                             "texas-mixed-beverage-austin.json"), "rb").read()
    assert hashlib.sha256(body).hexdigest() == t.CARD_SHA256
    card = json.loads(body)
    assert f"{card['id']}@v{card['revision']}" == t.CARD and card["revision"] == 3
    assert card["source"]["archive_policy"].startswith(
        "GRANTED by Haoyang Shang (project lead), 2026-11-15")
    assert inventory.rights_table()["tx_mixbev"] == inventory.APPROVED
    ok(test_the_card_is_committed_byte_identical)


def test_the_real_answer_reproduces_the_card():
    assert hashlib.sha256(BODY).hexdigest().startswith("9dd2508f")
    assert len(MONTHS) == 27 and min(MONTHS) == "2024-09"
    # Card evidence, read 2026-11-09: 1,477 Austin records for Sep 2026; index
    # Aug 2026 99.4, Sep 2026 116.0 (rounded there, unrounded here).
    assert MONTHS["2026-09"]["n"] == 1477
    assert round(t.index(MONTHS, "2026-08"), 1) == 99.4
    assert round(t.index(MONTHS, "2026-09"), 1) == 115.8
    # liquor + wine + beer + cover does not equal total_receipts exactly in the
    # published data (about -1.9% in Sep 2026); the card logs it, never uses it.
    assert abs(t.component_gap(MONTHS["2026-09"])) < 0.05 * MONTHS["2026-09"]["tot"]
    ok(test_the_real_answer_reproduces_the_card)


def test_parse_fails_loud():
    good = json.loads(BODY)
    for bad in ([], {"d": 1}, [dict(good[0], d="2026-09-15T00:00:00.000")],
                [good[0], good[0]], [{k: v for k, v in good[0].items() if k != "n"}],
                [dict(good[0], tot="-5")]):
        try:
            t.parse(json.dumps(bad).encode())
            raise AssertionError(f"accepted {bad!r:.60}")
        except ValueError:
            pass
    ok(test_parse_fails_loud)


def test_the_clock_is_central_time():
    assert t.lock_instant("2027-02") == utc(2027, 2, 1, 5, 59)        # Jan 31 23:59 CST
    assert t.resolution_instant("2027-02") == utc(2027, 3, 31, 17, 0)  # Mar 31 12:00 CDT
    assert t.lock_instant("2027-04") == utc(2027, 4, 1, 4, 59)         # Mar 31 23:59 CDT
    assert t.query_params("2026-11-16")["$where"].endswith("'2024-09-07T00:00:00'")
    ok(test_the_clock_is_central_time)


def test_capture_files_the_body_write_once():
    calls = []
    with Sandbox():
        fetch = lambda day: calls.append(day) or (BODY, t.request_url(day))
        row = t.capture(now=utc(2026, 11, 16, 3, 13), fetch=fetch)
        assert row["file"] == "sources/tx_mixbev/2026-11-16.json"
        assert row["newest_month"] == "2026-11" and row["months"] == 27
        assert t.capture(now=utc(2026, 11, 16, 9), fetch=fetch) == row
        assert calls == ["2026-11-16"]
        months, again = t.read_capture("2026-11-16")
        assert months == MONTHS and again == row
    ok(test_capture_files_the_body_write_once)


def test_the_reference_and_policy_three():
    ref = t.references(MONTHS, "2026-11")
    assert ref["zero_point"] == t.ZERO_POINT
    assert ref["ranked"] == {"mean": 100.0, "sd": 5.945}
    assert ref["denominator"] == {"month": "2025-11", "total_receipts": 94256824.0,
                                  "records": 1453}
    # October 2026 held 310 records on 11-16 (partial): a December round would
    # need it for the spread, so it is not posted.
    try:
        t.references(MONTHS, "2026-12")
        raise AssertionError("posted on a partial month")
    except baselines.NotPostable as e:
        assert "2026-10" in str(e)
    # The spec the round names is the one the module computes, and it is valid.
    assert baselines.normalize_spec(t.ZERO_POINT_SPEC)["name"] == t.ZERO_POINT
    ok(test_the_reference_and_policy_three)


def test_the_lock_reads_only_a_fresh_capture_at_or_before_it():
    lock = t.lock_instant("2026-11")            # Oct 31 23:59 CDT = 04:59Z
    fetches, reader = _fake({"2026-10-31": ("2026-10-31T00:37:00Z", MONTHS),
                             "2026-11-01": ("2026-11-01T06:37:00Z", {})})
    out = t.lock_reference("2026-11", fetches, reader)
    assert out["fetched_at"] == "2026-10-31T00:37:00Z" and out["ranked"]["mean"] == 100.0
    stale, reader = _fake({"2026-10-20": ("2026-10-20T00:37:00Z", MONTHS)})
    try:
        t.lock_reference("2026-11", stale, reader)
        raise AssertionError("an 11-day-old capture served a lock")
    except baselines.NotPostable:
        pass
    assert lock == utc(2026, 11, 1, 4, 59)
    ok(test_the_lock_reads_only_a_fresh_capture_at_or_before_it)


def test_resolution_coverage_grace_and_void():
    base = {"2026-11": {"tot": 100.0, "liq": 0, "wine": 0, "beer": 0, "cover": 0, "n": 100},
            "2025-11": {"tot": 80.0, "liq": 0, "wine": 0, "beer": 0, "cover": 0, "n": 100}}
    m = "2026-11"
    lock_months = {"2025-11": base["2025-11"]}
    thin = {m: dict(base[m], n=96, tot=96.0)}
    full = {m: dict(base[m], n=98, tot=110.0)}
    start = t.resolution_instant(m)                      # 2026-12-31T18:00Z
    fetches, reader = _fake({"lock": ("2026-11-01T00:37:00Z", lock_months),
                             "early": ("2026-12-31T00:37:00Z", full),
                             "thin": ("2027-01-01T00:37:00Z", thin),
                             "ok": ("2027-01-01T06:37:00Z", full)})
    assert start == utc(2026, 12, 31, 18, 0)
    assert t.resolve(m, utc(2026, 12, 31, 12), fetches, reader)["status"] == "pending"
    got = t.resolve(m, utc(2027, 1, 1, 7), fetches, reader)
    assert got["status"] == "resolved" and got["value"] == 137.5     # 100 x 110 / 80
    assert got["fetched_at"] == "2027-01-01T06:37:00Z"               # the 96% one skipped
    fetches, reader = _fake({"lock": ("2026-11-01T00:37:00Z", lock_months),
                             "thin": ("2027-01-01T00:37:00Z", thin)})
    assert t.resolve(m, utc(2027, 1, 1, 12), fetches, reader)["status"] == "pending"
    assert t.resolve(m, utc(2027, 1, 2, 0), fetches, reader)["status"] == "cancelled"
    ok(test_resolution_coverage_grace_and_void)


def test_history_is_the_index_of_complete_months():
    fetches, reader = _fake({"2026-11-16": ("2026-11-16T03:13:30Z", MONTHS)})
    h = t.history(fetches, reader)
    assert h[-1] == {"date": "2026-09-01", "value": round(t.index(MONTHS, "2026-09"), 4)}
    assert h[0]["date"] == "2025-09-01" and len(h) == 13
    ok(test_history_is_the_index_of_complete_months)


def test_the_rounds_follow_the_card_and_are_in_the_season():
    doc = json.load(open(os.path.join(ROOT, "docs", "data-cards",
                                      "texas-mixed-beverage-austin.rounds.json")))
    assert doc["card"] == t.CARD and doc["card_sha256"] == t.CARD_SHA256
    published = {r["round_id"]: r for r in json.load(
        open(os.path.join(ROOT, "questions", "season0.json")))["rounds"]}
    rounds = doc["rounds"]
    assert [r["round_id"] for r in rounds] == [
        "austin-mixbev-2027-02", "austin-mixbev-2027-03", "austin-mixbev-2027-04"]
    for r in rounds:
        m = r["round_id"][-7:]
        assert r["lock_at"] == t.lock_instant(m).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert r["release_at"] == t.resolution_instant(m).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert r["zero_point"] == t.ZERO_POINT_SPEC and not season.is_pilot(r)
        assert published[r["round_id"]] == r
    assert rounds[0]["lock_at"] >= "2026-11-29"
    assert season.validate_document(doc) == []
    ok(test_the_rounds_follow_the_card_and_are_in_the_season)


def test_the_capture_runs_from_the_rights_gated_collector_only():
    from tools import fetch_track_data
    assert [c for c in fetch_track_data.COLLECTORS if c[1] == "tx_mixbev"] == [
        ("tx_mixbev", "tx_mixbev", ["-m", "ssa.adapters.tx_mixbev", "capture"])]
    wf = os.path.join(ROOT, ".github", "workflows")
    for fn in os.listdir(wf):
        assert "ssa.adapters.tx_mixbev" not in open(os.path.join(wf, fn)).read(), fn
    ok(test_the_capture_runs_from_the_rights_gated_collector_only)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"{len(PASSED)} passed")
