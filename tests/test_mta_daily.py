"""MTA weekly subway riders (card mta-subway-weekly@v2): parse, ingest, reference, resolution.

No network. The fixture tests/fixtures/mta_daily/2026-11-09T005927Z.csv is the
real export the card's research fetched once (HTTP 200, 417,544 bytes, sha256
af6e2834...eae279, the hash the card cites), kept unmodified.
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import baselines, provenance                       # noqa: E402
from ssa.adapters import mta_daily as m                      # noqa: E402

PASSED = []
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "mta_daily", "2026-11-09T005927Z.csv")
FETCHED = datetime(2026, 11, 9, 0, 59, 27, tzinfo=timezone.utc)   # Sun 19:59 ET
BODY = open(FIXTURE, "rb").read()
PARSED = m.parse(BODY)
DAILY = m.subway(PARSED)


def ok(fn):
    PASSED.append(fn.__name__)
    print("ok", fn.__name__)


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


class Sandbox:
    def __enter__(self):
        self.dir = tempfile.mkdtemp()
        self.saved = (provenance.ROOT, provenance.ARCHIVE, provenance.MANIFEST,
                      m.ROOT, m.ARCHIVE, m.FETCHES)
        provenance.ROOT = m.ROOT = self.dir
        provenance.ARCHIVE = os.path.join(self.dir, "sources")
        provenance.MANIFEST = os.path.join(provenance.ARCHIVE, "manifest.json")
        m.ARCHIVE = os.path.join(provenance.ARCHIVE, m.NAME)
        m.FETCHES = os.path.join(m.ARCHIVE, "fetches.json")
        return self

    def write(self, name, body):
        p = os.path.join(self.dir, name)
        with open(p, "wb") as f:
            f.write(body)
        return p

    def __exit__(self, *exc):
        (provenance.ROOT, provenance.ARCHIVE, provenance.MANIFEST,
         m.ROOT, m.ARCHIVE, m.FETCHES) = self.saved
        shutil.rmtree(self.dir)


def csv_body(daily, modes=("Subway",), extra=()):
    lines = ["Date,Mode,Count"]
    for d in sorted(daily):
        for mode in modes:
            v = daily[d]
            lines.append(f"{d:%m/%d/%Y},{mode},{'' if v is None else v}")
    lines.extend(extra)
    return ("\n".join(lines) + "\n").encode()


# ------------------------------------------------------------------ the card

def test_the_card_is_committed_byte_identical():
    import hashlib
    p = os.path.join(ROOT, "docs", "data-cards", "mta-subway-weekly.json")
    body = open(p, "rb").read()
    assert hashlib.sha256(body).hexdigest() == m.CARD_SHA256
    card = json.loads(body)
    assert card["status"] == "approved" and card["revision"] == 2
    assert f"{card['id']}@v{card['revision']}" == m.CARD
    assert hashlib.sha256(BODY).hexdigest().startswith("af6e2834")
    ok(test_the_card_is_committed_byte_identical)


# ------------------------------------------------------------------ parse

def test_parse_reads_the_real_export():
    assert set(PARSED) == set(m.MODES) and m.unknown_modes(PARSED) == []
    newest = m.newest_posted(PARSED)
    assert newest["Subway"] == "2026-11-03" and newest["CRZ Entries"] == "2026-11-01"
    assert PARSED["Subway"][date(2026, 11, 4)] is None      # posted as empty rows
    assert date(2026, 11, 4) not in DAILY
    assert DAILY[date(2026, 11, 3)] == 4_062_339
    assert min(DAILY) == date(2020, 3, 1)
    ok(test_parse_reads_the_real_export)


def test_parse_fails_loud():
    good = csv_body({date(2026, 11, 2): 4_000_000})
    m.parse(good)
    bad = [
        good.replace(b"Date,Mode,Count", b"date,mode,count"),
        good.replace(b"Date,Mode,Count", b"Date,Mode,Count,Extra"),
        good + b"11/02/2026,Subway,5\n",                     # repeated
        good + b"2026-11-03,Subway,5\n",                     # date format
        good + b"11/03/2026,Subway,many\n",
        good + b"11/03/2026,Subway,-4\n",
        b"Date,Mode,Count\n11/02/2026,Bus,5\n",              # no Subway
        b"<html>Please wait</html>",
    ]
    for b in bad:
        try:
            m.parse(b)
            raise AssertionError(f"parsed {b[:60]!r}")
        except ValueError:
            pass
    # Policy (4): an unknown mode is reported, not refused.
    extra = m.parse(good + b"11/02/2026,Ferry,30000\n")
    assert m.unknown_modes(extra) == ["Ferry"]
    ok(test_parse_fails_loud)


# ------------------------------------------------------------------ the download checks

def test_the_download_window_and_the_friday_posting_check():
    f = m.check_download(BODY, FETCHED)
    assert f["window"] == m.REGULAR and f["weekend"] == "2026-11-07"
    assert f["lock_of"] == "2026-11-09" and f["resolves"] == "2026-10-26"
    # Saturday 00:00 ET is in, Friday 23:59 ET is out.
    assert m.classify(utc(2026, 11, 7, 5, 0))[0] == m.REGULAR
    for t in (utc(2026, 11, 7, 4, 59), utc(2026, 11, 11, 12, 0)):
        try:
            m.check_download(BODY, t)
            raise AssertionError(f"accepted a download at {t}")
        except ValueError:
            pass
    # Sunday 20:01 ET to Tuesday 12:00 ET: resolution-only.
    late = m.check_download(BODY, utc(2026, 11, 9, 1, 1))
    assert late["window"] == m.LATE and late["lock_of"] is None
    assert m.classify(utc(2026, 11, 10, 17, 0))[0] == m.LATE
    try:
        m.classify(utc(2026, 11, 10, 17, 1))
        raise AssertionError("accepted a download after Tuesday noon")
    except ValueError:
        pass
    # The same file claimed for the next weekend misses Friday's posting.
    try:
        m.check_download(BODY, utc(2026, 11, 14, 15, 0))
        raise AssertionError("an old file passed as next weekend's")
    except ValueError as e:
        assert "Friday posting missing" in str(e)
    # Daylight time: Saturday 00:00 EDT is 04:00Z.
    assert m.download_window(date(2026, 10, 3))[0] == utc(2026, 10, 3, 4, 0)
    ok(test_the_download_window_and_the_friday_posting_check)


def test_ingest_files_a_manual_capture_write_once():
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import ingest_mta_daily as tool
    with Sandbox() as sb:
        src = sb.write("dl.csv", BODY)
        out = tool.main([src, "--downloaded-at", "2026-11-09T00:59:27Z"],
                        now=utc(2026, 11, 9, 2))
        assert out["created"] and out["archive"] == "sources/mta_daily/2026-11-09.csv"
        row = m.load_fetches()["2026-11-09"]
        assert row["manual"] is True and row["window"] == m.REGULAR
        assert row["fetched_at"] == "2026-11-09T00:59:27Z" and row["url"] == m.URL
        assert row["newest_posted"]["Subway"] == "2026-11-03"
        assert json.load(open(provenance.MANIFEST))[m.NAME]["sha256"] == row["sha256"]
        again = m.ingest(src, FETCHED)
        assert again["created"] is False
        other = sb.write("other.csv", BODY + b"11/06/2026,AAR,1\n")
        for path, at in ((other, FETCHED),                        # same day, new bytes
                         (src, utc(2026, 11, 8, 15))):            # older day
            try:
                m.ingest(path, at)
                raise AssertionError("a second file for the record was accepted")
            except ValueError:
                pass
        try:
            tool.main([src, "--downloaded-at", "2026-11-14T15:00:00Z"],
                      now=utc(2026, 11, 14, 16))
            raise AssertionError("tool accepted a stale file")
        except SystemExit as e:
            assert "Friday posting missing" in str(e)
        assert sorted(os.listdir(m.ARCHIVE)) == ["2026-11-09.csv", "fetches.json"]
    ok(test_ingest_files_a_manual_capture_write_once)


def test_route_a_refuses_until_cleared():
    assert m.AUTOMATED_CLEARED is False
    called = []
    try:
        m.capture(fetch=lambda: called.append(1))
        raise AssertionError("route A ran")
    except RuntimeError as e:
        assert "not cleared" in str(e)
    assert not called
    ok(test_route_a_refuses_until_cleared)


def test_no_workflow_fetches_mta():
    wf_dir = os.path.join(ROOT, ".github", "workflows")
    for fn in os.listdir(wf_dir):
        for line in open(os.path.join(wf_dir, fn)):
            if "mta_daily" in line or "sayj-mze2" in line:
                assert line.lstrip().startswith("#"), (fn, line)
    src = open(os.path.join(wf_dir, "refresh.yml")).read()
    assert "#   run: python -m ssa.adapters.mta_daily capture" in src
    ok(test_no_workflow_fetches_mta)


# ------------------------------------------------------------------ the reference

def test_the_reference_reproduces_the_card():
    """Card baseline: 52 target weeks 2025-11-03 .. 2026-10-26, yoy4 MAE
    745,027 riders, 2.99% of the week; persistence on D-14 8.89%."""
    weeks = m.weekly(DAILY)
    s = baselines._Series(weeks, "week")
    t, err, rel, per = date(2025, 11, 3), [], [], []
    while t <= date(2026, 10, 26):
        p = baselines.yoy_k(s, t, 4, 1, 1)
        assert p["step_back"] == 0
        err.append(abs(weeks[t] - p["mean"]))
        rel.append(err[-1] / weeks[t])
        per.append(abs(weeks[t] - weeks[t - timedelta(days=14)]) / weeks[t])
        t += timedelta(days=7)
    assert len(err) == 52
    assert int(sum(err) / 52) == 745_027, sum(err) / 52
    assert round(100 * sum(rel) / 52, 2) == 2.99
    assert round(100 * sum(per) / 52, 2) == 8.89
    ok(test_the_reference_reproduces_the_card)


def test_references_from_one_capture_and_policy_one():
    D = date(2026, 11, 9)
    ref = m.references(DAILY, D)
    r = ref["ranked"]
    assert ref["zero_point"] == "yoy4" and r["method"] == "yoy4"
    assert r["window"] == ["2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26"]
    assert r["year_ago"] == ["2025-11-10"] and r["step_back"] == 0
    assert r["spread_targets"][0] == "2026-10-26" and len(r["spread_targets"]) == 13
    assert r["sd"] > 0
    assert r["mean"] == round(r["numerator"] / r["denominator"] * r["year_ago_value"], 2)
    assert ref["shown"]["window"] == ["2026-10-26"]
    # The unposted week D-7 never enters: dropping it changes nothing.
    cut = {d: v for d, v in DAILY.items() if d < D - timedelta(days=7)}
    assert m.references(cut, D)["ranked"] == r
    # Policy (1): one empty reference day, or one in last year's week, cancels.
    for hole in (D - timedelta(days=20), D - timedelta(days=364 + 20),
                 D - timedelta(days=361)):
        gap = dict(DAILY)
        del gap[hole]
        try:
            m.references(gap, D)
            raise AssertionError(f"posted with {hole} missing")
        except baselines.NotPostable as e:
            assert "policy (1)" in str(e)
    ok(test_references_from_one_capture_and_policy_one)


def test_lock_reference_reads_only_the_weekend_capture():
    with Sandbox() as sb:
        src = sb.write("dl.csv", BODY)
        m.ingest(src, FETCHED)
        ref = m.lock_reference(date(2026, 11, 9))
        assert ref["snapshot"] == "sources/mta_daily/2026-11-09.csv"
        assert ref["card"] == m.CARD and ref["ranked"]["method"] == "yoy4"
        # Not the lock capture of the following week, and nothing for it.
        try:
            m.lock_reference(date(2026, 11, 16))
            raise AssertionError("a week-old capture served a lock")
        except baselines.NotPostable:
            pass
    # A late (resolution-only) download is never a lock capture.
    fetches = {"2026-11-09": {"fetched_at": "2026-11-09T01:30:00Z", "window": m.LATE}}
    assert m.lock_capture(date(2026, 11, 9), fetches) is None
    ok(test_lock_reference_reads_only_the_weekend_capture)


# ------------------------------------------------------------------ resolution

def _fake(captures):
    """captures: {day: (fetched_at, window, daily)} -> (fetches, reader)."""
    fetches = {d: {"fetched_at": at, "window": w, "file": f"sources/mta_daily/{d}.csv",
                   "sha256": d} for d, (at, w, _) in captures.items()}

    def reader(day, _f=None):
        return {"Subway": captures[day][2]}, fetches[day]
    return fetches, reader


def test_resolution_scheduled_late_and_cancelled():
    W = date(2026, 11, 30)                      # P1: capture Sat 12-12
    full = {W + timedelta(days=i): 4_000_000 + i for i in range(7)}
    short = dict(full)
    short[W + timedelta(days=6)] = None
    assert m.resolution_window(W) == (utc(2026, 12, 12, 5, 0), utc(2026, 12, 15, 17, 0))
    fetches, reader = _fake({"2026-12-12": ("2026-12-12T16:00:00Z", m.REGULAR, full)})
    assert m.resolve(W, utc(2026, 12, 12, 4), fetches, reader)["status"] == "pending"
    got = m.resolve(W, utc(2026, 12, 13), fetches, reader)
    assert got["status"] == "resolved" and got["value"] == sum(full.values())
    # Saturday's file is short; a Monday resolution-only download stands in.
    fetches, reader = _fake({
        "2026-12-12": ("2026-12-12T16:00:00Z", m.REGULAR, short),
        "2026-12-14": ("2026-12-14T15:00:00Z", m.LATE, full)})
    assert m.resolve(W, utc(2026, 12, 13), fetches, reader)["status"] == "pending"
    got = m.resolve(W, utc(2026, 12, 16), fetches, reader)
    assert got["status"] == "resolved" and got["window"] == m.LATE
    # Nothing complete by Tuesday noon: cancelled, and a Wednesday file is ignored.
    fetches, reader = _fake({
        "2026-12-12": ("2026-12-12T16:00:00Z", m.REGULAR, short),
        "2026-12-16": ("2026-12-16T15:00:00Z", m.LATE, full)})
    assert m.resolve(W, utc(2026, 12, 20), fetches, reader)["status"] == "cancelled"
    ok(test_resolution_scheduled_late_and_cancelled)


def test_pilot_gate_and_the_rota():
    W = date(2026, 11, 30)
    a = {W + timedelta(days=i): 4_000_000 for i in range(7)}
    b = {d: v + 10_000 for d, v in a.items()}               # +0.25%
    c = {d: v + 30_000 for d, v in a.items()}               # +0.75%
    fetches, reader = _fake({"2026-12-12": ("2026-12-12T16:00:00Z", m.REGULAR, a),
                             "2026-12-19": ("2026-12-19T16:00:00Z", m.REGULAR, b),
                             "2026-12-20": ("2026-12-20T16:00:00Z", m.REGULAR, c)})
    assert m.pilot_change(W, "2026-12-12", "2026-12-19", fetches, reader)["settled"]
    assert not m.pilot_change(W, "2026-12-12", "2026-12-20", fetches, reader)["settled"]
    # Policy (7): two missed weekends in a row pause new rounds.
    fetches = {"2026-11-28": {"fetched_at": "2026-11-28T16:00:00Z", "window": m.REGULAR}}
    assert m.lapsed(fetches, utc(2026, 12, 1))["missed"] == []
    assert m.lapsed(fetches, utc(2026, 12, 8)) == {"missed": ["2026-12-05"], "paused": False}
    assert m.lapsed(fetches, utc(2026, 12, 15))["paused"] is True
    ok(test_pilot_gate_and_the_rota)


# ------------------------------------------------------------------ holidays and rounds

def test_holiday_flags_match_the_card():
    from ssa import us_holidays as h

    def names(start):
        return [r["holiday"] for r in h.misaligned(start, start + timedelta(days=6))]
    assert names(date(2026, 9, 7)) == ["Labor Day"]            # misaligned 2026
    assert names(date(2027, 5, 31)) == ["Memorial Day"]        # and 2027
    assert names(date(2025, 1, 20)) == ["Martin Luther King Jr. Day"]
    for aligned in (date(2025, 11, 24), date(2026, 11, 23), date(2027, 11, 22),
                    date(2026, 1, 19), date(2027, 1, 18), date(2026, 2, 16),
                    date(2026, 10, 12)):
        assert names(aligned) == [], aligned
    assert names(date(2026, 12, 21)) == ["Christmas Day"]
    assert names(date(2026, 12, 28)) == ["New Year's Day"]
    assert h.misaligned(date(2026, 12, 28), date(2027, 1, 3))[0]["date"] == "2027-01-01"
    ok(test_holiday_flags_match_the_card)


def test_the_proposed_rounds_follow_the_card():
    from ssa import season
    doc = json.load(open(os.path.join(ROOT, "docs", "data-cards",
                                      "mta-subway-weekly.rounds.json")))
    rounds = doc["rounds"]
    weeks = [date(2026, 11, 30) + timedelta(days=7 * i) for i in range(6)]
    assert [r["round_id"] for r in rounds] == \
        [f"mta-subway-{(w + timedelta(days=6)).isoformat()}" for w in weeks]
    assert [r["scored"] for r in rounds] == [False] * 5 + [True]
    for r, w in zip(rounds, weeks):
        assert r["lock_at"] == m.lock_instant(w).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert r["release_at"] == m.resolution_window(w)[0].strftime("%Y-%m-%dT%H:%M:%SZ")
        assert r["zero_point"] == "yoy4" and r["card_sha256"] == m.CARD_SHA256
        assert season._shape_problems(r) == []
        flagged = m.holiday_flags(w)
        assert ("Holiday flag" in r["pilot_note"]) == any(flagged.values())
    assert rounds[0]["lock_at"] == "2026-11-30T04:59:00Z"      # Sun 11-29 23:59 ET
    assert rounds[5]["lock_at"] == "2027-01-04T04:59:00Z"      # Sun 01-03 23:59 ET
    # Not publishable yet, for the reasons the PR lists: the series is not
    # registered (and then the rights and the 48-hour schedule rule).
    problems = season.validate_document(doc)
    assert problems and all("neither registered nor audited" in p for p in problems)
    ok(test_the_proposed_rounds_follow_the_card)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"{len(PASSED)} passed")
