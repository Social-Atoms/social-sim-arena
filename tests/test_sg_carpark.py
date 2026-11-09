"""HDB carpark occupancy in Clementi, Saturday 20:00 (card sg-hdb-carpark-clementi-sat2000@v2).

A real fixture (tests/fixtures/sg/carpark/, the data.gov.sg snapshot for
2026-11-07 20:00 fetched at the card stage) and bodies derived from it;
nothing here touches the network.
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import baselines, provenance                       # noqa: E402
from ssa.adapters import sg_carpark, sg_common              # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "sg", "carpark", "2026-11-07T200000.json")
PASSED = []


def ok(fn):
    PASSED.append(fn.__name__)
    print("ok", fn.__name__)


class Sandbox:
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


def real():
    with open(FIX, "rb") as f:
        return f.read()


def shifted(day, avail_delta=0, stamp_shift=timedelta(0), stale_clementi=0):
    """The real body moved to another Saturday: every stamp shifted by whole
    weeks, Clementi availability moved by `avail_delta` lots per carpark,
    and the first `stale_clementi` Clementi carparks made 2 h stale."""
    doc = json.loads(real())
    weeks = (date.fromisoformat(str(day)) - date(2026, 11, 7)).days
    move = timedelta(days=weeks) + stamp_shift

    def mv(s):
        return (sg_common.parse_sg_stamp(s) + move).astimezone(sg_common.SGT)
    it = doc["items"][0]
    it["timestamp"] = mv(it["timestamp"]).isoformat()
    cl = set(sg_carpark.clementi())
    staled = 0
    for c in it["carpark_data"]:
        up = mv(c["update_datetime"])
        if c["carpark_number"] in cl:
            if staled < stale_clementi:
                up -= timedelta(hours=2)
                staled += 1
            for i in c["carpark_info"]:
                if i["lot_type"] == "C":
                    t, a = int(i["total_lots"]), int(i["lots_available"])
                    i["lots_available"] = str(max(0, min(t, a + avail_delta)))
        c["update_datetime"] = up.replace(tzinfo=None).isoformat()
    return json.dumps(doc).encode()


def test_the_pinned_table_puts_48_hdb_carparks_in_clementi():
    cl = sg_carpark.clementi()
    info = sg_common.hdb_carparks()
    kinds = sorted(info[c]["car_park_type"] for c in cl)
    assert len(cl) == 48
    assert kinds.count("MULTI-STOREY CAR PARK") == 16 and kinds.count("SURFACE CAR PARK") == 32
    # The card's spot check: ACB (Albert Centre) -> 1.30106 N, 103.85412 E.
    lat, lon = sg_common.svy21_to_wgs84(float(info["ACB"]["y_coord"]),
                                        float(info["ACB"]["x_coord"]))
    assert abs(lat - 1.30106) < 2e-5 and abs(lon - 103.85412) < 2e-5
    ok(test_the_pinned_table_puts_48_hdb_carparks_in_clementi)


def test_real_snapshot_matches_the_card_stage_number():
    c = sg_carpark.Carpark()
    p = c.parse(real())
    minute = sg_common.sgt("2026-11-07", 20, 0)
    assert c.judge(p, minute, []) is None and len(p["rows"]) == 2007
    m = c.measure(p)
    # Card-stage replay: 5,699 of 14,372 lots, 39.653%.
    assert (m["occupied"], m["lots"]) == (5699, 14372) and m["value"] == 39.653
    assert 36 <= m["carparks"] <= 48
    ok(test_real_snapshot_matches_the_card_stage_number)


def test_validity_and_coverage_rules():
    c = sg_carpark.Carpark()
    minute = sg_common.sgt("2026-11-07", 20, 0)
    assert c.judge(c.parse(b'{"items": []}'), minute, []).startswith("empty")
    old = c.parse(shifted("2026-11-07", stamp_shift=-timedelta(seconds=110)))
    assert c.judge(old, minute, []).startswith("stale")         # 24 + 110 > 120 s
    doc = json.loads(real())
    doc["items"][0]["carpark_data"] = doc["items"][0]["carpark_data"][:1500]
    assert c.judge(c.parse(json.dumps(doc).encode()), minute, []).startswith("short")
    # Coverage: making 13 Clementi carparks stale leaves fewer than 36.
    try:
        c.measure(c.parse(shifted("2026-11-07", stale_clementi=13)))
        raise AssertionError("a partial town was measured")
    except c.Cancel as e:
        assert "coverage" in str(e)
    # A row with more available than total lots is dropped and listed.
    doc = json.loads(real())
    cl = set(sg_carpark.clementi())
    for cp in doc["items"][0]["carpark_data"]:
        if cp["carpark_number"] in cl:
            cp["carpark_info"][0]["lots_available"] = "99999"
            bad = cp["carpark_number"]
            break
    m = c.measure(c.parse(json.dumps(doc).encode()))
    assert m["dropped_available_over_total"] == [bad]
    ok(test_validity_and_coverage_rules)


def _archive(s, saturdays, delta, skip=()):
    store = sg_common.MinuteArchive(sg_carpark.NAME, s.dir)
    for d in saturdays:
        for m in sg_carpark.MINUTES if d in skip else sg_carpark.MINUTES[:1]:
            b = b'{"items": []}' if d in skip else shifted(d, delta(d))
            store.capture(sg_common.MinuteArchive.key(d.isoformat(), m),
                          lambda b=b: (b, "u"), now=sg_common.sgt(d, 20, 30))


def test_lock_reference_is_last_week_with_a_13_week_rms_and_policy_4():
    target = date(2026, 12, 12)
    sats = [target - timedelta(days=7 * i) for i in range(28, 0, -1)]
    delta = {d: ((i * 7) % 11) - 5 for i, d in enumerate(sats)}
    with Sandbox() as s:
        _archive(s, sats, delta.get)
        c = sg_carpark.Carpark(s.dir)
        series = c.series(c.evaluate())
        ref = sg_carpark.lock_reference(target, root=s.dir)
        r = ref["ranked"]
        assert r["mean"] == series[sats[-1]] and r["window"] == [sats[-1].isoformat()]
        errs = [series[sats[-i]] - series[sats[-i - 1]] for i in range(1, 14)]
        assert abs(r["sd"] - round(baselines.rms(errs), 3)) < 1e-9
        assert ref["shown"]["method"] == "mean4_rms13"
        assert ref["lock_at"] == "2026-12-10T12:00:00Z"
    # Last Saturday cancelled (all five minutes empty): persistence is not postable.
    with Sandbox() as s:
        _archive(s, sats, delta.get, skip={sats[-1]})
        try:
            sg_carpark.lock_reference(target, root=s.dir)
            raise AssertionError("posted without last week's value")
        except baselines.NotPostable:
            pass
        res = sg_carpark.resolve(sats[-1], sg_common.sgt(sats[-1], 21, 0), root=s.dir)
        assert res["status"] == "cancelled" and len(res["tried"]) == 5
    # Policy (4): 4 of the last 13 invalid -> 9 valid, not posted.
    with Sandbox() as s:
        _archive(s, sats, delta.get, skip=set(sats[-5:-1]))
        try:
            sg_carpark.lock_reference(target, root=s.dir)
            raise AssertionError("posted with 9 valid of the last 13")
        except baselines.NotPostable as e:
            assert "policy (4)" in str(e) or "eligible" in str(e)
    ok(test_lock_reference_is_last_week_with_a_13_week_rms_and_policy_4)


def test_capture_fetches_in_order_and_is_write_once():
    calls = []
    answers = {"2026-12-12T20:00:00": b'{"items": []}',
               "2026-12-12T20:01:00": shifted("2026-12-12")}

    class R:
        def __init__(self, c, st=200):
            self.status_code, self.content, self.headers = st, c, {}

    def get(url, headers, timeout, allow_redirects):
        calls.append(url)
        return R(answers[url.split("date_time=")[1]])
    with Sandbox() as s:
        at = sg_common.sgt("2026-12-12", 20, 30)
        got = sg_carpark.capture(now=at, get=get, sleep=lambda x: None, backfill=0,
                                 root=s.dir)
        assert got["fetched"] == ["20:00", "20:01"]
        res = sg_carpark.resolve("2026-12-12", at, root=s.dir)
        assert res["status"] == "resolved" and res["minute"] == "20:01"
        assert res["value"] == 39.653
        sg_carpark.capture(now=at + timedelta(minutes=90), get=get,
                           sleep=lambda x: None, backfill=0, root=s.dir)
        assert len(calls) == 2
    ok(test_capture_fetches_in_order_and_is_write_once)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"{len(PASSED)} passed")
