"""Reading the ESI out of prose without inventing anything. No network.

Run: PYTHONPATH=. python tests/test_pentaesi.py

The index has no CSV, no chart endpoint and no data API -- the number lives in
a WordPress post's first paragraph, next to five sub-indicators written in the
identical sentence shape. So every test here is about the same property: the
parser must read the headline or read nothing, and must never read a
sub-indicator and call it the index.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa.adapters import pentaesi


def post(date, body):
    return {"date": date + "T09:00:00", "content": {"rendered": body}}


REAL = (
    "The latest biweekly reading of the Penta-CivicScience Economic Sentiment "
    "Index (ESI) increased 1.5 points to 32.3&#8212;partially reversing the "
    "decline in the previous period. <p>Click to view image.</p> Four of the "
    "ESI&#8217;s five indicators increased during this period. Confidence in "
    "the overall U.S. economy increased the most, rising 3.4 points to 31.4. "
    "&#8212;Confidence in finding a new job increased 3.1 points to 29.0. "
    "&#8212;Confidence in personal finances decreased 1.2 points to 51.0."
)


def test_the_headline_is_read_and_the_sub_indicators_are_not():
    """The failure this module exists to prevent. 31.4, 29.0 and 51.0 are all
    in the same paragraph, in the same shape, and none of them is the index."""
    rows = pentaesi.parse([post("2026-08-12", REAL)])
    assert rows == [{"date": "2026-08-12", "value": 32.3}], rows


def test_a_decimal_point_does_not_end_the_sentence():
    """`[^.]` was the obvious span and it is wrong: every one of these
    sentences contains "0.1 points", so the span cannot reach past the number
    it is trying to read. That one character cost eleven of 2025's releases,
    and the gap looked exactly like the publisher skipping a fortnight."""
    body = ("The latest biweekly reading of the Penta-CivicScience Economic "
            "Sentiment Index (ESI) declined slightly by 0.1 points from 33.8 "
            "to 33.7, marking a minor dip.")
    assert pentaesi.parse([post("2025-06-18", body)]) == \
        [{"date": "2025-06-18", "value": 33.7}]


def test_the_house_style_variants_all_read():
    cases = {
        "the Penta-CivicScience Economic Sentiment Index (ESI) jumped 1.2 "
        "points to 35.5 over the past two weeks": 35.5,
        "The Penta-CivicScience Economic Sentiment Index (ESI) remained flat "
        "at 37.2 following last period's large increase": 37.2,
        "The Penta-CivicScience Economic Sentiment Index (ESI) posted its "
        "largest single-period increase since July 2024, rising 2.6 points "
        "to 34.2": 34.2,
        "The Penta-CivicScience Economic Sentiment Index (ESI) fell sharply "
        "by 2.2 points, from 34.7 to 32.5, ahead of the FOMC": 32.5,
        # The index was HPS-CivicScience before Penta, and was once spelled
        # with a space.
        "The HPS-Civic Science Economic Sentiment Index (ESI) fell 0.9 points "
        "to 41.1": 41.1,
    }
    for body, want in cases.items():
        got = pentaesi.parse([post("2024-01-03", body)])
        assert got and got[0]["value"] == want, (body[:50], got)


def test_a_post_that_never_names_the_index_is_a_gap_not_a_guess():
    """Losing a release is bad. Filling it with a number from the wrong
    sentence is worse, because nothing downstream can tell."""
    body = ("Economic sentiment posted another strong increase over the last "
            "two weeks. Confidence in finding a new job rose 3.1 points to "
            "29.0, its highest since March.")
    assert pentaesi.parse([post("2024-01-17", body)]) == []


def test_an_inline_stylesheet_does_not_become_readable_text():
    """The CMS inlines a stylesheet in the post body, and its selectors survive
    tag-stripping as a wall of `.locker{position:absolute...}` that a bounded
    span will happily match through. Four 2022 releases were lost to it."""
    body = ("The HPS-CivicScience Economic Sentiment Index (ESI) rose 0.5 "
            "points to 42.0. <style>.locker,.locker-loader{position:absolute;"
            "top:0;left:0;width:100%;height:100%}</style>")
    assert pentaesi.parse([post("2022-01-05", body)]) == \
        [{"date": "2022-01-05", "value": 42.0}]


def test_a_misread_large_enough_to_be_a_sub_indicator_stops_the_run():
    """The draft with no anchor produced 47-point jumps. Nothing downstream
    could have noticed, which is why this raises rather than warns."""
    rows = [post("2026-07-29", "the Penta-CivicScience Economic Sentiment "
                               "Index (ESI) decreased 2.6 points to 30.8"),
            post("2026-08-12", "the Penta-CivicScience Economic Sentiment "
                               "Index (ESI) increased 1.5 points to 78.4")]
    try:
        pentaesi.parse(rows)
        assert False, "a 47-point jump was published"
    except RuntimeError as e:
        assert "sub-indicator" in str(e), e


def test_a_revision_is_recorded_rather_than_fatal():
    """The publisher restates a previous reading, as the Conference Board does.
    On 2022-02-02 the stated change misses by 1.5 and both numbers are read
    correctly -- refusing the series over that would throw away four years of
    history to protect against nothing."""
    rows = [post("2022-01-19", "The HPS-Civic Science Economic Sentiment "
                               "Index (ESI) fell 0.9 points to 41.1"),
            post("2022-02-02", "The HPS-CivicScience Economic Sentiment "
                               "Index (ESI) increased 1.7 points to 41.3")]
    assert [r["value"] for r in pentaesi.parse(rows)] == [41.1, 41.3]
    # ... and it is still visible to anyone who asks.
    parsed = [{"date": "2022-01-19", "value": 41.1, "delta": -0.9},
              {"date": "2022-02-02", "value": 41.3, "delta": 1.7}]
    off = pentaesi.reconcile(parsed)
    assert len(off) == 1 and abs(off[0][4] - 1.5) < 0.01, off


def test_only_adjacent_releases_are_reconciled():
    """A delta describes the previous release, not the previous *parsed* row.
    Checking across a gap would report every gap as a data error."""
    parsed = [{"date": "2026-06-03", "value": 30.7, "delta": 0.1},
              {"date": "2026-07-15", "value": 33.4, "delta": 1.5}]   # 42 days
    assert pentaesi.reconcile(parsed) == []


def test_an_empty_feed_raises_rather_than_publishing_nothing():
    """A source that quietly returns nothing is worse than one that fails: the
    site renders, the leaderboard updates, and the series stops moving with
    nothing in the log. The refusal belongs at the network boundary, so an
    offline caller replaying an archive is not forced through it."""
    assert pentaesi.parse(json.dumps([])) == []

    class Empty:
        status_code = 200
        headers = {"X-WP-TotalPages": "1"}

        def raise_for_status(self):
            pass

        def json(self):
            return []

    saved, pentaesi.requests.get = pentaesi.requests.get, lambda *a, **k: Empty()
    try:
        pentaesi.fetch_text()
        assert False, "an empty feed was published as an empty series"
    except RuntimeError as e:
        assert "Refusing" in str(e), e
    finally:
        pentaesi.requests.get = saved


def test_fetch_and_parse_are_separate_as_every_adapter_must_be():
    """A vintage rebuilt from parsed rows is our reading of the file, not the
    file. tests/test_provenance.py asserts this of every upstream adapter."""
    assert callable(pentaesi.fetch_text) and callable(pentaesi.parse)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
