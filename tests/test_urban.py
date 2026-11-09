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

from ssa import domains, inventory, season, series, urban    # noqa: E402

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


def _shift(stamp, **kw):
    from datetime import datetime, timedelta
    t = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ") + timedelta(**kw)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _schedule(r):
    return [p for p in season.validate_document({"rounds": [r]})
            if "invalid schedule" in p]


def test_each_source_rule_holds_its_cards_instants_to_the_second():
    """Every proposed round passes on its card's schedule; moving the lock
    or the release by one minute, or naming a week that does not end on a
    Sunday, fails -- for each source with a rule."""
    rounds = proposed()
    seen = set()
    for r in rounds:
        src = series.SERIES[r["series"]]["source"]
        rule = urban.get(src)
        assert _schedule(r) == [], (r["round_id"], _schedule(r))
        if not rule.has_schedule or src in seen:
            continue
        seen.add(src)
        late = dict(r, lock_at=_shift(r["lock_at"], minutes=1))
        assert _schedule(late) and rule.card.split(" ")[0] in _schedule(late)[0], late
        early = dict(r, release_at=_shift(r["release_at"], minutes=-1))
        assert _schedule(early), r["round_id"]
    assert seen == {"nyc311", "mta_daily", "tsa_volumes", "sg_coe", "sg_taxi",
                    "sg_carpark_hdb", "sg_lta_pv"}, seen
    week = next(r for r in rounds if r["round_id"].startswith("nyc311-"))
    saturday = dict(week, round_id=week["round_id"][:-2] + "19")
    assert "not the Sunday" in _schedule(saturday)[0]
    ok(test_each_source_rule_holds_its_cards_instants_to_the_second)


def test_seattle_rounds_follow_the_same_rule():
    from ssa.adapters import seattle_cad
    from datetime import date
    monday = date(2026, 12, 14)
    r = {"round_id": "seattle-calls-2026-12-20", "tracker": "seattle_cad",
         "series": "seattle_calls_weekly", "question": "q", "unit": "calls",
         "release_estimated": False, "target_type": "continuous_normal",
         "resolve": "the first archived capture (card seattle-community-calls@v4)",
         "lock_at": urban._iso(seattle_cad.lock_instant(monday)),
         "release_at": urban._iso(seattle_cad.resolution_instant(monday)),
         "zero_point": "yoy4"}
    assert season.validate_document({"rounds": [r]}) == []
    assert r["lock_at"] == "2026-12-14T07:59:00Z"       # Sun 23:59 PST
    assert _schedule(dict(r, lock_at=_shift(r["lock_at"], hours=1)))
    ok(test_seattle_rounds_follow_the_same_rule)


def test_the_lock_reference_is_attached_once_and_frozen():
    """refresh.attach_lock_reference: nothing before the lock; at the lock
    the registry's reference is frozen into the lock snapshot and later runs
    read it back even if the rule would now say something else; a refusal is
    frozen too and leaves the round unscoreable, never on another null."""
    import tempfile
    from datetime import datetime, timezone
    from ssa import baselines, refresh
    calls = []

    class Stub:
        card = "stub@v2"
        has_lock_reference = True

        def __init__(self, out):
            self.out = out

        def lock_reference(self, r):
            calls.append(r["round_id"])
            if isinstance(self.out, Exception):
                raise self.out
            return self.out

    r = {"round_id": "t-urban-ref", "series": "nyc311_requests_weekly",
         "lock_at": "2026-12-14T04:59:00Z", "zero_point": "yoy4"}
    saved_locks, saved_get = refresh.LOCKS, urban.get
    tmp = tempfile.mkdtemp(prefix="ssa-urban-")
    refresh.LOCKS = tmp
    try:
        before = datetime(2026, 12, 13, tzinfo=timezone.utc)
        after = datetime(2026, 12, 15, tzinfo=timezone.utc)
        urban.get = lambda key: Stub({"zero_point": "yoy4", "snapshot": "s.json",
                                      "ranked": {"mean": 100.0, "sd": 5.0}})
        row = {"baselines": None, "scoreable": False, "baseline_note": "short"}
        refresh.attach_lock_reference(row, r, before)
        assert row["zero_point_reference"]["status"] == "pending" and not calls
        refresh.attach_lock_reference(row, r, after)
        assert row["baselines"]["yoy4"]["mean"] == 100.0 and row["scoreable"]
        assert "baseline_note" not in row
        assert row["zero_point_reference"]["snapshot"] == "s.json"
        # A later run whose rule would answer differently reads the freeze.
        urban.get = lambda key: Stub({"zero_point": "yoy4",
                                      "ranked": {"mean": 1.0, "sd": 1.0}})
        row2 = {"baselines": {"persistence": {"mean": 9.0, "sd": 1.5}}}
        refresh.attach_lock_reference(row2, r, after)
        assert row2["baselines"]["yoy4"]["mean"] == 100.0, "recomputed after lock"
        assert row2["baselines"]["persistence"]["mean"] == 9.0
        assert calls == ["t-urban-ref"]
        # A refusal is the card's answer, frozen like a reference.
        r2 = dict(r, round_id="t-urban-refused")
        urban.get = lambda key: Stub(baselines.NotPostable("no capture at lock"))
        row3 = {"baselines": {"persistence": {"mean": 9.0, "sd": 1.5}}}
        refresh.attach_lock_reference(row3, r2, after)
        assert row3["baselines"] is None and row3["scoreable"] is False
        assert "no capture at lock" in row3["baseline_note"]
        snap = refresh.read_lock_snapshot("t-urban-refused")
        assert snap["zero_point_reference"]["status"] == "refused"
        # A transient failure is reported and not frozen.
        r3 = dict(r, round_id="t-urban-broken")
        urban.get = lambda key: Stub(RuntimeError("sha256 mismatch"))
        row4 = {"baselines": {}}
        refresh.attach_lock_reference(row4, r3, after)
        assert "retried next run" in row4["baseline_note"]
        assert refresh.read_lock_snapshot("t-urban-broken") is None
    finally:
        refresh.LOCKS, urban.get = saved_locks, saved_get
        import shutil
        shutil.rmtree(tmp)
    ok(test_the_lock_reference_is_attached_once_and_frozen)


def test_the_round_must_name_the_zero_point_the_card_computes():
    rule = urban.get("nyc311")
    r = {"round_id": "nyc311-2026-12-20", "zero_point": "yoy4"}
    saved = rule._reference
    rule._reference = lambda r, t: {"zero_point": "mean4", "ranked": {}}
    try:
        rule.lock_reference(r)
        raise AssertionError("a round named yoy4 was ranked on mean4")
    except urban.NotPostable as e:
        assert "'yoy4'" in str(e) and "'mean4'" in str(e)
    finally:
        rule._reference = saved
    ok(test_the_round_must_name_the_zero_point_the_card_computes)


def test_every_rounds_file_passes_the_real_validator_but_for_two_rights():
    """With the series registered and the source schedules in place, every
    proposed round is publishable against the reviewed season except MTA's
    and TSA's, whose rights verdicts wait on a maintainer."""
    with open(os.path.join(ROOT, "questions", "season0.json")) as f:
        reviewed = season.rounds_from(json.load(f))
    problems = season.validate_document({"rounds": reviewed + proposed()})
    left = {p.split(":")[0] for p in problems}
    assert all("rights are 'unresolved'" in p for p in problems), problems
    assert {rid.rsplit("-", 3)[0] for rid in left} == {"mta-subway", "tsa-weekly"}, left
    assert len(left) == 12
    ok(test_every_rounds_file_passes_the_real_validator_but_for_two_rights)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"{len(PASSED)} passed")
