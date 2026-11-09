"""The Singapore cards' proposed rounds (docs/data-cards/sg-*.rounds.json).

They are not in questions/season0.json: they wait for a question batch. This
checks what can be checked now -- each file cites its card by the hash of the
committed copy, its zero points are closed specs, the clock matches the
adapters, and against the season validator (with the five series registered
in a stub, since ssa/series.py does not carry them yet) the only problems
left are the two schedule contracts a maintainer has to add: COE locks 53 h
before the exercise close and the passenger-volume rounds lock ~41 days
before release, by the cards' owner decisions, where the default contract
is 48 h.
"""
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import baselines, provenance, season, series       # noqa: E402
from ssa.adapters import sg_carpark, sg_coe, sg_lta_pv, sg_taxi  # noqa: E402

PASSED = []
SOURCES = {"sg_taxi_free_downtown_fri2300": "sg_taxi",
           "sg_hdb_occ_clementi_sat2000": "sg_carpark_hdb",
           "sg_coe_premium_cat_a": "sg_coe",
           "sg_pv_train_ew24ns1_weekday_tapin": "sg_lta_pv",
           "sg_pv_bus_22009_weekday_tapin": "sg_lta_pv"}


def ok(fn):
    PASSED.append(fn.__name__)
    print("ok", fn.__name__)


def docs():
    out = {}
    for p in sorted(glob.glob(os.path.join(ROOT, "docs", "data-cards", "sg-*.rounds.json"))):
        with open(p) as f:
            out[os.path.basename(p)] = json.load(f)
    return out


def test_each_file_cites_its_committed_card_by_hash():
    got = docs()
    assert len(got) == 5
    for name, d in got.items():
        cid = name[:-len(".rounds.json")]
        with open(os.path.join(ROOT, "docs", "data-cards", cid + ".json"), "rb") as f:
            raw = f.read()
        card = json.loads(raw)
        assert d["card"] == f"{cid}@v{card['revision']}" and card["status"] == "approved"
        assert d["card_sha256"] == provenance.digest(raw), name
        for r in d["rounds"]:
            baselines.normalize_spec(r["zero_point"], r["round_id"])
    ok(test_each_file_cites_its_committed_card_by_hash)


def test_the_clock_matches_the_adapters_and_none_is_in_the_season():
    with open(os.path.join(ROOT, "questions", "season0.json")) as f:
        live = {r["round_id"] for r in json.load(f)["rounds"]}
    iso = sg_taxi.sg_common.iso
    for d in docs().values():
        for r in d["rounds"]:
            assert r["round_id"] not in live
            rid = r["round_id"]
            if rid.startswith("sg-taxi-dtc-"):
                assert r["lock_at"] == iso(sg_taxi.lock_instant(rid[-10:]))
            elif rid.startswith("sg-hdb-clementi-"):
                assert r["lock_at"] == iso(sg_carpark.lock_instant(rid[-10:]))
            elif rid.startswith("sg-coe-a-"):
                m, b = rid[9:16], rid[17:]
                assert r["lock_at"] == iso(sg_coe.lock_instant(m, b))
            else:
                assert r["lock_at"] == iso(sg_lta_pv.lock_instant(rid[-7:]))
                assert r["release_at"] == iso(sg_lta_pv.release_instant(rid[-7:]))
    ok(test_the_clock_matches_the_adapters_and_none_is_in_the_season)


def test_season_validator_leaves_only_the_two_schedule_contracts():
    added = []
    try:
        for sid, src in SOURCES.items():
            assert sid not in series.SERIES
            series.SERIES[sid] = {"source": src}
            added.append(sid)
        rounds = [r for d in docs().values() for r in d["rounds"]]
        problems = season.validate_document({"rounds": rounds})
    finally:
        for sid in added:
            del series.SERIES[sid]
    for p in problems:
        rid = p.split(":")[0]
        assert "release is" in p and "requires 48h" in p, p
        assert rid.startswith(("sg-coe-a-", "sg-pv-")), p
    assert sum(p.startswith("sg-coe-a-") for p in problems) == 2
    assert sum(p.startswith("sg-pv-") for p in problems) == 6
    ok(test_season_validator_leaves_only_the_two_schedule_contracts)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"{len(PASSED)} passed")
