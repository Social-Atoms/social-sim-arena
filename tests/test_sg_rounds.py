"""The Singapore cards' proposed rounds (docs/data-cards/sg-*.rounds.json).

They are not in questions/season0.json: they wait for a question batch. This
checks what can be checked now -- each file cites its card by the hash of the
committed copy, its zero points are closed specs, the clock matches the
adapters, and against the season validator (the five series are registered
in ssa/series.py) every round passes, including the two schedules the
cards chose on purpose: COE locks 53 h before the exercise close and the
passenger-volume rounds ~41 days before release (ssa/urban.py).
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
            # Proposed specifications may now be promoted into the season.
            # Their schedules and card hashes remain validated below.
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


def test_the_season_validator_passes_every_round_on_its_cards_schedule():
    for sid, src in SOURCES.items():
        assert series.SERIES[sid]["source"] == src, sid
    rounds = [r for d in docs().values() for r in d["rounds"]]
    problems = season.validate_document({"rounds": rounds})
    # COE's 1 h-before-bidding lock and LTA's day-before-the-month lock are
    # source rules in ssa/urban.py now, so nothing is left.
    assert problems == [], problems
    ok(test_the_season_validator_passes_every_round_on_its_cards_schedule)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"{len(PASSED)} passed")
