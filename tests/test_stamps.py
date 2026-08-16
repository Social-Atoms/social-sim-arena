"""Per-round timestamp manifests. No network, and no `ots` client required.

Run: python tests/test_stamps.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa import stamps


class Scratch:
    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="ssa-stamps-")
        self.saved = (stamps.ROOT, stamps.STAMPS, stamps.FORECASTS, stamps.LOCKS)
        stamps.ROOT = self.dir
        stamps.STAMPS = os.path.join(self.dir, "stamps")
        stamps.FORECASTS = os.path.join(self.dir, "forecasts")
        stamps.LOCKS = os.path.join(self.dir, "locks")
        os.makedirs(os.path.join(stamps.FORECASTS, "r1"))
        os.makedirs(stamps.LOCKS)
        return self

    def forecast(self, entrant, body):
        with open(os.path.join(stamps.FORECASTS, "r1", entrant + ".json"), "w") as f:
            json.dump(body, f, indent=2)

    def lock(self, body):
        with open(os.path.join(stamps.LOCKS, "r1.json"), "w") as f:
            json.dump(body, f)

    def __exit__(self, *a):
        stamps.ROOT, stamps.STAMPS, stamps.FORECASTS, stamps.LOCKS = self.saved
        shutil.rmtree(self.dir, ignore_errors=True)


FC = {"round_id": "r1", "entrant": "claude-opus",
      "topline": {"mean": 41.2, "sd": 1.5}, "notes": "x"}


def test_the_canonical_hash_is_the_one_the_repository_already_cites():
    """Sorted keys, no whitespace, UTF-8. tools/validate_submission.py prints
    this on every submission and the paper quotes it, so it is fixed --
    changing the serialization would orphan every proof ever made."""
    import hashlib
    want = hashlib.sha256(
        json.dumps(FC, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert stamps.canonical_sha256(FC) == want


def test_a_manifest_covers_every_forecast_and_the_frozen_history():
    with Scratch() as s:
        s.forecast("claude-opus", FC)
        s.forecast("persistence", dict(FC, entrant="persistence"))
        s.lock({"round_id": "r1", "history": [{"date": "2026-08-01", "value": 40}]})
        m = stamps.build_manifest("r1", "2026-08-12T14:00:00Z")
        assert m["forecast_count"] == 2
        assert set(m["forecasts"]) == {"claude-opus", "persistence"}
        assert m["forecasts"]["claude-opus"] == stamps.canonical_sha256(FC)
        # Without the lock snapshot a stamp proves the forecasts existed but
        # not what they forecast *from*, and "the baselines were computed from
        # strictly pre-lock data" is a claim too.
        assert m["lock_snapshot_sha256"], m


def test_the_hash_is_of_the_object_not_the_file():
    """Whitespace in a submitted file is not part of what was claimed: two
    files differing only in indentation state the same forecast."""
    with Scratch() as s:
        s.forecast("claude-opus", FC)
        a = stamps.build_manifest("r1", "t")["forecasts"]["claude-opus"]
        with open(os.path.join(stamps.FORECASTS, "r1", "claude-opus.json"), "w") as f:
            json.dump(FC, f)                      # same object, no indent
        b = stamps.build_manifest("r1", "t")["forecasts"]["claude-opus"]
        assert a == b


def test_a_missing_lock_snapshot_is_recorded_rather_than_faked():
    with Scratch() as s:
        s.forecast("claude-opus", FC)
        assert stamps.build_manifest("r1", "t")["lock_snapshot_sha256"] is None


def test_the_manifest_is_never_rebuilt_once_it_exists():
    """Rewriting it after the stamp invalidates the proof, which is the one
    thing this module exists to prevent. So a forecast that lands after the
    lock is not covered -- the correct outcome, and what lock-audit.yml
    independently rejects."""
    with Scratch() as s:
        s.forecast("claude-opus", FC)
        stamps.ensure("r1", "2026-08-12T14:00:00Z")
        first = json.load(open(stamps.manifest_path("r1")))
        s.forecast("late-entrant", dict(FC, entrant="late-entrant"))
        stamps.ensure("r1", "2026-08-12T14:00:00Z")
        again = json.load(open(stamps.manifest_path("r1")))
        assert again == first, "the manifest moved after it was stamped"
        assert "late-entrant" not in again["forecasts"]


def test_a_missing_client_leaves_the_manifest_and_says_so():
    """The manifest is useful on its own and a refresh with forecasts to file
    must not die over a proof -- but an unstamped round has to say so."""
    with Scratch() as s:
        s.forecast("claude-opus", FC)
        saved, stamps.have_client = stamps.have_client, lambda: False
        try:
            st = stamps.ensure("r1", "2026-08-12T14:00:00Z")
        finally:
            stamps.have_client = saved
        assert st["manifest"] is True
        assert st["proof"] is False
        assert st["bitcoin_attested"] is False
        assert st["ok"] is False, "an unstamped round must not report success"
        assert os.path.exists(stamps.manifest_path("r1"))


def test_status_of_a_round_that_was_never_stamped():
    with Scratch():
        assert stamps.status("nope") == {"round_id": "nope", "manifest": False}


def test_attestation_is_read_from_the_proof_not_assumed():
    with Scratch() as s:
        s.forecast("claude-opus", FC)
        stamps.write_manifest(stamps.build_manifest("r1", "t"))
        # No proof file at all: never attested, and no crash reaching for one.
        assert stamps.attested(stamps.manifest_path("r1")) is False


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
