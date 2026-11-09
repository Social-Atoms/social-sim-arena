"""Singapore free taxis in Downtown Core, Friday 23:00 (card sg-taxi-downtown-fri2300@v2).

Real fixtures (tests/fixtures/sg/taxi/, data.gov.sg past-minute snapshots
fetched at the card stage on 2026-11-09) plus synthetic bodies; nothing here
touches the network.
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
from ssa.adapters import sg_common, sg_taxi                 # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "sg", "taxi")
PASSED = []
DTC = (103.8515, 1.2840)            # Raffles Place: inside DOWNTOWN CORE
CHANGI = (103.9890, 1.3590)         # Changi Airport: outside it


def ok(fn):
    PASSED.append(fn.__name__)
    print("ok", fn.__name__)


class Sandbox:
    """A temporary repository root holding the pinned reference files."""

    def __enter__(self):
        self.dir = tempfile.mkdtemp()
        shutil.copytree(os.path.join(ROOT, "sources", "sg-ref"),
                        os.path.join(self.dir, "sources", "sg-ref"))
        self.saved = (provenance.ROOT, provenance.ARCHIVE, provenance.MANIFEST)
        provenance.ROOT = self.dir
        provenance.ARCHIVE = os.path.join(self.dir, "sources")
        provenance.MANIFEST = os.path.join(provenance.ARCHIVE, "manifest.json")
        return self

    def __exit__(self, *exc):
        provenance.ROOT, provenance.ARCHIVE, provenance.MANIFEST = self.saved
        shutil.rmtree(self.dir)


class R:
    def __init__(self, content, status=200):
        self.status_code, self.content, self.headers = status, content, {}


def fixture(name):
    with open(os.path.join(FIX, name + ".json"), "rb") as f:
        return f.read()


def body(stamp, dtc, other):
    pts = [list(DTC)] * dtc + [list(CHANGI)] * other
    return json.dumps({"type": "FeatureCollection", "features": [{
        "type": "Feature", "geometry": {"type": "MultiPoint", "coordinates": pts},
        "properties": {"timestamp": stamp, "taxi_count": len(pts),
                       "api_info": {"status": "healthy"}}}]}).encode()


def server(answers, calls):
    """get() serving {date_time: bytes}; anything else is a 500."""
    def get(url, headers, timeout, allow_redirects):
        calls.append(url)
        assert "research" in headers["User-Agent"]
        dt = url.split("date_time=")[1]
        if dt not in answers:
            return R(b"", status=500)
        return R(answers[dt])
    return get


def test_real_snapshots_count_downtown_core_as_the_card_measured():
    t = sg_taxi.Taxi()
    # Card-stage counts (2026-11-09 replay of the same bodies).
    for name, island, dtc in (("2026-10-09T230000", 2399, 186),
                              ("2026-11-06T230000", 2700, 141)):
        p = t.parse(fixture(name))
        assert p["taxi_count"] == island == len(p["points"])
        assert t.measure(p)["value"] == dtc, name
    pa = sg_common.planning_areas()
    assert len(pa.names) == 55 and pa.locate(*DTC) == "DOWNTOWN CORE"
    assert pa.locate(*CHANGI) == "CHANGI"
    ok(test_real_snapshots_count_downtown_core_as_the_card_measured)


def test_validity_rules_stale_low_and_points_missing():
    t = sg_taxi.Taxi()
    minute = sg_common.sgt("2026-12-11", 23, 0)
    good = t.parse(body("2026-12-11T22:59:30+08:00", 100, 1900))
    prior = [{"island": n} for n in (2000, 2100, 2200, 2300)]
    assert t.judge(good, minute, prior) is None
    stale = t.parse(body("2026-12-11T22:57:59+08:00", 100, 1900))   # 121 s
    assert t.judge(stale, minute, prior).startswith("stale")
    edge = t.parse(body("2026-12-11T22:58:00+08:00", 100, 1900))    # 120 s
    assert t.judge(edge, minute, prior) is None
    low = t.parse(body("2026-12-11T22:59:30+08:00", 10, 600))       # < 0.3 x 2150
    assert t.judge(low, minute, prior).startswith("low")
    doc = json.loads(body("2026-12-11T22:59:30+08:00", 100, 1900))
    doc["features"][0]["properties"]["taxi_count"] = 2500
    assert t.judge(t.parse(json.dumps(doc).encode()), minute, prior).startswith("points missing")
    # No earlier valid Friday: only the timestamp and point rules apply.
    assert t.judge(low, minute, []) is None
    for bad in (b"[]", b"{}", b'{"type":"FeatureCollection","features":[]}'):
        try:
            t.parse(bad)
            raise AssertionError(f"parsed {bad!r}")
        except ValueError:
            pass
    ok(test_validity_rules_stale_low_and_points_missing)


def test_a_real_glitch_friday_is_cancelled_after_all_five_minutes():
    """2026-10-16: the feed was low from before 21:00 to about 23:58 (card
    evidence); none of 23:00..23:10 recovers, so the round is cancelled."""
    answers = {"2026-10-09T23:00:00": fixture("2026-10-09T230000"),
               "2026-10-16T23:00:00": fixture("2026-10-16T230000")}
    for m in ("2301", "2302", "2305", "2310"):
        answers[f"2026-10-16T{m[:2]}:{m[2:]}:00"] = fixture(f"2026-10-16T{m}00")
    calls, sleeps = [], []
    with Sandbox() as s:
        run_at = sg_common.sgt("2026-10-16", 23, 31)
        got = sg_taxi.capture(now=run_at, get=server(answers, calls),
                              sleep=sleeps.append, backfill=1, root=s.dir)
        assert got["target"] == "2026-10-16"
        assert got["fetched"] == ["23:00", "23:01", "23:02", "23:05", "23:10"]
        assert got["backfill"] == [{"target": "2026-10-09", "fetched": ["23:00"]}]
        assert all(x == 3.0 for x in sleeps) and len(sleeps) == 5
        res = sg_taxi.resolve("2026-10-16", run_at, root=s.dir)
        assert res["status"] == "cancelled", res
        assert [x["reason"][:3] for x in res["tried"]] == ["low"] * 5
        rows = sg_common.MinuteArchive(sg_taxi.NAME, s.dir).load()
        assert rows["2026-10-09T23:00"]["backfill"] is True
        assert rows["2026-10-16T23:10"]["file"] == "sources/lta-rt/taxi/2026-10-16/2310.json"
        assert "backfill" not in rows["2026-10-16T23:00"]
        # Write-once: the same run again costs no request.
        n = len(calls)
        sg_taxi.capture(now=run_at, get=server(answers, calls), sleep=sleeps.append,
                        backfill=1, root=s.dir)
        assert len(calls) == n
        man = provenance.load_manifest()[sg_taxi.NAME]
        assert man["file"].startswith("sources/lta-rt/taxi/")
        # The archive, not the endpoint, is the record: a changed file is refused.
        with open(os.path.join(s.dir, rows["2026-10-09T23:00"]["file"]), "ab") as f:
            f.write(b" ")
        try:
            sg_taxi.resolve("2026-10-16", run_at, root=s.dir)
            raise AssertionError("a changed archive was read")
        except RuntimeError:
            pass
    ok(test_a_real_glitch_friday_is_cancelled_after_all_five_minutes)


def test_fallback_minute_resolves_and_stops_fetching():
    answers = {"2026-12-04T23:00:00": body("2026-12-04T22:59:40+08:00", 150, 2000),
               "2026-12-11T23:00:00": body("2026-12-11T22:40:00+08:00", 1, 99),
               "2026-12-11T23:01:00": body("2026-12-11T23:00:50+08:00", 120, 1900)}
    calls = []
    with Sandbox() as s:
        run_at = sg_common.sgt("2026-12-11", 23, 30)
        assert sg_taxi.resolve("2026-12-11", run_at - timedelta(minutes=1),
                               root=s.dir)["status"] == "pending"
        got = sg_taxi.capture(now=run_at, get=server(answers, calls),
                              sleep=lambda x: None, backfill=1, root=s.dir)
        assert got["fetched"] == ["23:00", "23:01"]
        res = sg_taxi.resolve("2026-12-11", run_at, root=s.dir)
        assert res["status"] == "resolved" and res["minute"] == "23:01"
        assert res["value"] == 120.0 and res["island"] == 2020
    ok(test_fallback_minute_resolves_and_stops_fetching)


def test_a_minute_never_archived_is_pending_until_grace_then_cancelled():
    calls = []
    with Sandbox() as s:
        run_at = sg_common.sgt("2026-12-11", 23, 30)
        try:
            sg_taxi.capture(now=run_at, get=server({}, calls), sleep=lambda x: None,
                            backfill=0, root=s.dir)
            raise AssertionError("a 500 was filed")
        except Exception as e:                       # noqa: BLE001
            assert "HTTP 500" in str(e)
        assert len(calls) == 1                       # an HTTP error is never retried
        assert sg_taxi.resolve("2026-12-11", run_at, root=s.dir)["status"] == "pending"
        late = sg_common.sgt("2026-12-12", 12, 1)    # Saturday 12:00 SGT has passed
        assert sg_taxi.resolve("2026-12-11", late, root=s.dir)["status"] == "cancelled"
    ok(test_a_minute_never_archived_is_pending_until_grace_then_cancelled)


def _weekly_archive(s, fridays, dtc_of, fetched_at=None, skip=()):
    store = sg_common.MinuteArchive(sg_taxi.NAME, s.dir)
    for d in fridays:
        b = body(f"{d}T22:59:40+08:00", dtc_of(d), 300) if d not in skip else \
            body(f"{d}T22:59:40+08:00", 1, 10)
        for m in sg_taxi.MINUTES if d in skip else sg_taxi.MINUTES[:1]:
            bb = b
            at = fetched_at or (sg_common.sgt(d, 23, 30) + timedelta(minutes=int(m[3:])))
            store.capture(sg_common.MinuteArchive.key(d.isoformat(), m),
                          lambda bb=bb: (bb, "u"), now=at)


def test_lock_reference_is_mean_k4_with_its_own_rms_from_the_archive_at_lock():
    target = date(2026, 12, 11)
    fridays = [target - timedelta(days=7 * i) for i in range(40, 0, -1)]
    dtc = {d: 100 + (i * 37) % 61 for i, d in enumerate(fridays)}
    glitch = {fridays[-3]}                            # 2026-11-20: cancelled
    with Sandbox() as s:
        _weekly_archive(s, fridays, dtc.get, skip=glitch)
        # A body fetched after the lock is not in the archive "as it stood".
        store = sg_common.MinuteArchive(sg_taxi.NAME, s.dir)
        store.capture("2026-12-04T23:01", lambda: (b"{}", "u"),
                      now=sg_common.sgt("2026-12-10", 1, 0))
        ref = sg_taxi.lock_reference(target, root=s.dir)
        valid = [d for d in fridays if d not in glitch]
        want = sum(dtc[d] for d in valid[-4:]) / 4
        r = ref["ranked"]
        assert r["method"] == "mean4_rms13" and r["mean"] == round(want, 2)
        assert r["window"] == [d.isoformat() for d in valid[-4:]]
        # The spread, recomputed by hand: 13 most recent valid past targets.
        errs = []
        for t in reversed(valid):
            prev = [d for d in valid if d < t][-4:]
            if len(prev) < 4 or (t - prev[0]).days > 7 * 6:
                continue
            errs.append(dtc[t] - sum(dtc[d] for d in prev) / 4)
            if len(errs) == 13:
                break
        assert abs(r["sd"] - round(baselines.rms(errs), 2)) < 1e-9
        assert ref["shown"]["method"] == "persistence_rms13"
        assert ref["lock_at"] == "2026-12-09T15:00:00Z"
        # Policy (2): fewer than 4 valid Fridays among the last 6 -> not posted.
        many = set(fridays[-6:-3]) | glitch
    with Sandbox() as s:
        _weekly_archive(s, fridays, dtc.get, skip=many)
        try:
            sg_taxi.lock_reference(target, root=s.dir)
            raise AssertionError("posted with 3 valid of the last 6")
        except baselines.NotPostable:
            pass
    ok(test_lock_reference_is_mean_k4_with_its_own_rms_from_the_archive_at_lock)


def test_the_zero_point_spec_is_closed_and_measured():
    spec = baselines.normalize_spec(sg_taxi.ZERO_POINT)
    assert spec["family"] == "mean_k" and spec["k"] == 4 and spec["search"] == 6
    assert spec["spread"] == {"rule": "rms", "n": 13, "search": 26, "end": "resolved"}
    assert sg_taxi.lock_instant("2027-01-08") == datetime(2027, 1, 6, 15, 0,
                                                          tzinfo=timezone.utc)
    try:
        sg_taxi.lock_instant("2027-01-07")
        raise AssertionError("a Thursday was accepted")
    except ValueError:
        pass
    ok(test_the_zero_point_spec_is_closed_and_measured)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"{len(PASSED)} passed")
