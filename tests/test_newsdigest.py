"""Hand-checked unit tests for the news corpus archive. No network.

Run: python tests/test_newsdigest.py
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa.adapters import newsdigest as nd


WIKITEXT = """
'''Politics and elections'''
*[[2026 midterm elections]]
**The [[United States Senate]] votes 51-49 to confirm the nominee. (''[[Reuters]]'')
*Turnout in the special election reaches 41%. ([https://ex.com/a AP])
'''Sports'''
*A team wins a game.
'''Business and economy'''
*The [[Federal Reserve]] holds rates steady.<ref>cite</ref>
"""


def test_parse_keeps_leaves_and_drops_topic_lines():
    items = nd.parse_items(WIKITEXT, ("Politics and elections",))
    texts = [t for _, t in items]
    assert "2026 midterm elections" not in texts, "a parent topic was emitted as news"
    assert any(t.startswith("The United States Senate votes 51-49") for t in texts), texts
    assert any("(AP)" in t for t in texts), texts
    assert all("<ref>" not in t and "[[" not in t for t in texts), texts


def test_parse_with_no_category_filter_keeps_every_heading():
    # The archive stores every heading so that a change to CATEGORIES costs a
    # re-parse and not another pass over Wikipedia.
    cats = {c for c, _ in nd.parse_items(WIKITEXT, None)}
    assert "Sports" in cats and "Politics and elections" in cats, cats
    assert "Sports" not in {c for c, _ in nd.parse_items(WIKITEXT)}


def test_resolve_index_picks_the_newest_revision_at_or_before_asof():
    idx = [{"revid": 1, "timestamp": "2026-06-03T04:00:00Z"},
           {"revid": 2, "timestamp": "2026-06-04T09:00:00Z"},
           {"revid": 3, "timestamp": "2026-06-06T23:00:00Z"}]
    assert nd.resolve_index(idx, "2026-06-02T14:00:00Z") is None, "page did not exist yet"
    assert nd.resolve_index(idx, "2026-06-04T09:00:00Z")["revid"] == 2, "at the instant counts"
    assert nd.resolve_index(idx, "2026-06-05T14:00:00Z")["revid"] == 2
    assert nd.resolve_index(idx, "2026-09-01T14:00:00Z")["revid"] == 3


def test_pooled_storage_round_trips():
    body = {"items": [], "revisions": {}}
    a = [("Politics and elections", "one"), ("Politics and elections", "two")]
    b = a + [("Business and economy", "three")]
    nd.store_revision(body, 111, a)
    nd.store_revision(body, 222, b)
    # Shared text is stored once, and each revision still reads back exactly.
    assert len(body["items"]) == 3, body["items"]
    assert [i["text"] for i in nd.revision_items(body, 111)] == ["one", "two"]
    assert [i["text"] for i in nd.revision_items(body, 222)] == ["one", "two", "three"]
    assert nd.revision_items(body, 999) is None


def test_archive_serves_only_asofs_its_index_can_answer():
    """An index fetched at time T knows nothing about edits after T, so serving
    an asof past `fetched_at` would silently answer with a stale revision and
    the backtest would read a corpus that is missing the days closest to the
    lock -- exactly the days that matter most."""
    tmp = tempfile.mkdtemp()
    old = nd.DAYS
    try:
        nd.DAYS = tmp
        d = date(2026, 6, 3)
        body = {"date": d.isoformat(), "title": nd.page_title(d),
                "fetched_at": "2026-06-10T00:00:00Z", "missing": False,
                "index": [{"revid": 7, "timestamp": "2026-06-04T09:00:00Z"}],
                "items": [{"category": "Politics and elections", "text": "one"},
                          {"category": "Sports", "text": "two"}],
                "revisions": {"7": [0, 1]}}
        nd.save_day(d, body)
        assert nd.from_day_archive(d, "2026-06-20T14:00:00Z") is None, \
            "served an asof the stored index cannot know about"
        got = nd.from_day_archive(d, "2026-06-05T14:00:00Z")
        assert got["revid"] == 7 and got["revision_timestamp"] == "2026-06-04T09:00:00Z"
        # Filtering happens on read, from the all-headings pool.
        assert [i["text"] for i in got["items"]] == ["one"], got["items"]
        # Before the page existed: a recorded gap, not an error.
        gap = nd.from_day_archive(d, "2026-06-04T00:00:00Z")
        assert gap["revid"] is None and gap["items"] == []
    finally:
        nd.DAYS = old
        shutil.rmtree(tmp)


def test_digest_reads_the_archive_and_never_the_network():
    """The whole point of the pre-pass: a backtest prompt is built from disk."""
    tmp = tempfile.mkdtemp()
    pages, days = nd.PAGES, nd.DAYS
    try:
        nd.PAGES = os.path.join(tmp, "pages")
        nd.DAYS = os.path.join(tmp, "days")
        asof = "2026-06-20T14:00:00Z"
        for k in range(1, 15):
            d = date(2026, 6, 20 - k)
            nd.save_day(d, {
                "date": d.isoformat(), "title": nd.page_title(d),
                "fetched_at": "2026-08-01T00:00:00Z", "missing": False,
                "index": [{"revid": 1000 + k, "timestamp": d.isoformat() + "T23:00:00Z"}],
                "items": [{"category": "Politics and elections", "text": f"event {k}"}],
                "revisions": {str(1000 + k): [0]}})
        def boom(*a, **kw):
            raise AssertionError("the archive should have answered this")
        real_get, nd._get = nd._get, boom
        try:
            out = nd.digest(asof)
        finally:
            nd._get = real_get
        assert out["days_missing"] == 0, out["days_missing"]
        assert out["text"].count("[Politics and elections]") == 14
        assert "2026-06-06:" in out["text"] and "2026-06-20:" not in out["text"], \
            "the lock day itself must never be in the corpus"
    finally:
        nd.PAGES, nd.DAYS = pages, days
        shutil.rmtree(tmp)


def test_the_memo_hands_out_copies():
    """A caller that edits the digest it was given must not edit the copy the
    next caller gets -- every entrant in a round is supposed to see the same
    corpus, and a shared mutable dict is exactly how that stops being true."""
    tmp = tempfile.mkdtemp()
    pages, days = nd.PAGES, nd.DAYS
    nd._digest_memo.clear()
    try:
        nd.PAGES, nd.DAYS = os.path.join(tmp, "pages"), os.path.join(tmp, "days")
        d = date(2026, 6, 19)
        nd.save_day(d, {"date": d.isoformat(), "title": nd.page_title(d),
                        "fetched_at": "2026-08-01T00:00:00Z", "missing": False,
                        "index": [{"revid": 5, "timestamp": "2026-06-19T23:00:00Z"}],
                        "items": [{"category": "Politics and elections", "text": "e"}],
                        "revisions": {"5": [0]}})
        asof = "2026-06-20T14:00:00Z"
        first = nd.digest(asof, days=1)
        first["text"] = "tampered"
        second = nd.digest(asof, days=1)
        assert second["text"] != "tampered", "the memo handed out its own dict"
        assert "[Politics and elections] e" in second["text"], second["text"]
    finally:
        nd.PAGES, nd.DAYS = pages, days
        nd._digest_memo.clear()
        shutil.rmtree(tmp)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
