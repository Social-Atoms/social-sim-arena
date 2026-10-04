"""The one-time Season 0 correction (#178, #187) stays applied and stays true.

Offline: Civiqs, Trends and Silver Bulletin values are re-derived from the
committed archives. Wikipedia keeps no archive here, so its two rows are
checked for shape and against the audit's recorded values, without a request.
"""
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import correct_season0_178 as fix  # noqa: E402
from ssa import resolve  # noqa: E402

AUDITED_WIKI = {"wiki-2026-08-30-trump": 291.0, "wiki-2026-08-30-taylor-swift": 88.2}


def _load(*parts):
    with open(os.path.join(ROOT, *parts)) as fh:
        return json.load(fh)


def test_every_correction_is_rederived_from_the_rounds_own_archive():
    resolved = _load("resolutions", "resolved.json")
    for rid, (sid, day, derive, _) in fix.CORRECTIONS.items():
        entry = resolved[rid]
        assert entry.get("corrected"), f"{rid} carries no corrected block"
        assert entry["observed_date"] == day and entry["series"] == sid, rid
        replaced = entry["corrected"]["replaces"]
        assert replaced["observed_date"] != day or rid.endswith("family-finances"), \
            f"{rid}: the superseded entry was not kept"
        if derive is fix.wiki_value:
            assert entry["value"] == AUDITED_WIKI[rid], rid
            continue
        assert entry["value"] == derive(sid, day), \
            f"{rid}: stored {entry['value']} is not what the archive says"


def test_every_voided_round_left_the_season_with_its_reason():
    live = {r["round_id"] for r in _load("questions", "season0.json")["rounds"]}
    resolved = _load("resolutions", "resolved.json")
    for rid in fix.VOIDS:
        assert rid not in live, f"{rid} is voided but still in the season"
        doc = _load("questions", "legacy", rid + ".json")
        assert doc["withdrawn_at"] > doc["lock_at"], \
            f"{rid}: a void after the lock must say so by its timestamp"
        if rid in resolved:
            assert resolved[rid].get("voided", {}).get("why"), \
                f"{rid}: its resolution does not say it was voided"


def test_a_voided_resolution_does_not_hold_an_observation():
    rd = {"round_id": "live", "series": "s", "lock_at": "2026-01-01T00:00:00Z",
          "release_at": "2026-01-02T00:00:00Z"}
    point = {"value": 1.0, "observed_date": "2026-01-02", "series": "s"}
    held = {"old": dict(point)}
    voided = {"old": dict(point, voided={"at": "x", "why": "y"})}
    real = resolve.resolve_round
    resolve.resolve_round = lambda r, s, now: (dict(point), None)
    try:
        now = datetime(2026, 1, 3, tzinfo=timezone.utc)
        new, skipped = resolve.resolve_all({"rounds": [rd]}, {}, held, now)
        assert not new and skipped, "a live resolution must still block a second claim"
        new, skipped = resolve.resolve_all({"rounds": [rd]}, {}, voided, now)
        assert "live" in new, f"a voided resolution blocked a live round: {skipped}"
    finally:
        resolve.resolve_round = real


def test_rerunning_the_correction_changes_nothing():
    wiki = {rid: fix.CORRECTIONS[rid] for rid in AUDITED_WIKI}
    for rid, (sid, day, _, why) in wiki.items():
        fix.CORRECTIONS[rid] = (sid, day, lambda s, d, v=AUDITED_WIKI[rid]: v, why)
    try:
        _, _, legacy, lines = fix.plan(datetime.now(timezone.utc))
    finally:
        fix.CORRECTIONS.update(wiki)
    assert not legacy, f"would void again: {sorted(legacy)}"
    assert all(line.lstrip().startswith("=") for line in lines), \
        "\n".join(l for l in lines if not l.lstrip().startswith("="))


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
