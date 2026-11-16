"""CTA monthly rail boardings (card chicago-cta-rail-monthly@v3): parse, calendar, reference, resolution.

No network. tests/fixtures/cta_daily/2026-11-16.json is one real answer to the
card's daily request (900-day window, as fetched before the window was widened
to 1,100 days), fetched once on 2026-11-16T03:13:47Z with the arena's honest
User-Agent: HTTP 200, 101,735 bytes, 824 rows, 2024-05-30 to 2026-08-31,
sha256 1b3d73e1...398a, unmodified.
"""
import hashlib
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import baselines, inventory, season, source_notices    # noqa: E402
from ssa.adapters import cta_daily as c                          # noqa: E402

PASSED = []
FIX = os.path.join(ROOT, "tests", "fixtures", "cta_daily", "2026-11-16.json")
BODY = open(FIX, "rb").read()
DAILY = c.parse(BODY)


def ok(fn):
    PASSED.append(fn.__name__)
    print("ok", fn.__name__)


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def _fake(captures):
    fetches = {d: {"fetched_at": at, "file": f"sources/cta_daily/{d}.json",
                   "sha256": d} for d, (at, _) in captures.items()}

    def reader(day, rows=None):
        return captures[day][1], fetches[day]
    return fetches, reader


def _month(month, rail=400000, odd=None):
    out = {}
    for d in c.days_of(month):
        out[d] = {"day_type": c.day_type(d), "bus": 600000, "rail": rail,
                  "total": 600000 + rail}
    if odd:
        out.update(odd)
    return out


def test_the_card_is_committed_byte_identical():
    body = open(os.path.join(ROOT, "docs", "data-cards",
                             "chicago-cta-rail-monthly.json"), "rb").read()
    assert hashlib.sha256(body).hexdigest() == c.CARD_SHA256
    card = json.loads(body)
    assert f"{card['id']}@v{card['revision']}" == c.CARD and card["revision"] == 3
    assert card["source"]["archive_policy"].startswith(
        "GRANTED by Haoyang Shang (project lead), 2026-11-15")
    assert source_notices.CHICAGO_DISCLAIMER in card["source"]["archive_policy"]
    assert inventory.rights_table()["cta_daily"] == inventory.APPROVED
    ok(test_the_card_is_committed_byte_identical)


def test_the_real_answer_parses_and_matches_the_calendar():
    assert hashlib.sha256(BODY).hexdigest().startswith("1b3d73e1")
    assert len(DAILY) == 824 and min(DAILY) == date(2024, 5, 30)
    assert max(DAILY) == date(2026, 8, 31) and c.newest_complete(DAILY) == "2026-08"
    # Every published day type since 2024-05-30 is the calendar's, and bus +
    # rail = total on every row: the six-holiday rule matches CTA's coding.
    months = sorted({c.month_key(d) for d in DAILY})
    assert all(c.anomalies(DAILY, m) == [] for m in months)
    assert DAILY[date(2026, 7, 3)]["day_type"] == "U"       # July 4 on a Saturday
    assert c.holidays(2026)[date(2026, 7, 3)] == "Independence Day"
    assert c.rail_total(DAILY, "2026-08") == 13462479
    ok(test_the_real_answer_parses_and_matches_the_calendar)


def test_parse_fails_loud():
    good = json.loads(BODY)
    for bad in ([], [good[0], good[0]], [dict(good[0], rail_boardings="x")],
                [{k: v for k, v in good[0].items() if k != "bus"}]):
        try:
            c.parse(json.dumps(bad).encode())
            raise AssertionError(f"accepted {bad!r:.60}")
        except ValueError:
            pass
    ok(test_parse_fails_loud)


def test_the_clock():
    assert c.lock_instant("2027-02") == utc(2027, 2, 1, 5, 59)       # Jan 31 23:59 CST
    assert c.release_instant("2027-02") == utc(2027, 4, 9, 14, 0)    # Feb 28 + 40 days
    assert c.void_instant("2027-02") == utc(2027, 6, 8, 23, 59)      # day 100
    assert c.query_params("2026-11-16")["$where"] == "service_date >= '2023-11-12T00:00:00'"
    ok(test_the_clock)


def test_the_reference_on_the_real_answer():
    # A September 2026 target against this answer (L = August, offset 1): the
    # only target the 900-day fixture holds every input for.
    ref = c.references(DAILY, "2026-09")
    assert ref["zero_point"] == c.ZERO_POINT and ref["offset"] == 1
    assert ref["ranked"] == {"mean": 13495767.8, "sd": 345503.9}
    assert ref["holidays"] == {"2026-09-07": "Labor Day"}
    assert ref["day_types"] == {"W": 21, "A": 4, "U": 5}
    assert ref["shown"]["seasonal_naive"] == c.rail_total(DAILY, "2025-09")
    # L older than M-5 and the round is not posted.
    try:
        c.references(DAILY, "2027-02")
        raise AssertionError("posted with L = M-6")
    except baselines.NotPostable as e:
        assert "older than M-5" in str(e)
    # With an offset of 3 the 900-day answer lacks the spread's oldest trend
    # months: the reason the window is 1,100 days.
    try:
        c.references(DAILY, "2026-11")
        raise AssertionError("posted without the spread's inputs")
    except baselines.NotPostable:
        pass
    assert baselines.normalize_spec(c.ZERO_POINT_SPEC)["name"] == c.ZERO_POINT
    ok(test_the_reference_on_the_real_answer)


