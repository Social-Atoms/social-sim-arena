"""Where every number came from. No network.

Run: python tests/test_provenance.py
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa import provenance


class Scratch:
    """A throwaway archive, so tests never write into the committed one."""

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="ssa-prov-")
        self.saved = (provenance.ARCHIVE, provenance.MANIFEST, provenance.ROOT)
        provenance.ARCHIVE = self.dir
        provenance.MANIFEST = os.path.join(self.dir, "manifest.json")
        provenance.ROOT = os.path.dirname(self.dir)
        return self

    def __exit__(self, *a):
        provenance.ARCHIVE, provenance.MANIFEST, provenance.ROOT = self.saved
        shutil.rmtree(self.dir, ignore_errors=True)


def test_a_body_is_stored_verbatim_and_described():
    with Scratch():
        body = "pollster,approve\nYouGov,41.2\n"
        b = provenance.record("sb_approval", "https://example/pub?output=csv", body,
                              fetched_at="2026-08-15T02:00:00Z")
        assert b["bytes"] == len(body.encode())
        assert b["sha256"] == provenance.digest(body)
        assert b["file"].endswith("sb_approval/2026-08-15.csv"), b["file"]
        with open(os.path.join(provenance.ARCHIVE, "sb_approval",
                               "2026-08-15.csv")) as f:
            assert f.read() == body, "the archive must be the body, byte for byte"


def test_a_second_fetch_the_same_day_does_not_write_a_second_vintage():
    """The refresh runs every six hours. Four identical copies of one file are
    three files of noise."""
    with Scratch():
        body = "a,b\n1,2\n"
        provenance.record("x", "u", body, fetched_at="2026-08-15T02:00:00Z")
        provenance.record("x", "u", body, fetched_at="2026-08-15T08:00:00Z")
        assert os.listdir(os.path.join(provenance.ARCHIVE, "x")) == ["2026-08-15.csv"]
        # but the later confirmation is recorded
        m = provenance.load_manifest()
        assert m["x"]["fetched_at"] == "2026-08-15T08:00:00Z"


def test_when_it_last_changed_is_not_when_it_was_last_seen():
    """A stale upstream is the failure that hides: the pipeline keeps running,
    the site keeps rendering, the numbers quietly stop moving. `fetched_at`
    alone cannot tell that apart from a healthy source."""
    with Scratch():
        provenance.record("x", "u", "one\n", fetched_at="2026-08-01T00:00:00Z")
        provenance.record("x", "u", "one\n", fetched_at="2026-08-15T00:00:00Z",
                          day="2026-08-15")
        m = provenance.load_manifest()["x"]
        assert m["fetched_at"] == "2026-08-15T00:00:00Z"
        assert m["changed_at"] == "2026-08-01T00:00:00Z", m
        now = datetime(2026, 8, 15, tzinfo=timezone.utc)
        assert provenance.unchanged_since("x", now) == 14

        provenance.record("x", "u", "two\n", fetched_at="2026-08-15T06:00:00Z",
                          day="2026-08-15")
        assert provenance.load_manifest()["x"]["changed_at"] == "2026-08-15T06:00:00Z"
        later = datetime(2026, 8, 15, 12, tzinfo=timezone.utc)
        assert provenance.unchanged_since("x", later) == 0


def test_a_changed_body_replaces_the_days_vintage_rather_than_being_dropped():
    with Scratch():
        provenance.record("x", "u", "one\n", fetched_at="2026-08-15T02:00:00Z")
        provenance.record("x", "u", "one\ntwo\n", fetched_at="2026-08-15T08:00:00Z")
        with open(os.path.join(provenance.ARCHIVE, "x", "2026-08-15.csv")) as f:
            assert f.read() == "one\ntwo\n", "the day holds the freshest body"


def test_the_manifest_is_json_and_survives_a_corrupt_read():
    with Scratch():
        provenance.record("x", "u", "one\n", fetched_at="2026-08-15T02:00:00Z")
        with open(provenance.MANIFEST) as f:
            assert set(json.load(f)["x"]) >= {"url", "fetched_at", "sha256",
                                              "bytes", "file", "changed_at"}
        with open(provenance.MANIFEST, "w") as f:
            f.write("{not json")
        assert provenance.load_manifest() == {}, "a broken manifest must not throw"
        # and the next record rebuilds it rather than refusing
        provenance.record("x", "u", "one\n", fetched_at="2026-08-16T02:00:00Z")
        assert "x" in provenance.load_manifest()


def test_unknown_source_has_no_staleness_rather_than_a_wrong_one():
    with Scratch():
        assert provenance.unchanged_since("never-fetched") is None


def test_the_adapters_can_fetch_and_parse_separately():
    """A vintage rebuilt from parsed rows is our reading of the file, not the
    file. Every upstream adapter has to expose the raw body for that reason."""
    from ssa.adapters import silverbulletin, umich, fredcsv
    for mod in (silverbulletin, umich, fredcsv):
        assert callable(getattr(mod, "fetch_text", None)), mod.__name__
        assert callable(getattr(mod, "parse", None)), mod.__name__
    rows = silverbulletin.parse(
        "pollster,subgroup,population,date,approve,disapprove\n"
        "YouGov,All polls,A,2026-08-01,41.2,55.0\n")
    assert rows[0]["pollster"] == "YouGov"
    assert umich.parse("Month,YYYY,ICS_ALL\nJuly,2026,55.2\n") == \
        [{"date": "2026-07-01", "value": 55.2}]
    assert fredcsv.parse("observation_date,UMCSENT\n2026-06-01,49.5\n") == \
        [{"date": "2026-06-01", "value": 49.5}]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
