"""End-to-end replay of every wired Urban Track source, offline.

For each source: take its proposed round (docs/data-cards/<id>.rounds.json)
moved to a past period the committed real capture covers, pass it through the
season validator, let `refresh.attach_lock_reference` freeze the card's
reference from the archive as it stood at the lock, seal a stub forecast and
open it after the lock, resolve the round through `resolve.resolve_round`
from the archived capture, and score it through `refresh.build_leaderboard`
against the frozen zero point. Nothing touches the network or the
repository's archive, locks or forecasts.

The lock capture is the real body cut back to what the source had posted at
the lock (each card states its posting lag), so the reference cannot read the
answer; the resolution capture is the real body as served. Where a source has
no real capture with enough history (taxi: the weekly archive has not run;
LTA: no DataMall key) the history is synthetic and the resolution body is
real (taxi) or synthetic (LTA), as each test says.

Only the commitment salt of the seal is random, by design; every number the
test checks is deterministic.
"""
import copy
import json
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pyrage                                                  # noqa: E402

from ssa import (baselines, provenance, refresh, resolve, scoring,  # noqa: E402
                 seal, season, series, signing, urban)
from ssa.adapters import (mta_daily, nyc311, seattle_cad, sg_carpark,  # noqa: E402
                          sg_coe, sg_common, sg_lta_pv, sg_taxi, tsa_volumes)

PASSED = []
RESULTS = {}


def ok(fn):
    PASSED.append(fn.__name__)
    print("ok", fn.__name__)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def proposed(card_id):
    with open(os.path.join(ROOT, "docs", "data-cards", card_id + ".rounds.json")) as f:
        return json.load(f)["rounds"]


def replay_round(template, round_id, source):
    """The template round moved to `round_id`, on the card's own clock."""
    r = copy.deepcopy(template)
    r["round_id"] = round_id
    rule = urban.get(source)
    t = rule.target(r)
    r["lock_at"], r["release_at"] = iso(rule._lock(t)), iso(rule._release(t))
    r.pop("scored", None)
    r.pop("pilot", None)
    return r


@contextmanager
def arena():
    """Scratch locks/ and forecasts/, and a live seal key pair."""
    tmp = tempfile.mkdtemp(prefix="ssa-replay-")
    saved = (refresh.LOCKS, refresh.FORECASTS)
    refresh.LOCKS = os.path.join(tmp, "locks")
    refresh.FORECASTS = os.path.join(tmp, "forecasts")
    private, public = signing.generate()
    age = pyrage.x25519.Identity.generate()
    keys = os.path.join(tmp, "keys.json")
    with open(keys, "w") as fh:
        json.dump({"keys": [{"key_id": "replay", "public_key": public}]}, fh)
    env = {seal.ENABLE_ENV: "1", seal.AFTER_ENV: "2020-01-01T00:00:00Z",
           seal.RECIPIENT_ENV: str(age.to_public()),
           seal.AGE_KEY_ID_ENV: "age-replay",
           seal.IDENTITIES_ENV: json.dumps([str(age)]),
           signing.LIVE_KEY_ENV: private, signing.LIVE_KEY_ID_ENV: "replay"}
    try:
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(signing, "KEYS_FILE", keys):
            yield tmp, {"replay": public}
    finally:
        refresh.LOCKS, refresh.FORECASTS = saved
        urban.IO.clear()
        shutil.rmtree(tmp)