def test_resolution_first_complete_snapshot_review_and_void():
    m = "2027-02"
    part = {d: v for d, v in _month(m).items() if d.day < 20}
    full = _month(m)
    odd = _month(m, odd={date(2027, 2, 15): {"day_type": "W", "bus": 1, "rail": 2, "total": 9}})
    later = _month(m, rail=500000)                     # a re-publication after resolution
    fetches, reader = _fake({"a": ("2027-03-20T00:37:00Z", part),
                             "b": ("2027-04-12T00:37:00Z", full),
                             "c": ("2027-04-20T00:37:00Z", later)})
    assert c.resolve(m, utc(2027, 2, 20), fetches, reader)["status"] == "pending"
    assert c.resolve(m, utc(2027, 3, 25), fetches, reader)["status"] == "pending"
    got = c.resolve(m, utc(2027, 5, 1), fetches, reader)
    assert got["status"] == "resolved" and got["value"] == 400000.0 * 28
    assert got["fetched_at"] == "2027-04-12T00:37:00Z"          # never the later re-publication
    fetches, reader = _fake({"b": ("2027-04-12T00:37:00Z", odd)})
    assert c.resolve(m, utc(2027, 5, 1), fetches, reader)["status"] == "review"
    fetches, reader = _fake({"a": ("2027-03-20T00:37:00Z", part)})
    assert c.resolve(m, utc(2027, 6, 1), fetches, reader)["status"] == "pending"
    assert c.resolve(m, utc(2027, 6, 9), fetches, reader)["status"] == "cancelled"
    ok(test_resolution_first_complete_snapshot_review_and_void)


def test_the_lock_reads_only_a_fresh_capture():
    fetches, reader = _fake({"x": ("2027-01-20T00:37:00Z", DAILY)})
    try:
        c.lock_reference("2027-02", fetches, reader)
        raise AssertionError("an 11-day-old capture served a lock")
    except baselines.NotPostable as e:
        assert "no capture" in str(e)
    ok(test_the_lock_reads_only_a_fresh_capture)


def test_the_rounds_follow_the_card_and_are_in_the_season():
    doc = json.load(open(os.path.join(ROOT, "docs", "data-cards",
                                      "chicago-cta-rail-monthly.rounds.json")))
    assert doc["card"] == c.CARD and doc["card_sha256"] == c.CARD_SHA256
    published = {r["round_id"]: r for r in json.load(
        open(os.path.join(ROOT, "questions", "season0.json")))["rounds"]}
    assert [r["round_id"] for r in doc["rounds"]] == [
        "cta-rail-2027-02", "cta-rail-2027-03", "cta-rail-2027-04"]
    for r in doc["rounds"]:
        m = r["round_id"][-7:]
        assert r["lock_at"] == c.lock_instant(m).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert r["release_at"] == c.release_instant(m).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert r["zero_point"] == c.ZERO_POINT_SPEC and not season.is_pilot(r)
        assert published[r["round_id"]] == r
    assert "Holidays" not in doc["rounds"][0]["question"]
    assert season.validate_document(doc) == []
    ok(test_the_rounds_follow_the_card_and_are_in_the_season)


def test_the_city_disclaimer_condition_is_met():
    """The lead accepted the City's terms; their first condition is the
    verbatim disclaimer wherever the data is shown. One table holds it, the
    site prints it for every CTA round."""
    row = inventory.INVENTORY["cta_daily"]
    assert "disclaimer" in row["conditions"]
    notice = source_notices.notice_for("cta_daily")
    assert notice["disclaimer"] == source_notices.CHICAGO_DISCLAIMER
    payload = source_notices.site_payload()
    assert payload["series"]["cta_rail_monthly"] == "cta_daily"
    assert source_notices.CHICAGO_DISCLAIMER.startswith(
        "This site provides applications using data that has been modified")
    ok(test_the_city_disclaimer_condition_is_met)


def test_the_capture_runs_from_the_rights_gated_collector_only():
    from tools import fetch_track_data
    assert [x for x in fetch_track_data.COLLECTORS if x[1] == "cta_daily"] == [
        ("cta_daily", "cta_daily", ["-m", "ssa.adapters.cta_daily", "capture"])]
    ok(test_the_capture_runs_from_the_rights_gated_collector_only)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"{len(PASSED)} passed")
