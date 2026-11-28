"""The shared write-once capture and manual ingest (ssa/capture.py)."""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import requests                                               # noqa: E402

from ssa import capture, provenance                           # noqa: E402

PASSED = []


def ok(fn):
    PASSED.append(fn.__name__)
    print("ok", fn.__name__)


class Sandbox:
    def __enter__(self):
        self.dir = tempfile.mkdtemp()
        self.saved = (provenance.ROOT, provenance.ARCHIVE, provenance.MANIFEST)
        provenance.ROOT = self.dir
        provenance.ARCHIVE = os.path.join(self.dir, "sources")
        provenance.MANIFEST = os.path.join(provenance.ARCHIVE, "manifest.json")
        return self

    def __exit__(self, *exc):
        provenance.ROOT, provenance.ARCHIVE, provenance.MANIFEST = self.saved
        shutil.rmtree(self.dir)


class R:
    def __init__(self, status=200, content=b"[1]", headers=None):
        self.status_code, self.content = status, content
        self.headers = headers or {}


def test_get_is_honest_bounded_and_records_freshness():
    seen = []

    def get(url, headers, timeout, allow_redirects):
        seen.append((headers, timeout, allow_redirects))
        return R(headers={"Last-Modified": "Sat, 07 Nov 2026 10:00:00 GMT",
                          "X-SODA2-Data-Out-Of-Date": "false",
                          "X-SODA2-Truth-Last-Modified": "Sat, 07 Nov 2026",
                          "Set-Cookie": "nope"})
    got = capture.get("https://x/y", name="t", accept="application/json", get=get)
    body, url = got
    assert body == b"[1]" and url == "https://x/y"
    h, timeout, redirects = seen[0]
    assert "Mozilla" not in h["User-Agent"] and "research" in h["User-Agent"]
    assert timeout == capture.TIMEOUT and redirects is False
    assert got.headers == {"last-modified": "Sat, 07 Nov 2026 10:00:00 GMT",
                           "x-soda2-data-out-of-date": "false",
                           "x-soda2-truth-last-modified": "Sat, 07 Nov 2026"}
    ok(test_get_is_honest_bounded_and_records_freshness)


def test_get_refuses_a_stale_socrata_body_and_never_retries_an_http_error():
    stale = lambda *a, **k: R(headers={"x-soda2-data-out-of-date": "TRUE"})  # noqa: E731
    try:
        capture.get("u", name="t", get=stale)
        raise AssertionError("a stale replica was accepted")
    except capture.Stale:
        pass
    assert capture.get("u", name="t", get=stale, refuse_stale=False)[0] == b"[1]"
    calls = []

    def blocked(*a, **k):
        calls.append(1)
        return R(status=429)
    try:
        capture.get("u", name="t", get=blocked)
        raise AssertionError("a 429 was not raised")
    except requests.HTTPError as e:
        assert "429" in str(e)
    assert calls == [1]
    drops = []

    def flaky(*a, **k):
        drops.append(1)
        if len(drops) == 1:
            raise requests.ConnectionError("reset")
        return R()
    assert capture.get("u", name="t", get=flaky)[0] == b"[1]" and len(drops) == 2
    drops.clear()

    def dead(*a, **k):
        drops.append(1)
        raise requests.Timeout("slow")
    try:
        capture.get("u", name="t", get=dead)
        raise AssertionError("a timeout was swallowed")
    except requests.Timeout:
        pass
    assert len(drops) == 2, "a dropped connection is retried once, never more"
    ok(test_get_refuses_a_stale_socrata_body_and_never_retries_an_http_error)


