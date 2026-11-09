"""LTA DataMall Passenger Volume (cards sg-lta-pv-train-jurong-east@v2,
sg-lta-pv-bus-boon-lay-int@v2).

SSA has no DataMall AccountKey yet, so every body here is a fixture shaped
like the documented response (API User Guide v6.11, sections 2.5 and 2.8,
Annex A): a JSON `value[0].Link` and a zip holding one CSV. Nothing here
touches the network.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import zipfile
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import baselines, provenance                       # noqa: E402
from ssa.adapters import sg_lta_pv as pv                    # noqa: E402

PASSED = []
HEADER = "YEAR_MONTH,DAY_TYPE,TIME_PER_HOUR,PT_TYPE,PT_CODE,TOTAL_TAP_IN_VOLUME,TOTAL_TAP_OUT_VOLUME"


def ok(fn):
    PASSED.append(fn.__name__)
    print("ok", fn.__name__)


class Sandbox:
    def __enter__(self):
        self.dir = tempfile.mkdtemp()
        self.saved = (provenance.ROOT, provenance.ARCHIVE, provenance.MANIFEST, pv.ROOT)
        provenance.ROOT = pv.ROOT = self.dir
        provenance.ARCHIVE = os.path.join(self.dir, "sources")
        provenance.MANIFEST = os.path.join(provenance.ARCHIVE, "manifest.json")
        return self

    def __exit__(self, *exc):
        provenance.ROOT, provenance.ARCHIVE, provenance.MANIFEST, pv.ROOT = self.saved
        shutil.rmtree(self.dir)


def pv_zip(kind, month, per_hour=1000, code=None, n_codes=None, empty_hours=0,
           zero_hour=None, pt_type=None):
    spec = pv.KINDS[kind]
    code = code or spec["code"]
    n_codes = n_codes or spec["min_codes"] + 5
    others = [f"X{i:04d}" if kind == "train" else f"{10000 + i:05d}" for i in range(n_codes - 1)]
    ym = pv.month_key(month)
    lines = [HEADER]
    for h in range(5, 24):
        v = 0 if h == zero_hour else per_hour
        lines.append(f"{ym},WEEKDAY,{h},{pt_type or spec['pt_type']},{code},{v},{v - 1}")
        lines.append(f"{ym},WEEKENDS/HOLIDAY,{h},{pt_type or spec['pt_type']},{code},7,7")
    for o in others:
        lines.append(f"{ym},WEEKDAY,8,{spec['pt_type']},{o},3,4")
    for _ in range(empty_hours):
        lines.append(f"{ym},WEEKDAY,,{spec['pt_type']},{others[0]},1,1")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"transport_node_{kind}_{pv.compact(month)}.csv", "\n".join(lines) + "\n")
    return buf.getvalue()


class R:
    def __init__(self, c, st=200):
        self.status_code, self.content, self.headers = st, c, {}


def test_weekday_divisor_uses_the_frozen_public_holidays():
    assert len(pv.weekdays("2027-01")) == 20           # 21 weekdays, New Year's Day
    assert len(pv.weekdays("2027-02")) == 19           # 20, CNY Monday in lieu
    assert len(pv.weekdays("2027-03")) == 21           # 23, Hari Raya Puasa, Good Friday
    assert len(pv.weekdays("2027-03", holidays=[])) == 23
    try:
        pv.weekdays("2028-01")
        raise AssertionError("guessed a 2028 holiday list")
    except ValueError:
        pass
    assert pv.lock_instant("2027-01") == datetime(2026, 12, 31, 15, 59, tzinfo=timezone.utc)
    assert pv.release_instant("2027-01") == datetime(2027, 2, 9, 16, 0, tzinfo=timezone.utc)
    assert pv.cancel_instant("2027-01") == datetime(2027, 2, 17, 15, 59, tzinfo=timezone.utc)
    ok(test_weekday_divisor_uses_the_frozen_public_holidays)


def test_parse_refuses_the_wrong_file_and_keeps_codes_as_text():
    body = pv_zip("bus", "2026-10", empty_hours=5)
    p = pv.parse(body, "bus", "202610")
    assert p["dropped_empty_hour"] == 5 and p["codes"] >= 4500
    assert any(r["PT_CODE"] == "22009" for r in p["rows"])
    lead = pv.parse(pv_zip("bus", "2026-10", code="01012"), "bus", "2026-10")
    assert any(r["PT_CODE"] == "01012" for r in lead["rows"])     # leading zero kept
    for bad, why in ((pv_zip("bus", "2026-09"), "YEAR_MONTH"),
                     (pv_zip("bus", "2026-10", pt_type="TRAIN"), "PT_TYPE"),
                     (pv_zip("train", "2026-10", n_codes=120), "PT_CODEs"),
                     (b"not a zip", "zip")):
        try:
            pv.parse(bad, "bus" if why != "PT_CODEs" else "train", "2026-10")
            raise AssertionError(f"accepted a file failing {why}")
        except ValueError as e:
            assert why in str(e), (why, e)
    ok(test_parse_refuses_the_wrong_file_and_keeps_codes_as_text)


def test_the_metric_and_its_cancel_rules():
    p = pv.parse(pv_zip("train", "2027-01", per_hour=1000), "train", "2027-01")
    got = pv.weekday_average(p, "train")
    assert got == {"value": round(19 * 1000 / 20), "total_tap_in": 19000, "weekdays": 20}
    z = pv.parse(pv_zip("train", "2027-01", zero_hour=7), "train", "2027-01")
    try:
        pv.weekday_average(z, "train")
        raise AssertionError("a zero hour 06-22 was scored")
    except pv.Cancel:
        pass
    # The guide's sample writes merged interchanges with a hyphen; the card's
    # code is 'EW24/NS1' and no alias is announced, so that file cancels.
    h = pv.parse(pv_zip("train", "2027-01", code="EW24-NS1"), "train", "2027-01")
    try:
        pv.weekday_average(h, "train")
        raise AssertionError("'EW24-NS1' was read as the card's 'EW24/NS1'")
    except pv.Cancel as e:
        assert "EW24/NS1" in str(e)
    ok(test_the_metric_and_its_cancel_rules)


def test_no_key_no_request_and_the_key_never_reaches_the_archive():
    calls = []

    def get(url, headers, timeout, allow_redirects):
        calls.append((url, dict(headers)))
        if url.startswith(pv.BASE):
            ym = url.split("Date=")[1]
            return R(json.dumps({"odata.metadata": "x", "value": [{
                "Link": f"https://ltafarecard.s3.amazonaws.com/{ym}/transport_node_bus_{ym}.zip"
                        "?X-Amz-Expires=300&X-Amz-Signature=deadbeef"}]}).encode())
        month = url.split("transport_node_bus_")[1][:6]
        return R(pv_zip("bus", month))
    with Sandbox() as s:
        try:
            pv.capture("bus", now=datetime(2026, 11, 12, tzinfo=timezone.utc), get=get, env={})
            raise AssertionError("ran without a key")
        except pv.MissingKey:
            pass
        assert calls == []
        now = datetime(2026, 11, 12, 1, 0, tzinfo=timezone.utc)      # after the 10th
        assert pv.available_months(now) == ["2026-08", "2026-09", "2026-10"]
        assert pv.available_months(datetime(2026, 11, 9, 1, 0, tzinfo=timezone.utc)) == \
            ["2026-07", "2026-08", "2026-09"]
        got = pv.capture("bus", now=now, get=get, env={"LTA_API_KEY": "sekrit"})
        assert [g["month"] for g in got] == ["2026-08", "2026-09", "2026-10"]
        api = [h for u, h in calls if u.startswith(pv.BASE)]
        assert api and all(h["AccountKey"] == "sekrit" for h in api)
        s3 = [h for u, h in calls if not u.startswith(pv.BASE)]
        assert all("AccountKey" not in h for h in s3)
        rows = pv.load_fetches("bus")
        assert rows["202610"]["url"].endswith("transport_node_bus_202610.zip")
        assert rows["202610"]["file"] == "sources/lta-pv/bus/202610.zip"
        for dirpath, _, files in os.walk(s.dir):
            for fn in files:
                with open(os.path.join(dirpath, fn), "rb") as f:
                    data = f.read()
                assert b"sekrit" not in data and b"X-Amz-Signature" not in data, fn
        # A second run fetches nothing new; different bytes for a month are refused.
        n = len(calls)
        assert pv.capture("bus", now=now, get=get, env={"LTA_API_KEY": "sekrit"}) == []
        assert len(calls) == n
        try:
            pv.file_month("bus", "2026-10", pv_zip("bus", "2026-10", per_hour=5), "u")
            raise AssertionError("an archived month was overwritten")
        except ValueError:
            pass
    ok(test_no_key_no_request_and_the_key_never_reaches_the_archive)


def _archive(months, value_of, kind="train", fetched=None):
    for m in months:
        body = pv_zip(kind, m, per_hour=value_of(m))
        at = fetched or (pv.release_instant(m) + timedelta(hours=7))
        pv.file_month(kind, m, body, "https://s3/x.zip?sig", now=at)


def test_lock_reference_interim_then_own_relative_rms():
    with Sandbox():
        _archive(["2026-10", "2026-11"], lambda m: 1000)
        ref = pv.lock_reference("train", "2027-01")
        r = ref["ranked"]
        # mean_k(k=1, lag=1): November 2026 (two before January), interim cv.
        assert r["window"] == ["2026-11"] and ref["zero_point"] == "mean1_lag1_interim"
        nov = round(19 * 1000 / len(pv.weekdays("2026-11")))
        assert r["mean"] == nov and r["sd"] == round(0.0543 * nov)
        # December is released 2027-01-10, after the lock: not in the archive at lock.
        _archive(["2026-12"], lambda m: 1000)
        assert pv.lock_reference("train", "2027-01")["ranked"]["window"] == ["2026-11"]
        # Policy (3): the reference month missing -> not posted.
        try:
            pv.lock_reference("train", "2027-02")      # needs December at lock 01-31: ok
            pv.lock_reference("train", "2026-12")      # needs October: ok
            pv.lock_reference("train", "2026-11")      # needs September: missing
            raise AssertionError("posted without its reference month")
        except baselines.NotPostable:
            pass
    with Sandbox():
        months = [baselines._month_shift("2026-12", i) for i in range(15, -1, -1)]
        months = [m for m in months if m >= "2026-01"]
        _archive(months, lambda m: 900 + int(m[5:]) * 13)
        ref = pv.lock_reference("train", "2027-01")
        assert len(months) == 12 and ref["zero_point"] == "mean1_lag1_interim"
    with Sandbox():
        months = [baselines._month_shift("2027-04", i) for i in range(15, -1, -1)]
        val = lambda m: 900 + int(m[5:]) * 13                       # noqa: E731
        _archive(months, val)
        ref = pv.lock_reference("train", "2027-06")
        assert ref["zero_point"] == "mean1_lag1_rmsrel12"
        series = pv.history("train")
        r = ref["ranked"]
        assert r["mean"] == series["2027-04"]
        errs = [(series[t] - series[baselines._month_shift(t, 2)]) / series[baselines._month_shift(t, 2)]
                for t in (baselines._month_shift("2027-06", j) for j in range(2, 14))]
        assert r["sd"] == max(round(r["mean"] * baselines.rms(errs)), 1)
    ok(test_lock_reference_interim_then_own_relative_rms)


def test_resolution_window_from_the_10th_to_the_17th():
    with Sandbox():
        m = "2027-01"
        before = pv.release_instant(m) - timedelta(minutes=1)
        assert pv.resolve("train", m, before)["status"] == "pending"
        # A file archived early (impossible, but) is not the release.
        _archive([m], lambda x: 1000, fetched=before)
        assert pv.resolve("train", m, pv.release_instant(m) + timedelta(hours=1))["status"] == "pending"
    with Sandbox():
        m = "2027-01"
        _archive([m], lambda x: 1000)
        res = pv.resolve("train", m, pv.release_instant(m) + timedelta(days=1))
        assert res["status"] == "resolved" and res["value"] == 950.0 and res["weekdays"] == 20
    with Sandbox():
        late = pv.cancel_instant("2027-01") + timedelta(minutes=1)
        assert pv.resolve("bus", "2027-01", late)["status"] == "cancelled"
    ok(test_resolution_window_from_the_10th_to_the_17th)


def test_the_proposed_zero_points_are_valid_specs():
    for kind in pv.KINDS:
        for n in (0, 12):
            spec = baselines.normalize_spec(pv.zero_point(kind, n))
            assert spec["lag"] == 1 and spec["period"] == "month"
    assert pv.zero_point("bus", 0)["spread"]["cv"] == 0.0666
    assert pv.zero_point("train", 12)["spread"]["rule"] == "rms_relative"
    ok(test_the_proposed_zero_points_are_valid_specs)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"{len(PASSED)} passed")
