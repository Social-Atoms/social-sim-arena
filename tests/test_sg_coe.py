"""Singapore COE Category A premium (card sg-coe-cat-a-premium@v3).

Real fixtures (tests/fixtures/sg/coe/: the two datastore pages and the
metadata body fetched at the card stage on 2026-11-09, 1,995 rows to the
November 2026 (1) exercise); nothing here touches the network.
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import baselines, provenance                       # noqa: E402
from ssa.adapters import sg_coe                             # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "sg", "coe")
PASSED = []


def ok(fn):
    PASSED.append(fn.__name__)
    print("ok", fn.__name__)


def fx(name):
    with open(os.path.join(FIX, name + ".json"), "rb") as f:
        return f.read()


class Sandbox:
    def __enter__(self):
        self.dir = tempfile.mkdtemp()
        self.saved = (provenance.ROOT, provenance.ARCHIVE, provenance.MANIFEST, sg_coe.ROOT)
        provenance.ROOT = sg_coe.ROOT = self.dir
        provenance.ARCHIVE = os.path.join(self.dir, "sources")
        provenance.MANIFEST = os.path.join(provenance.ARCHIVE, "manifest.json")
        return self

    def __exit__(self, *exc):
        provenance.ROOT, provenance.ARCHIVE, provenance.MANIFEST, sg_coe.ROOT = self.saved
        shutil.rmtree(self.dir)


class R:
    def __init__(self, c, st=200):
        self.status_code, self.content, self.headers = st, c, {}


def with_row(month, bidding_no, premium, quota="1200"):
    """Page 1000 plus one Category A row (and the totals moved by one)."""
    p0, p1 = json.loads(fx("page-00000")), json.loads(fx("page-01000"))
    p1["result"]["records"].append({
        "_id": 1996, "month": month, "bidding_no": bidding_no,
        "vehicle_class": "Category A", "quota": quota, "bids_success": quota,
        "bids_received": "2,100", "premium": premium})
    for p in (p0, p1):
        p["result"]["total"] = 1996
    return json.dumps(p0).encode(), json.dumps(p1).encode()


def server(p0, p1, calls):
    def get(url, headers, timeout, allow_redirects):
        calls.append(url)
        assert "research" in headers["User-Agent"]
        if url == sg_coe.METADATA:
            return R(fx("metadata"))
        if url.endswith("offset=0"):
            return R(p0)
        if url.endswith("offset=1000"):
            return R(p1)
        return R(b"", 404)
    return get


def test_real_pages_parse_and_the_reference_matches_the_card():
    recs = sg_coe.parse_page(fx("page-00000"))[0] + sg_coe.parse_page(fx("page-01000"))[0]
    tab = sg_coe.table(recs)
    assert len(tab) == 1995
    a = sg_coe.category_series(tab)
    last = sg_coe.exercise_index("2026-11", "1")
    assert a[last] == 139001 and max(a) == last
    assert [a[last - i] for i in range(7, -1, -1)] == [
        126000, 123890, 128501, 133009, 131890, 130001, 132004, 139001]
    # The card's spread check: 48 targets to 2026-11 (1), sd = RMS of the last
    # 26 changes -> mean CRPS S$2,713, 90% coverage 0.94.
    from ssa import scoring
    crps, cov = [], 0
    for t in sorted(a)[-48:]:
        r = baselines.reference(sg_coe.ZERO_POINT, {k: v for k, v in a.items() if k < t}, t)
        crps.append(scoring.crps_normal(r["mean"], r["sd"], a[t]))
        cov += abs(a[t] - r["mean"]) <= 1.645 * r["sd"]
    assert round(sum(crps) / 48) == 2713 and round(cov / 48, 2) == 0.94
    # 2020 (April to June) had no exercises: a gap the index axis sees.
    assert sg_coe.exercise_index("2020-04", "1") not in a
    ok(test_real_pages_parse_and_the_reference_matches_the_card)


def test_schedule_lock_and_resolution_instants():
    utc = timezone.utc
    assert sg_coe.lock_instant("2026-12", "1") == datetime(2026, 12, 7, 3, 0, tzinfo=utc)
    assert sg_coe.resolution_instant("2026-12", "1") == datetime(2026, 12, 9, 10, 0, tzinfo=utc)
    assert sg_coe.grace_end("2026-12", "2") == datetime(2026, 12, 25, 8, 0, tzinfo=utc)
    # The two 2026 exceptions in LTA's schedule.
    assert sg_coe.window("2026-02", "2")[1] == datetime(2026, 2, 20, 8, 0, tzinfo=utc)
    assert sg_coe.lock_instant("2026-06", "1") == datetime(2026, 6, 2, 3, 0, tzinfo=utc)
    assert len(sg_coe.SCHEDULE) == 24
    try:
        sg_coe.lock_instant("2027-01", "1")
        raise AssertionError("an unpublished exercise got a lock")
    except KeyError:
        pass
    assert sg_coe.exercise_of(sg_coe.exercise_index("2027-01", "2")) == ("2027-01", "2")
    ok(test_schedule_lock_and_resolution_instants)


def test_capture_only_when_a_rule_needs_one_and_lock_reference():
    calls = []
    p0, p1 = fx("page-00000"), fx("page-01000")
    with Sandbox() as s:
        # Nothing open on Thursday 2026-11-12: no request at all.
        quiet = datetime(2026, 11, 12, 6, 17, tzinfo=timezone.utc)
        assert sg_coe.capture_if_due(quiet, get=server(p0, p1, calls))["captured"] is False
        assert calls == []
        # Sunday 18:17Z, 8 h 43 min before the 11-16 lock: the lock capture.
        at = datetime(2026, 11, 15, 18, 17, tzinfo=timezone.utc)
        got = sg_coe.capture_if_due(at, get=server(p0, p1, calls))
        assert got["captured"] and got["reason"] == "lock capture for 2026-11 (2)"
        row = got["row"]
        assert row["rows"] == 1995 and row["newest_exercise"] == "2026-11 (1)"
        assert row["last_updated_at"] == "2026-11-04T17:37:51+08:00"
        assert row["files"]["page-01000"]["file"] == "sources/coe/2026-11-15T1817Z/page-01000.json"
        assert len(calls) == 3
        # The next run in the window does not capture again.
        assert sg_coe.capture_if_due(at + timedelta(hours=6),
                                     get=server(p0, p1, calls))["captured"] is False
        ref = sg_coe.lock_reference("2026-11", "2")
        assert ref["ranked"]["mean"] == 139001 and ref["ranked"]["sd"] == 4657
        assert ref["ranked"]["exercises"] == ["2026-11 (1)"]
        assert ref["shown"]["mean"] == round((132004 + 139001) / 2)
        assert ref["capture"] == "2026-11-15T1817Z"
        # The same capture is too old for the December (1) lock.
        try:
            sg_coe.lock_reference("2026-12", "1")
            raise AssertionError("a three-week-old capture served as the lock capture")
        except baselines.NotPostable:
            pass
    ok(test_capture_only_when_a_rule_needs_one_and_lock_reference)


def test_resolution_waits_for_the_row_then_cancels_at_grace():
    calls = []
    with Sandbox() as s:
        close2 = sg_coe.resolution_instant("2026-11", "2")      # Wed 18 Nov 10:00Z
        # 12:17Z: data.gov.sg has not posted yet -> a capture, still pending.
        t1 = close2 + timedelta(hours=2, minutes=17)
        got = sg_coe.capture_if_due(t1, get=server(fx("page-00000"), fx("page-01000"), calls))
        assert got["reason"] == "resolution capture for 2026-11 (2)"
        assert sg_coe.resolve("2026-11", "2", t1)["status"] == "pending"
        # 18:17Z: the row is there (with a thousands comma) -> resolved.
        p0, p1 = with_row("2026-11", "2", "141,500")
        t2 = t1 + timedelta(hours=6)
        assert sg_coe.capture_if_due(t2, get=server(p0, p1, calls))["captured"]
        res = sg_coe.resolve("2026-11", "2", t2)
        assert res["status"] == "resolved" and res["value"] == 141500.0
        assert res["bids_received"] == 2100 and res["capture"] == "2026-11-18T1817Z"
        # Resolved: later runs in the window do not capture.
        assert not sg_coe.capture_if_due(t2 + timedelta(hours=6),
                                         get=server(p0, p1, calls))["captured"]
    with Sandbox() as s:
        # A premium of 0 counts as missing (policy (4)); none by close + 48 h.
        p0, p1 = with_row("2026-11", "2", "0")
        t = sg_coe.resolution_instant("2026-11", "2") + timedelta(minutes=17)
        sg_coe.capture(t, get=server(p0, p1, calls))
        assert sg_coe.resolve("2026-11", "2", t)["status"] == "pending"
        late = sg_coe.grace_end("2026-11", "2") + timedelta(minutes=1)
        assert sg_coe.resolve("2026-11", "2", late)["status"] == "cancelled"
    ok(test_resolution_waits_for_the_row_then_cancels_at_grace)


def test_an_inconsistent_capture_is_refused_and_nothing_is_filed():
    calls = []
    p0 = fx("page-00000")
    p1 = json.loads(fx("page-01000"))
    p1["result"]["total"] = 2000                    # moved between the pages
    with Sandbox() as s:
        try:
            sg_coe.capture(datetime(2026, 11, 15, 18, 17, tzinfo=timezone.utc),
                           get=server(p0, json.dumps(p1).encode(), calls))
            raise AssertionError("an inconsistent capture was filed")
        except ValueError as e:
            assert "total moved" in str(e)
        assert not os.path.exists(os.path.join(s.dir, "sources", "coe"))
        try:
            sg_coe.capture(datetime(2026, 11, 15, 18, 17, tzinfo=timezone.utc),
                           get=lambda *a, **k: R(b"", 429))
            raise AssertionError("a 429 was swallowed")
        except Exception as e:                       # noqa: BLE001
            assert "HTTP 429" in str(e)
    ok(test_an_inconsistent_capture_is_refused_and_nothing_is_filed)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"{len(PASSED)} passed")