def test_daily_capture_is_write_once_and_keeps_headers():
    with Sandbox() as s:
        store = capture.DailyArchive("demo", ext="json")
        calls = []

        def fetch(day):
            calls.append(day)
            return capture.Fetched(b'{"n": 1}', f"u/{day}", {"etag": "w/1"})
        t = datetime(2026, 11, 30, 0, 17, tzinfo=timezone.utc)
        row = store.capture(fetch, parse=json.loads,
                            describe=lambda d: {"n": d["n"]}, now=t)
        assert row == {"fetched_at": "2026-11-30T00:17:00Z", "url": "u/2026-11-30",
                       "sha256": provenance.digest(b'{"n": 1}'), "bytes": 8,
                       "file": "sources/demo/2026-11-30.json", "n": 1,
                       "headers": {"etag": "w/1"}}
        again = store.capture(lambda day: (b'{"n": 2}', "u"), now=t + timedelta(hours=6))
        assert again == row and calls == ["2026-11-30"]
        assert provenance.load_manifest()["demo"]["sha256"] == row["sha256"]
        try:
            store.capture(lambda day: (b"not json", "u"), parse=json.loads,
                          now=t + timedelta(days=1))
            raise AssertionError("a malformed body was filed")
        except ValueError:
            pass
        assert not os.path.exists(store.path("2026-12-01"))
        # A file without its fetches row is never overwritten.
        with open(store.path("2026-12-02"), "wb") as f:
            f.write(b"{}")
        try:
            store.capture(lambda day: (b"{}", "u"), now=t + timedelta(days=2))
            raise AssertionError("overwrote a capture of unknown time")
        except RuntimeError:
            pass
        body, r = store.read("2026-11-30")
        assert body == b'{"n": 1}'
        with open(os.path.join(s.dir, row["file"]), "ab") as f:
            f.write(b" ")
        try:
            store.read("2026-11-30")
            raise AssertionError("a changed archive was read")
        except RuntimeError:
            pass
        fx = store.load()
        lock = datetime(2026, 11, 30, 8, tzinfo=timezone.utc)
        assert store.snapshot_at_or_before(fx, lock) == "2026-11-30"
        assert store.snapshot_at_or_before(fx, lock - timedelta(hours=8)) is None
        # An archive that is not where provenance files it is refused.
        odd = capture.DailyArchive("demo", archive=os.path.join(s.dir, "elsewhere"))
        try:
            odd.capture(lambda day: (b"{}", "u"), now=t + timedelta(days=5))
            raise AssertionError("split the record across two directories")
        except RuntimeError:
            pass
    ok(test_daily_capture_is_write_once_and_keeps_headers)


def test_manual_ingest_checks_first_and_never_backdates_or_rewrites():
    with Sandbox() as s:
        src = os.path.join(s.dir, "download.xlsx")
        with open(src, "wb") as f:
            f.write(b"PK wave 2026-11-01")

        def check(body):
            if b"2026-11-01" not in body:
                raise ValueError("not the expected wave")
            return "2026-11-01"
        now = datetime(2026, 11, 3, 9, tzinfo=timezone.utc)
        out = capture.ingest_manual("xtab", src, check=check, now=now,
                                    describe=lambda wave: {"wave": wave})
        assert out["created"] and out["archive"] == "sources/xtab/2026-11-03.xlsx"
        assert out["row"]["manual"] is True and out["row"]["wave"] == "2026-11-01"
        assert out["sha256"] == provenance.digest(b"PK wave 2026-11-01")
        assert provenance.load_manifest()["xtab"]["note"] == "manual download"
        fetches = json.load(open(os.path.join(s.dir, "sources/xtab/fetches.json")))
        assert fetches["2026-11-03"]["fetched_at"] == "2026-11-03T09:00:00Z"
        # The same bytes again: a no-op.
        assert not capture.ingest_manual("xtab", src, check=check, now=now)["created"]
        # Different bytes for a day held: refused.
        other = os.path.join(s.dir, "other.xlsx")
        with open(other, "wb") as f:
            f.write(b"PK wave 2026-11-01 revised")
        for kwargs in ({"now": now},                                  # same day
                       {"capture_day": "2026-11-02", "now": now}):    # back-dated
            try:
                capture.ingest_manual("xtab", other, check=check, **kwargs)
                raise AssertionError(f"filed with {kwargs}")
            except ValueError:
                pass
        # A failed check writes nothing.
        bad = os.path.join(s.dir, "bad.xlsx")
        with open(bad, "wb") as f:
            f.write(b"PK wave 2026-10-25")
        try:
            capture.ingest_manual("xtab", bad, check=check, capture_day="2026-11-10")
            raise AssertionError("an unchecked file was filed")
        except ValueError:
            pass
        assert sorted(os.listdir(os.path.join(s.dir, "sources/xtab"))) == \
            ["2026-11-03.xlsx", "fetches.json"]
    ok(test_manual_ingest_checks_first_and_never_backdates_or_rewrites)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"{len(PASSED)} passed")
