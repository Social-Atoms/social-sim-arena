"""TSA weekly checkpoint numbers (card tsa-checkpoint-weekly@v2): parse, capture, reference, resolution.

No network. tests/fixtures/tsa_volumes/2026-11-09/ holds the three pages the
card's research fetched once (2026-11-09T01:01:26Z to 01:02:22Z, HTTP 200,
197,250 / 190,187 / 190,270 bytes), kept unmodified.
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import requests                                               # noqa: E402

from ssa import baselines, provenance                        # noqa: E402
from ssa.adapters import tsa_volumes as t                     # noqa: E402

PASSED = []
FIX = os.path.join(ROOT, "tests", "fixtures", "tsa_volumes", "2026-11-09")
BODIES = {k: open(os.path.join(FIX, f"{k}.html"), "rb").read()
          for k in ("current", "2025", "2024")}
PAGES = {k: t.parse_page(b) for k, b in BODIES.items()}
DAILY, _ = t.merge(PAGES)
CHALLENGE = b"<html><body><h1>Request unsuccessful.</h1></body></html>"


def ok(fn):
    PASSED.append(fn.__name__)
    print("ok", fn.__name__)


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


class Sandbox:
    def __enter__(self):
        self.dir = tempfile.mkdtemp()
        self.saved = (provenance.ROOT, provenance.ARCHIVE, provenance.MANIFEST,
                      t.ROOT, t.ARCHIVE, t.FETCHES, t.FAILURES)
        provenance.ROOT = t.ROOT = self.dir
        provenance.ARCHIVE = os.path.join(self.dir, "sources")
        provenance.MANIFEST = os.path.join(provenance.ARCHIVE, "manifest.json")
        t.ARCHIVE = os.path.join(provenance.ARCHIVE, t.NAME)
        t.FETCHES = os.path.join(t.ARCHIVE, "fetches.json")
        t.FAILURES = os.path.join(t.ARCHIVE, "failures.json")
        return self

    def __exit__(self, *exc):
        (provenance.ROOT, provenance.ARCHIVE, provenance.MANIFEST,
         t.ROOT, t.ARCHIVE, t.FETCHES, t.FAILURES) = self.saved
        shutil.rmtree(self.dir)


def server(bodies, log):
    """A fake fetch: URL -> (body, url), logging each request."""
    by_url = {t.page_url(): bodies["current"]}
    by_url.update({t.page_url(int(k)): v for k, v in bodies.items() if k != "current"})

    def fetch(url):
        log.append(url)
        if url not in by_url:
            raise requests.HTTPError(f"tsa_volumes: HTTP 404 from {url}")
        return by_url[url], url
    return fetch


# ------------------------------------------------------------------ the card

def test_the_card_is_committed_byte_identical():
    import hashlib
    body = open(os.path.join(ROOT, "docs", "data-cards",
                             "tsa-checkpoint-weekly.json"), "rb").read()
    assert hashlib.sha256(body).hexdigest() == t.CARD_SHA256
    card = json.loads(body)
    assert card["status"] == "approved" and f"{card['id']}@v{card['revision']}" == t.CARD
    ok(test_the_card_is_committed_byte_identical)


# ------------------------------------------------------------------ parse

def test_parse_reads_the_three_real_pages():
    cur = PAGES["current"]
    assert len(cur) == 309 and min(cur) == date(2026, 1, 1) and max(cur) == date(2026, 11, 5)
    assert cur[date(2026, 11, 5)] == 2_610_670
    assert len(PAGES["2025"]) == 365 and len(PAGES["2024"]) == 366
    assert min(DAILY) == date(2024, 1, 1) and len(DAILY) == 309 + 365 + 366
    ok(test_parse_reads_the_three_real_pages)


def test_a_page_that_is_not_the_table_is_refused():
    for b in (CHALLENGE, b"<table><tr><th>Day</th><th>Numbers</th></tr></table>",
              BODIES["current"] + BODIES["2025"],                # two tables
              b"<table><tr><th>Date</th><th>Numbers</th></tr></table>"):
        try:
            t.parse_page(b)
            raise AssertionError(f"parsed {b[:50]!r}")
        except t.NotTheTable:
            pass
    row = (b"<table><tr><th>Date</th><th>Numbers</th></tr>"
           b"<tr><td>11/5/2026</td><td>%s</td></tr></table>")
    assert t.parse_page(row % b"2,610,670") == {date(2026, 11, 5): 2_610_670}
    for bad in (b"n/a", b"2.6M"):
        try:
            t.parse_page(row % bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    ok(test_a_page_that_is_not_the_table_is_refused)


def test_merge_prefers_the_current_page_and_logs_conflicts():
    d = date(2026, 1, 1)
    merged, conflicts = t.merge({"2026": {d: 100}, "current": {d: 101}})
    assert merged[d] == 101 and conflicts == [
        {"date": "2026-01-01", "page": "current", "was": 100, "now": 101}]
    merged, _ = t.merge({"current": {d: 0}})               # zero is not posted
    assert d not in merged
    ok(test_merge_prefers_the_current_page_and_logs_conflicts)


# ------------------------------------------------------------------ capture

def test_capture_files_three_pages_write_once():
    log, naps = [], []
    with Sandbox():
        row = t.capture(now=utc(2026, 11, 9, 1, 1, 26), fetch=server(BODIES, log),
                        sleep=naps.append)
        assert log == [t.BASE, f"{t.BASE}/2025", f"{t.BASE}/2024"]
        assert naps == [10, 10]
        assert row["newest_day"] == "2026-11-05"
        assert sorted(row["pages"]) == ["2024", "2025", "current"]
        cur = row["pages"]["current"]
        assert cur["file"] == "sources/tsa_volumes/2026-11-09/current.html"
        assert cur["rows"] == 309 and cur["year"] == 2026
        for k, pg in row["pages"].items():
            with open(os.path.join(t.ROOT, pg["file"]), "rb") as f:
                assert f.read() == BODIES[k]                 # as served
        man = json.load(open(provenance.MANIFEST))[t.NAME]
        assert man["file"] == cur["file"] and man["sha256"] == cur["sha256"]
        # A second run the same UTC day costs no request.
        assert t.capture(now=utc(2026, 11, 9, 6, 17), fetch=server(BODIES, log)) == row
        assert len(log) == 3
        daily, again = t.read_capture("2026-11-09")
        assert daily == DAILY and again == row
    ok(test_capture_files_three_pages_write_once)


def test_a_failed_capture_files_nothing_and_is_recorded():
    with Sandbox():
        bodies = dict(BODIES, **{"2025": CHALLENGE})
        try:
            t.capture(now=utc(2026, 11, 9, 1), fetch=server(bodies, []), sleep=lambda s: None)
            raise AssertionError("a challenge page was filed")
        except t.NotTheTable:
            pass
        assert not os.path.exists(os.path.join(t.ARCHIVE, "2026-11-09"))
        assert t.load_fetches() == {}
        fails = json.load(open(t.FAILURES))
        assert fails[0]["url"] == f"{t.BASE}/2025" and "NotTheTable" in fails[0]["error"]
        # A 403 is raised as it came, recorded, never retried.
        calls = []

        def refused(url):
            calls.append(url)
            raise requests.HTTPError(f"tsa_volumes: HTTP 403 from {url}")
        try:
            t.capture(now=utc(2026, 11, 9, 2), fetch=refused, sleep=lambda s: None)
            raise AssertionError("a 403 passed")
        except requests.HTTPError:
            pass
        assert calls == [t.BASE] and len(json.load(open(t.FAILURES))) == 2
        # Fewer rows than the previous capture of the same year: failed.
        t.capture(now=utc(2026, 11, 9, 3), fetch=server(BODIES, []), sleep=lambda s: None)
        cut = BODIES["current"].replace(
            b'<tr><td class="text-align-center">11/5/2026</td><td class="text-align-center">2,610,670</td></tr>', b"")
        cut = cut.replace(
            b'<tr><td class="text-align-center">11/4/2026</td><td class="text-align-center">2,107,186</td></tr>', b"")
        assert len(t.parse_page(cut)) == 307
        try:
            t.capture(now=utc(2026, 11, 10, 1), fetch=server(dict(BODIES, current=cut), []),
                      sleep=lambda s: None)
            raise AssertionError("a shrunken page was filed")
        except t.NotTheTable as e:
            assert "fewer than the 309" in str(e)
        assert sorted(t.load_fetches()) == ["2026-11-09"]
    ok(test_a_failed_capture_files_nothing_and_is_recorded)


def test_january_asks_for_the_two_years_before_the_newest_date():
    jan = (b"<table><tr><th>Date</th><th>Numbers</th></tr>"
           b"<tr><td>1/2/2027</td><td>2,400,000</td></tr>"
           b"<tr><td>1/1/2027</td><td>2,300,000</td></tr></table>")
    p2026 = BODIES["current"]          # stands in for the moved 2026 page
    log = []
    with Sandbox():
        row = t.capture(now=utc(2027, 1, 3, 1), sleep=lambda s: None,
                        fetch=server({"current": jan, "2026": p2026,
                                      "2025": BODIES["2025"]}, log))
        assert row["pages"]["current"]["year"] == 2027
        assert log == [t.BASE, f"{t.BASE}/2026", f"{t.BASE}/2025"]
    ok(test_january_asks_for_the_two_years_before_the_newest_date)


def test_the_refresh_captures_daily_and_never_blocks_on_it():
    import yaml
    with open(os.path.join(ROOT, ".github", "workflows", "refresh.yml")) as f:
        wf = yaml.safe_load(f)
    steps = [st for job in wf["jobs"].values() for st in job.get("steps", [])]
    runs = [st.get("run", "") for st in steps]
    i = runs.index("python -m ssa.adapters.tsa_volumes capture")
    assert steps[i].get("continue-on-error") is True
    assert i < runs.index("python -m ssa.refresh"), "capture after the refresh"
    ok(test_the_refresh_captures_daily_and_never_blocks_on_it)


# ------------------------------------------------------------------ the reference

def test_the_reference_reproduces_the_card():
    """Card baseline: 52 target weeks 2025-11-03 .. 2026-10-26, MAPE 1.72%,
    MAE 284,746; the week of 2026-11-09 has a reference of 15.62 million,
    sd 0.29 million."""
    s = baselines._Series(DAILY, "day")
    weeks = t.weekly(DAILY)
    w, rel, err = date(2025, 11, 3), [], []
    while w <= date(2026, 10, 26):
        p = baselines.yoy_k(s, w, 32, 3, 0, "fixed", None, "start", 7)
        err.append(abs(weeks[w] - p["mean"]))
        rel.append(err[-1] / weeks[w])
        w += timedelta(days=7)
    assert len(err) == 52
    assert round(100 * sum(rel) / 52, 2) == 1.72 and int(sum(err) / 52) == 284_746
    ref = t.references(DAILY, date(2026, 11, 9))["ranked"]
    assert round(ref["mean"] / 1e6, 2) == 15.62 and round(ref["sd"] / 1e6, 2) == 0.29
    ok(test_the_reference_reproduces_the_card)


def test_the_end_moves_back_never_forward_and_gaps_refuse():
    D = date(2026, 11, 9)
    full = t.references(DAILY, D)["ranked"]
    assert full["window"][0] == "2026-10-05" and full["end_day"] == "2026-11-05"
    assert full["k_used"] == 32 and full["spread_targets"][0] == "2026-10-26"
    # Thursday not posted (holiday delay): the end moves back to Wednesday.
    late = {d: v for d, v in DAILY.items() if d != date(2026, 11, 5)}
    held = t.references(late, D)["ranked"]
    assert held["end_day"] == "2026-11-04" and held["k_used"] == 31
    assert held["sd"] == full["sd"]                 # past weeks keep W-4
    # A posted Friday never moves it forward.
    extra = {**DAILY, date(2026, 11, 6): 2_500_000}
    assert t.references(extra, D)["ranked"]["end_day"] == "2026-11-05"
    # A date missing inside the range: not posted.
    gap = {d: v for d, v in DAILY.items() if d != date(2026, 10, 20)}
    try:
        t.references(gap, D)
        raise AssertionError("posted with a hole")
    except baselines.NotPostable:
        pass
    ok(test_the_end_moves_back_never_forward_and_gaps_refuse)


def test_lock_reference_uses_the_weekend_capture_only():
    with Sandbox():
        t.capture(now=utc(2026, 11, 9, 1, 1, 26), fetch=server(BODIES, []),
                  sleep=lambda s: None)
        ref = t.lock_reference(date(2026, 11, 9))       # Sun 20:01 ET: in window
        assert ref["fetched_at"] == "2026-11-09T01:01:26Z" and ref["card"] == t.CARD
        assert "sources/tsa_volumes/2026-11-09/current.html" in ref["snapshot"]
        assert ref["zero_point"] == t.ZERO_POINT
        try:
            t.lock_reference(date(2026, 11, 16))         # a week-old capture
            raise AssertionError("a stale capture served a lock")
        except baselines.NotPostable:
            pass
    assert t.lock_window(date(2026, 11, 9)) == (utc(2026, 11, 7, 5), utc(2026, 11, 9, 4, 59))
    ok(test_lock_reference_uses_the_weekend_capture_only)


# ------------------------------------------------------------------ resolution

def _fake(captures):
    fetches = {d: {"fetched_at": at,
                   "pages": {"current": {"file": f"sources/tsa_volumes/{d}/current.html",
                                         "sha256": d}}}
               for d, (at, _) in captures.items()}

    def reader(day, _f=None):
        return captures[day][1], fetches[day]
    return fetches, reader


def test_resolution_waits_for_tuesday_noon_and_cancels_after_friday():
    W = date(2026, 12, 7)
    full = {W + timedelta(days=i): 2_400_000 + i for i in range(7)}
    short = {d: v for d, v in full.items() if d != W + timedelta(days=6)}
    assert t.resolution_instant(W) == utc(2026, 12, 15, 17) and \
        t.grace_end(W) == utc(2026, 12, 18, 17)
    fetches, reader = _fake({"2026-12-15": ("2026-12-15T00:17:00Z", full),   # Mon evening
                             "2026-12-16": ("2026-12-16T00:17:00Z", full)})
    assert t.resolve(W, utc(2026, 12, 15, 12), fetches, reader)["status"] == "pending"
    got = t.resolve(W, utc(2026, 12, 16, 1), fetches, reader)
    assert got["status"] == "resolved" and got["fetched_at"] == "2026-12-16T00:17:00Z"
    assert got["value"] == sum(full.values())
    fetches, reader = _fake({"2026-12-16": ("2026-12-16T00:17:00Z", short),
                             "2026-12-19": ("2026-12-19T00:17:00Z", full)})
    assert t.resolve(W, utc(2026, 12, 17), fetches, reader)["status"] == "pending"
    assert t.resolve(W, utc(2026, 12, 20), fetches, reader)["status"] == "cancelled"
    # Pilot revision gate, 0.2%.
    a = full
    b = {d: v + 600 for d, v in a.items()}           # +0.025%
    c = {d: v + 6000 for d, v in a.items()}          # +0.25%
    fetches, reader = _fake({"x": ("2026-12-16T00:17:00Z", a), "y": ("2026-12-19T00:17:00Z", b),
                             "z": ("2026-12-20T00:17:00Z", c)})
    assert t.pilot_change(W, "x", "y", fetches, reader)["settled"]
    assert not t.pilot_change(W, "x", "z", fetches, reader)["settled"]
    ok(test_resolution_waits_for_tuesday_noon_and_cancels_after_friday)


def test_the_proposed_rounds_follow_the_card():
    from ssa import season
    doc = json.load(open(os.path.join(ROOT, "docs", "data-cards",
                                      "tsa-checkpoint-weekly.rounds.json")))
    rounds = doc["rounds"]
    weeks = [date(2026, 11, 30) + timedelta(days=7 * i) for i in range(6)]
    assert [r["round_id"] for r in rounds] == \
        [f"tsa-weekly-{(w + timedelta(days=6)).isoformat()}" for w in weeks]
    assert [season.is_pilot(r) for r in rounds] == [True] * 5 + [False]
    for r, w in zip(rounds, weeks):
        assert r["lock_at"] == t.lock_instant(w).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert r["release_at"] == t.resolution_instant(w).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert baselines.normalize_spec(r["zero_point"]) == baselines.normalize_spec(t.YOY)
        assert season._shape_problems(r) == []
    assert rounds[1]["release_at"] == "2026-12-15T17:00:00Z"   # P2, Tue 12-15 12:00 ET
    problems = season.validate_document(doc)
    assert problems and all("rights are 'unresolved'" in p or "invalid schedule" in p
                            for p in problems), problems
    ok(test_the_proposed_rounds_follow_the_card)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"{len(PASSED)} passed")