def run(r, source, *, lock_now, resolve_now, rights_pending=False,
        refused=None):
    """Validate, freeze the reference at lock, seal + open a stub forecast,
    resolve, score. Returns (row, resolution, score).

    `refused`: the card's lock rule is expected to refuse the reference with
    a message containing it. The round then resolves but is never scored."""
    problems = season.validate_document({"rounds": [r]})
    if rights_pending:
        problems = [p for p in problems if "rights are" not in p]
    assert problems == [], problems
    with arena() as (tmp, public):
        row = {"baselines": None, "scoreable": False, "baseline_note": "none"}
        # One second before the lock: nothing to attach yet.
        refresh.attach_lock_reference(row, r, lock_now - timedelta(days=2))
        assert row["zero_point_reference"]["status"] == "pending"
        refresh.attach_lock_reference(row, r, lock_now)
        if refused is not None:
            assert row["scoreable"] is False and refused in row["baseline_note"], row
            snap = refresh.read_lock_snapshot(r["round_id"])
            assert snap["zero_point_reference"]["status"] == "refused"
            res, why = resolve.resolve_round(r, {}, resolve_now)
            assert why is None, why
            scored = dict(r, baselines=row["baselines"])
            assert refresh.build_leaderboard([scored], {r["round_id"]: res}) == []
            assert "scores" not in scored, "an unposted reference scored a round"
            RESULTS[source] = {"round": r["round_id"], "refused": row["baseline_note"],
                               "value": res["value"], "vintage": res["vintage"]}
            return row, res, None
        assert row["scoreable"] is True, row.get("baseline_note")
        name = baselines.zero_point_name(r)
        ref = row["baselines"][name]
        assert ref["sd"] > 0 and ref["mean"] > 0, ref
        snap = refresh.read_lock_snapshot(r["round_id"])
        assert snap["zero_point_reference"]["status"] == "posted"
        # The stub entrant answers the reference shifted by half its spread,
        # sealed before the lock and opened after it.
        fc = {"round_id": r["round_id"], "entrant": "stub",
              "topline": {"mean": round(ref["mean"] + ref["sd"] / 2, 6),
                          "sd": ref["sd"]}}
        receipt = seal.seal(fc, received_at=datetime.strptime(
            r["lock_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            - timedelta(hours=1))
        opened, _salt = seal.open_receipt(receipt, public)
        assert opened == fc
        rdir = os.path.join(refresh.FORECASTS, r["round_id"])
        os.makedirs(rdir)
        with open(os.path.join(rdir, "stub.json"), "w") as f:
            json.dump(opened, f)
        # Before the resolution window the card says pending, never a value.
        early, why = resolve.resolve_round(r, {}, datetime.strptime(
            r["release_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            - timedelta(minutes=1))
        assert early is None
        res, why = resolve.resolve_round(r, {}, resolve_now)
        assert why is None, why
        assert res["series"] == r["series"] and res["vintage"]
        # The reference frozen at lock is what scores, even if read again.
        again = {"baselines": {}}
        refresh.attach_lock_reference(again, r, resolve_now)
        assert again["baselines"][name] == ref
        scored = dict(r, baselines=row["baselines"])
        board = refresh.build_leaderboard([scored], {r["round_id"]: res})
        got = scored["scores"]["stub"]
        want = scoring.crps_normal(fc["topline"]["mean"], fc["topline"]["sd"],
                                   res["value"])
        ref_crps = scoring.crps_normal(ref["mean"], ref["sd"], res["value"])
        assert got["crps"] == round(want, 4)
        assert got["skill"] == round(scoring.skill(want, ref_crps), 4)
        assert board[0]["entrant"] == "stub"
        RESULTS[source] = {"round": r["round_id"], "reference": ref["mean"],
                           "sd": ref["sd"], "value": res["value"],
                           "skill": got["skill"], "vintage": res["vintage"]}
        return row, res, got


# ---------------------------------------------------------------- weekly

def truncating(reader, real_key, real_fetches):
    """A reader that serves the real capture cut to the days a fake fetch row
    says were posted (`posted_through`), with the fake row's fetch time."""
    def read(day, fetches=None):
        parsed, row = reader(real_key, real_fetches)
        cut = fetches[day].get("posted_through")
        if cut is not None:
            parsed = _cut(parsed, cut)
        return parsed, dict(row, **{k: v for k, v in fetches[day].items()
                                    if k != "posted_through"})
    return read


def _cut(parsed, cut):
    if isinstance(parsed, dict) and parsed and all(
            isinstance(v, dict) and v and not isinstance(next(iter(v)), str)
            for v in parsed.values()):
        return {k: _cut(v, cut) for k, v in parsed.items()}       # MTA: by mode
    return {d: v for d, v in parsed.items() if str(d) <= cut}


def test_seattle_calls_end_to_end():
    """Card seattle-community-calls@v4 on the committed capture of 2026-11-08
    (newest day 2026-11-05): the week of 2026-10-26 locks Sunday 10-25 23:59
    PT on the archive as it stood (posted through 10-22, the three-day lag)
    and resolves on the full body taken after Tuesday 11-10 12:00 PT.

    The committed capture is the v2 request (from 2025-08-25, 440 days), not
    v4's 630-day window, so yoy4's 13-week spread finds only 3 past targets
    with a year-earlier counterpart: the card refuses to post the reference,
    exactly as v4 says, and the round resolves but is never scored. The first
    capture under the v4 window supports it (tests/test_seattle_cad.py)."""
    real = seattle_cad.load_fetches()
    key = sorted(real)[-1]
    template = {"tracker": "seattle_cad", "series": "seattle_calls_weekly",
                "question": series.SERIES["seattle_calls_weekly"]["question"],
                "unit": series.SERIES["seattle_calls_weekly"]["unit"],
                "release_estimated": False, "target_type": "continuous_normal",
                "resolve": ("the first SSA capture archived under "
                            "sources/seattle_cad/ at or after Tuesday 12:00 PT "
                            "whose seven days pass the outage check; card "
                            "seattle-community-calls@v4"),
                "zero_point": "yoy4"}
    r = replay_round(template, "seattle-calls-2026-11-01", "seattle_cad")
    w = date(2026, 10, 26)
    lock, res_at = seattle_cad.lock_instant(w), seattle_cad.resolution_instant(w)
    fake = {"2026-10-25": {"fetched_at": iso(lock - timedelta(hours=2)),
                           "posted_through": "2026-10-22"},
            "2026-11-10": {"fetched_at": iso(res_at + timedelta(hours=1))}}
    urban.IO["seattle_cad"] = {"fetches": fake, "reader": truncating(
        seattle_cad.read_capture, key, real)}
    _, res, _ = run(r, "seattle_cad", lock_now=lock,
                    resolve_now=res_at + timedelta(hours=2),
                    refused="past targets qualify for the spread")
    daily, _ = seattle_cad.read_capture(key, real)
    assert res["value"] == float(seattle_cad.week_total(
        seattle_cad.community_daily(daily), w))
    ok(test_seattle_calls_end_to_end)


def test_nyc311_end_to_end():
    """Card nyc-311-requests@v2 on the committed 2026-11-09 capture (newest
    full day 11-06): the week of 10-26, lock capture posted through Saturday
    10-24, resolved after Tuesday 11-10 12:00 ET on a fresh capture whose
    truth time is after Monday 18:00 ET."""
    real = nyc311.load_fetches()
    key = sorted(real)[-1]
    template = proposed("nyc-311-requests")[0]
    r = replay_round(template, "nyc311-2026-11-01", "nyc311")
    w = date(2026, 10, 26)
    lock, res_at = nyc311.lock_instant(w), nyc311.resolution_instant(w)
    hdr = dict(real[key]["headers"])

    def headers(at):
        stamp = at.strftime("%a, %d %b %Y %H:%M:%S GMT")
        return dict(hdr, **{"x-soda2-data-out-of-date": "false",
                            "x-soda2-truth-last-modified": stamp})
    fake = {"2026-10-25": {"fetched_at": iso(lock - timedelta(hours=3)),
                           "headers": headers(lock - timedelta(hours=4)),
                           "posted_through": "2026-10-24"},
            "2026-11-10": {"fetched_at": iso(res_at + timedelta(hours=1)),
                           "headers": headers(res_at)}}
    urban.IO["nyc311"] = {"fetches": fake, "reader": truncating(
        nyc311.read_capture, key, real)}
    run(r, "nyc311", lock_now=lock, resolve_now=res_at + timedelta(hours=2))
    ok(test_nyc311_end_to_end)


def test_mta_subway_end_to_end():
    """Card mta-subway-weekly@v2 on the committed real export (newest day
    11-03): the week of 10-26, locked on the weekend download (posted
    through 10-18, the card's D-8), resolved on a download from Saturday
    11-07. MTA's rights are unresolved, so the validator's rights line is
    the one problem allowed."""
    path = os.path.join(ROOT, "tests", "fixtures", "mta_daily",
                        "2026-11-09T005927Z.csv")
    with open(path, "rb") as f:
        body = f.read()

    def real_reader(day, fetches):
        return mta_daily.parse(body), {"file": os.path.relpath(path, ROOT),
                                        "sha256": provenance.digest(body)}
    r = replay_round(proposed("mta-subway-weekly")[0], "mta-subway-2026-11-01",
                     "mta_daily")
    w = date(2026, 10, 26)
    lock = mta_daily.lock_instant(w)
    start, _end = mta_daily.resolution_window(w)
    fake = {"2026-10-25": {"fetched_at": iso(lock - timedelta(hours=5)),
                           "window": mta_daily.REGULAR,
                           "posted_through": "2026-10-18"},
            "2026-11-07": {"fetched_at": iso(start + timedelta(hours=10)),
                           "window": mta_daily.REGULAR}}
    urban.IO["mta_daily"] = {"fetches": fake,
                             "reader": truncating(real_reader, None, None)}
    run(r, "mta_daily", lock_now=lock, resolve_now=start + timedelta(hours=11),
        rights_pending=True)
    ok(test_mta_subway_end_to_end)


def test_tsa_checkpoint_end_to_end():
    """Card tsa-checkpoint-weekly@v2 on the three committed real pages
    (newest date 11-05): the week of 10-26, locked on a Saturday capture
    posted through 10-24, resolved after Tuesday 11-03 12:00 ET. TSA's
    rights are unresolved; only that validator line is allowed."""
    fx = os.path.join(ROOT, "tests", "fixtures", "tsa_volumes", "2026-11-09")
    pages = {}
    for name in ("current", "2025", "2024"):
        with open(os.path.join(fx, name + ".html"), "rb") as f:
            pages[name] = tsa_volumes.parse_page(f.read())
    daily = tsa_volumes.merge(pages)[0]
    row = {"pages": {name: {"file": f"tests/fixtures/tsa_volumes/2026-11-09/{name}.html",
                            "sha256": "replay"} for name in pages}}

    def real_reader(day, fetches):
        return dict(daily), dict(row)
    r = replay_round(proposed("tsa-checkpoint-weekly")[0], "tsa-weekly-2026-11-01",
                     "tsa_volumes")
    w = date(2026, 10, 26)
    lock, res_at = tsa_volumes.lock_instant(w), tsa_volumes.resolution_instant(w)
    fake = {"2026-10-25": {"fetched_at": iso(lock - timedelta(hours=6)),
                           "posted_through": "2026-10-24"},
            "2026-11-03": {"fetched_at": iso(res_at + timedelta(hours=1))}}
    urban.IO["tsa_volumes"] = {"fetches": fake,
                               "reader": truncating(real_reader, None, None)}
    run(r, "tsa_volumes", lock_now=lock, resolve_now=res_at + timedelta(hours=2),
        rights_pending=True)
    ok(test_tsa_checkpoint_end_to_end)


# ---------------------------------------------------------------- Singapore

def test_coe_premium_end_to_end():
    """Card sg-coe-cat-a-premium@v3 on the committed real datastore pages
    (newest exercise 2026-11 (1)): that exercise locks 1 h before bidding
    opened on the table without its own row, and resolves on the full table
    captured after the close + 2 h."""
    recs = []
    for name in ("page-00000", "page-01000"):
        with open(os.path.join(ROOT, "tests", "fixtures", "sg", "coe",
                               name + ".json"), "rb") as f:
            recs.extend(sg_coe.parse_page(f.read())[0])
    full = sg_coe.table(recs)
    before = sg_coe.table([x for x in recs
                           if (x["month"], str(x["bidding_no"])) != ("2026-11", "1")])
    files = {"page-00000": {"file": "tests/fixtures/sg/coe/page-00000.json",
                            "sha256": "replay"}}
    lock = sg_coe.lock_instant("2026-11", "1")
    res_at = sg_coe.resolution_instant("2026-11", "1")
    rows = {"lock": {"fetched_at": iso(lock - timedelta(hours=1)), "files": files},
            "result": {"fetched_at": iso(res_at + timedelta(minutes=17)),
                       "files": files}}

    def reader(key, rows):
        return (before if key == "lock" else full), rows[key]
    r = replay_round(proposed("sg-coe-cat-a-premium")[0], "sg-coe-a-2026-11-1",
                     "sg_coe")
    urban.IO["sg_coe"] = {"rows": rows, "reader": reader}
    _, res, _ = run(r, "sg_coe", lock_now=lock,
                    resolve_now=res_at + timedelta(hours=1))
    assert res["value"] == float(full[("2026-11", "1", "Category A")]["premium"])
    ok(test_coe_premium_end_to_end)


class SgSandbox:
    """A temporary repository root holding the pinned reference files, as
    the Singapore adapters' own tests use."""

    def __enter__(self):
        self.dir = tempfile.mkdtemp()
        shutil.copytree(os.path.join(ROOT, "sources", "sg-ref"),
                        os.path.join(self.dir, "sources", "sg-ref"))
        self.saved = (provenance.ROOT, provenance.ARCHIVE, provenance.MANIFEST,
                      sg_lta_pv.ROOT)
        provenance.ROOT = sg_lta_pv.ROOT = self.dir
        provenance.ARCHIVE = os.path.join(self.dir, "sources")
        provenance.MANIFEST = os.path.join(provenance.ARCHIVE, "manifest.json")
        return self

    def __exit__(self, *exc):
        (provenance.ROOT, provenance.ARCHIVE, provenance.MANIFEST,
         sg_lta_pv.ROOT) = self.saved
        shutil.rmtree(self.dir)


def _taxi_body(stamp, dtc, other):
    pts = [[103.8515, 1.2840]] * dtc + [[103.9890, 1.3590]] * other
    return json.dumps({"type": "FeatureCollection", "features": [{
        "type": "Feature", "geometry": {"type": "MultiPoint", "coordinates": pts},
        "properties": {"timestamp": stamp, "taxi_count": len(pts),
                       "api_info": {"status": "healthy"}}}]}).encode()


def test_taxi_downtown_end_to_end():
    """Card sg-taxi-downtown-fri2300@v3: 30 synthetic past Fridays (the
    weekly capture has not run yet, so no real history exists), and the
    target Friday 2026-11-06 resolved on the committed real 23:00 snapshot."""
    target = date(2026, 11, 6)
    with open(os.path.join(ROOT, "tests", "fixtures", "sg", "taxi",
                           "2026-11-06T230000.json"), "rb") as f:
        real = f.read()
    real_value = sg_taxi.Taxi().measure(sg_taxi.Taxi().parse(real))["value"]
    with SgSandbox() as s:
        store = sg_common.MinuteArchive(sg_taxi.NAME, s.dir)
        fridays = [target - timedelta(days=7 * i) for i in range(30, 0, -1)]
        for i, d in enumerate(fridays):
            b = _taxi_body(f"{d}T22:59:40+08:00", int(real_value) + (i * 37) % 61 - 30,
                           2000)
            store.capture(sg_common.MinuteArchive.key(d.isoformat(), "23:00"),
                          lambda b=b: (b, "u"), now=sg_common.sgt(d, 23, 30))
        store.capture(sg_common.MinuteArchive.key(target.isoformat(), "23:00"),
                      lambda: (real, "u"), now=sg_common.sgt(target, 23, 30))
        r = replay_round(proposed("sg-taxi-downtown-fri2300")[0],
                         "sg-taxi-dtc-2026-11-06", "sg_taxi")
        urban.IO["sg_taxi"] = {"root": s.dir}
        _, res, _ = run(r, "sg_taxi", lock_now=sg_taxi.lock_instant(target),
                        resolve_now=sg_common.sgt(target, 23, 45))
        assert res["value"] == real_value and res["minute"] == "23:00"
    ok(test_taxi_downtown_end_to_end)


def _carpark_shifted(real, day, delta):
    doc = json.loads(real)
    move = timedelta(days=(day - date(2026, 11, 7)).days)

    def mv(stamp):
        return (sg_common.parse_sg_stamp(stamp) + move).astimezone(sg_common.SGT)
    it = doc["items"][0]
    it["timestamp"] = mv(it["timestamp"]).isoformat()
    clementi = set(sg_carpark.clementi())
    for c in it["carpark_data"]:
        c["update_datetime"] = mv(c["update_datetime"]).replace(tzinfo=None).isoformat()
        if c["carpark_number"] in clementi:
            for i in c["carpark_info"]:
                if i["lot_type"] == "C":
                    t, a = int(i["total_lots"]), int(i["lots_available"])
                    i["lots_available"] = str(max(0, min(t, a + delta)))
    return json.dumps(doc).encode()


def test_hdb_carpark_clementi_end_to_end():
    """Card sg-hdb-carpark-clementi-sat2000@v3: past Saturdays are the
    committed real 2026-11-07 20:00 snapshot moved back whole weeks with
    Clementi availability varied (no weekly archive exists yet); the target
    Saturday 2026-11-07 resolves on the real snapshot as served."""
    target = date(2026, 11, 7)
    with open(os.path.join(ROOT, "tests", "fixtures", "sg", "carpark",
                           "2026-11-07T200000.json"), "rb") as f:
        real = f.read()
    with SgSandbox() as s:
        store = sg_common.MinuteArchive(sg_carpark.NAME, s.dir)
        sats = [target - timedelta(days=7 * i) for i in range(28, 0, -1)]
        for i, d in enumerate(sats):
            b = _carpark_shifted(real, d, ((i * 7) % 11) - 5)
            store.capture(sg_common.MinuteArchive.key(d.isoformat(), "20:00"),
                          lambda b=b: (b, "u"), now=sg_common.sgt(d, 20, 30))
        store.capture(sg_common.MinuteArchive.key(target.isoformat(), "20:00"),
                      lambda: (real, "u"), now=sg_common.sgt(target, 20, 30))
        r = replay_round(proposed("sg-hdb-carpark-clementi-sat2000")[0],
                         "sg-hdb-clementi-2026-11-07", "sg_carpark_hdb")
        urban.IO["sg_carpark_hdb"] = {"root": s.dir}
        c = sg_carpark.Carpark(s.dir)
        want = c.measure(c.parse(real))["value"]
        _, res, _ = run(r, "sg_carpark_hdb", lock_now=sg_carpark.lock_instant(target),
                        resolve_now=sg_common.sgt(target, 20, 45))
        assert res["value"] == want
    ok(test_hdb_carpark_clementi_end_to_end)


def _pv_zip(kind, month, per_hour, code):
    import io
    import zipfile
    spec = sg_lta_pv.KINDS[kind]
    ym = sg_lta_pv.month_key(month)
    lines = [",".join(sg_lta_pv.COLUMNS)]
    for h in range(5, 24):
        lines.append(f"{ym},WEEKDAY,{h},{spec['pt_type']},{code},{per_hour},{per_hour}")
    for i in range(spec["min_codes"] + 4):
        o = f"X{i:04d}" if kind == "train" else f"{10000 + i:05d}"
        lines.append(f"{ym},WEEKDAY,8,{spec['pt_type']},{o},3,4")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"transport_node_{kind}_{sg_lta_pv.compact(month)}.csv", "\n".join(lines) + "\n")
    return buf.getvalue()


def test_lta_passenger_volume_end_to_end():
    """Cards sg-lta-pv-*@v2. No real DataMall file exists (no AccountKey), so
    the months are synthetic zips in the documented Annex A shape; the target
    month's file spells the station the DataMall sample's way ('EW24-NS1'),
    which the alias table accepts."""
    target = "2026-10"
    with SgSandbox():
        months = ["2026-06", "2026-07", "2026-08", target]
        for i, m in enumerate(months):
            code = "EW24-NS1" if m == target else "EW24/NS1"
            at = sg_lta_pv.release_instant(m) + timedelta(hours=3)
            sg_lta_pv.file_month("train", m, _pv_zip("train", m, 1000 + 40 * i, code),
                                 "https://datamall2.mytransport.sg/x", now=at)
        r = replay_round(proposed("sg-lta-pv-train-jurong-east")[0],
                         "sg-pv-train-je-2026-10", "sg_lta_pv")
        r["zero_point"] = sg_lta_pv.zero_point("train", 0)
        _, res, _ = run(r, "sg_lta_pv", lock_now=sg_lta_pv.lock_instant(target),
                        resolve_now=sg_lta_pv.release_instant(target) + timedelta(hours=4))
        days = len(sg_lta_pv.weekdays(target))
        assert res["value"] == float(round(19 * (1000 + 40 * 3) / days))
    ok(test_lta_passenger_volume_end_to_end)


def test_every_wired_source_was_replayed():
    wired = {k for k, s in urban.SOURCES.items() if s.has_lock_reference}
    assert wired == set(RESULTS), sorted(wired ^ set(RESULTS))
    for k, v in sorted(RESULTS.items()):
        if "refused" in v:
            print(f"  {k:15s} {v['round']:28s} reference refused at lock; "
                  f"value {v['value']:,.3f}")
            continue
        print(f"  {k:15s} {v['round']:28s} ref {v['reference']:>14,.3f} "
              f"sd {v['sd']:>10,.3f} value {v['value']:>14,.3f} skill {v['skill']:+.4f}")
    ok(test_every_wired_source_was_replayed)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"{len(PASSED)} passed")
