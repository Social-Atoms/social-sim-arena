"""The Urban Track registry (ssa/urban.py) and the series it feeds.

Every series a proposed rounds file names is registered, audited and filed
under the `urban` subject; each is read from its own archive, never the
network; a source with no capture yet builds to an empty series instead of
failing the run.
"""
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import domains, inventory, series, urban            # noqa: E402

PASSED = []


def ok(fn):
    PASSED.append(fn.__name__)
    print("ok", fn.__name__)


def proposed():
    out = []
    for p in sorted(glob.glob(os.path.join(ROOT, "docs", "data-cards", "*.rounds.json"))):
        with open(p) as f:
            out.extend(json.load(f)["rounds"])
    return out


def test_every_proposed_series_is_registered_audited_and_urban():
    rounds = proposed()
    assert len(rounds) >= 30, "the rounds files look empty"
    for r in rounds:
        sids = list(r.get("cells") or []) + [r["series"]]
        for sid in sids:
            spec = series.SERIES.get(sid)
            assert spec, f"{r['round_id']}: {sid} is not registered"
            assert inventory.state_of(spec["source"]), f"{sid}: unaudited source"
            assert domains.domain_of(sid) == "urban", sid
            assert urban.get(spec["source"]), f"{sid}: no urban registry entry"
    # Seattle has no rounds file yet; its series is registered per its card.
    assert series.SERIES["seattle_calls_weekly"]["card"] == "seattle-community-calls@v4"
    ok(test_every_proposed_series_is_registered_audited_and_urban)


def test_every_urban_entry_names_its_card_and_the_series_agree():
    for key, src in urban.SOURCES.items():
        assert src.card and "@v" in src.card, key
        assert inventory.state_of(key), f"{key}: not in the inventory"
    for sid in series.URBAN_SERIES:
        spec = series.SERIES[sid]
        for k in ("question", "methodology", "unit", "cadence", "publisher"):
            assert spec[k], (sid, k)
        assert spec["card"] in urban.get(spec["source"]).card, sid
    ok(test_every_urban_entry_names_its_card_and_the_series_agree)


def test_the_committed_archives_build_and_missing_ones_build_empty():
    built = {}
    for sid in series.URBAN_SERIES:
        spec = series.SERIES[sid]
        built[sid] = urban.get(spec["source"]).history(dict(spec, sid=sid))
    # Seattle, NYC 311 and TSA have a real capture committed under sources/.
    for sid in ("seattle_calls_weekly", "nyc311_requests_weekly",
                "tsa_checkpoint_weekly"):
        h = built[sid]
        assert len(h) >= 30, (sid, len(h))
        assert all(h[i]["date"] < h[i + 1]["date"] for i in range(len(h) - 1))
        assert all(isinstance(p["value"], float) and p["value"] > 0 for p in h)
    # No capture yet (manual MTA, keyless LTA, SG weekly captures not run).
    assert built["mta_subway_weekly"] == []
    assert built["sg_pv_train_ew24ns1_weekday_tapin"] == []
    ok(test_the_committed_archives_build_and_missing_ones_build_empty)


def test_build_all_takes_injected_rows_and_never_fails_on_an_empty_archive():
    saved = series.SERIES
    series.SERIES = {k: v for k, v in saved.items() if v.get("archive_pending")}
    try:
        given = [{"date": "2026-11-01", "value": 5.0}]
        out = series.build_all({"seattle_cad": {"seattle_calls_weekly": given},
                                "mta_daily": {}})
        assert out["seattle_calls_weekly"] == given
        assert out["mta_subway_weekly"] == []
        assert set(out) == set(series.SERIES)
    finally:
        series.SERIES = saved
    ok(test_build_all_takes_injected_rows_and_never_fails_on_an_empty_archive)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"{len(PASSED)} passed")
