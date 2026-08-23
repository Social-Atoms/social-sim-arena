"""One index, the model's own queries, and a record of everything. No network.

Run: PYTHONPATH=. python tests/test_search.py

The `web` arm is the only condition in the arena whose corpus does not exist
until a model asks for it, and the only one that cannot be backtested. Both
facts make the archive load-bearing rather than tidy: it is the cost control,
the audit trail, and the sole reason a web forecast can be re-derived at all.
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa import harness
from ssa.adapters import search


class Scratch:
    """A throwaway archive, and no network: `requests.post` raises."""

    def __init__(self, replies=None):
        self.replies = replies or {}
        self.posted = []

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="ssa-search-")
        self.saved = (search.ARCHIVE, search.CACHE, search.ROUNDS,
                      search.requests.post, os.environ.get(search.ENV))
        search.ARCHIVE = self.dir
        search.CACHE = os.path.join(self.dir, "cache")
        search.ROUNDS = os.path.join(self.dir, "rounds")
        search.requests.post = self._post
        os.environ[search.ENV] = "tvly-test"
        # no network, so no need to pace the fake index
        self.saved_interval, search.MIN_INTERVAL = search.MIN_INTERVAL, 0.0
        # The live path logs every paid reply; keep test replies out of the tree.
        self.saved_log = os.environ.get("SSA_REPLIES_DIR")
        os.environ["SSA_REPLIES_DIR"] = os.path.join(self.dir, "replies")
        return self

    def _post(self, url, timeout=None, json=None):
        self.posted.append(json["query"])
        hits = self.replies.get(json["query"], [
            {"title": f"about {json['query']}", "url": "https://e/1",
             "published_date": "2026-08-17", "content": "body of " + json["query"]}])
        return _Reply({"results": hits})

    def __exit__(self, *a):
        (search.ARCHIVE, search.CACHE, search.ROUNDS,
         search.requests.post, key) = self.saved
        search.MIN_INTERVAL = self.saved_interval
        if key is None:
            os.environ.pop(search.ENV, None)
        else:
            os.environ[search.ENV] = key
        if self.saved_log is None:
            os.environ.pop("SSA_REPLIES_DIR", None)
        else:
            os.environ["SSA_REPLIES_DIR"] = self.saved_log
        shutil.rmtree(self.dir, ignore_errors=True)


class _Reply:
    def __init__(self, payload):
        self.status_code = 200
        self._p = payload
        self.text = ""

    def json(self):
        return self._p


ROUND = {"round_id": "r1", "series": "yougov_approval", "unit": "%",
         "question": "q", "release_at": "2026-08-20T14:00:00Z",
         "baselines": {"persistence": {"mean": 41.0, "sd": 1.5}}}
HIST = [{"date": f"2026-08-{i+1:02d}", "value": 40.0 + i} for i in range(12)]


def test_a_query_is_bought_once_and_shared_by_every_entrant():
    """Fifteen models issuing overlapping keywords is the expected case, and
    it is what makes the arm affordable at all: the cache is keyed on the
    query, not on who asked."""
    with Scratch() as s:
        search.search("trump approval august 2026")
        search.search("trump approval august 2026")
        search.search("  TRUMP Approval August 2026  ")   # same question
        assert s.posted == ["trump approval august 2026"], s.posted


def test_the_retrieval_settings_are_part_of_the_cache_key():
    """A corpus gathered three-deep is not a corpus gathered ten-deep. Serving
    one for the other would silently mix two experiments -- the same reason
    `call_identity` carries the base URL."""
    with Scratch() as s:
        search.search("q")
        saved = search.RESULTS_PER_QUERY
        try:
            search.RESULTS_PER_QUERY = saved + 7
            search.search("q")
        finally:
            search.RESULTS_PER_QUERY = saved
        assert s.posted == ["q", "q"], "settings changed and the cache still hit"


def test_a_repeated_query_does_not_buy_a_bigger_corpus():
    """A model that asks the same thing twice should not out-retrieve one that
    asks two different things. The budget is on distinct information."""
    with Scratch() as s:
        got = search.gather(["approval polls", "approval polls", "economy news"])
        assert len(got) == 2, got
        assert s.posted == ["approval polls", "economy news"]


def test_the_query_budget_is_enforced_here_not_asked_for_politely():
    with Scratch() as s:
        search.gather([f"q{i}" for i in range(search.MAX_QUERIES + 5)])
        assert len(s.posted) == search.MAX_QUERIES, s.posted


def test_no_key_and_no_archive_raises_instead_of_forecasting_blind():
    with Scratch():
        os.environ.pop(search.ENV, None)
        try:
            search.search("never asked before")
            assert False, "a forecast would have been filed with no corpus"
        except RuntimeError as e:
            assert "not in the archive" in str(e), e
        # ... but an archived query still answers offline, which is what lets
        # anyone re-derive a filed forecast without a key.
        os.environ[search.ENV] = "tvly-test"
        search.search("asked once")
        os.environ.pop(search.ENV, None)
        assert search.search("asked once")["query"] == "asked once"


def test_every_entrant_reads_the_same_rendering_of_the_same_documents():
    """Two entrants that retrieved the same documents must read the same words.
    A per-model formatter would put a second uncontrolled variable into an arm
    that exists to isolate one."""
    with Scratch():
        a = search.render(search.gather(["shared query"]))
        b = search.render(search.gather(["shared query"]))
        assert a == b
        assert "shared query" in a and "body of shared query" in a


def test_the_frozen_round_file_is_written_once_and_never_moves():
    """The corpus is part of what the entrant was shown at lock time. A file
    that changes afterwards proves nothing, which is the same argument `locks/`
    rests on."""
    with Scratch():
        recs = search.gather(["first"])
        search.record_round("r1", "claude-opus", ["first"], recs)
        first = json.load(open(search.round_path("r1", "claude-opus")))
        search.record_round("r1", "claude-opus", ["second"],
                            search.gather(["second"]))
        again = json.load(open(search.round_path("r1", "claude-opus")))
        assert again == first, "the frozen corpus moved after the fact"
        assert again["queries"] == ["first"]


def test_the_search_runs_once_per_round_and_the_refresh_reads_the_file():
    """The refresh runs every six hours. Re-searching each time would re-bill
    the index and, worse, hand the entrant a different corpus for a round it
    had already answered."""
    with Scratch() as s:
        calls = []

        def fake_call(entrant, prompt, with_usage=False, context=None, via=None):
            calls.append(prompt)
            # `"queries"` with its quotes appears only in the query turn's
            # answer template; the forecast turn merely mentions the word.
            if '"queries"' in prompt:
                text = '{"queries": ["what moved approval this week"]}'
            else:
                text = '{"mean": 41.2, "sd": 1.4}'
            return (text, None) if with_usage else text

        saved_call, saved_key = harness.call_provider, harness.has_key
        harness.call_provider, harness.has_key = fake_call, lambda e: True
        try:
            f1 = harness.forecast("claude-opus-web", ROUND, HIST)
            f2 = harness.forecast("claude-opus-web", ROUND, HIST)
        finally:
            harness.call_provider, harness.has_key = saved_call, saved_key

        assert s.posted == ["what moved approval this week"], s.posted
        assert sum(1 for p in calls if '"queries"' in p) == 1, \
            "the query turn ran twice for one round"
        assert f1["topline"] == f2["topline"]
        assert "context=web" in f1["notes"]


def test_the_model_supplies_the_queries_and_a_non_answer_is_not_papered_over():
    """The queries being the model's own is the entire difference between this
    arm and `news`. Substituting ours when a model fails to answer would put
    our keywords into the one place they must not be."""
    assert harness.parse_queries('{"queries": ["a", " b ", "", 7, null]}') == \
        ["a", "b"]
    for bad in ('{"queries": []}', '{"queries": "a, b"}', '{"nope": 1}'):
        try:
            harness.parse_queries(bad)
            assert False, f"{bad} was accepted"
        except ValueError:
            pass


def test_the_query_turn_asks_about_the_same_question_it_will_forecast():
    """A model choosing queries for a question it has not been shown is
    choosing badly for reasons that are our fault, not its own."""
    q = harness.build_query_prompt(ROUND, HIST, "web")
    f = harness.build_prompt(ROUND, HIST, "recent10", "direct")
    for pt in HIST[-3:]:
        assert str(pt["value"]) in q, "the query turn hides the history"
    assert ROUND["release_at"][:10] in q
    assert '"queries"' in q
    assert "<number>" not in q, "the query turn must not ask for a forecast"
    assert "<number>" in f


def test_a_cached_reply_goes_stale_after_the_window():
    """The cache dedupes overlapping queries inside one round's buying window.
    It must not also serve last week's snippets to next week's round just
    because two models phrased the same words."""
    with Scratch() as s:
        search.gather(["q1"])
        path = search._cache_path("q1")
        rec = json.load(open(path))
        rec["fetched_at"] = "2026-08-01T00:00:00Z"
        json.dump(rec, open(path, "w"))
        search.gather(["q1"])
        assert s.posted == ["q1", "q1"], \
            "an aged cache entry was served instead of refetched"


def test_a_corpus_gathered_before_the_window_is_regathered_inside_it():
    """A record frozen weeks before its lock -- the era when the refresh bought
    forecasts from listing day -- is not "what the entrant saw at lock". It is
    superseded once, when the window opens, and the replacement then stays."""
    from datetime import datetime, timedelta, timezone
    with Scratch() as s:
        lock = (datetime.now(timezone.utc) + timedelta(days=2)) \
            .strftime("%Y-%m-%dT%H:%M:%SZ")
        rnd = dict(ROUND, round_id="r2", lock_at=lock)
        search.record_round("r2", "claude-opus-web", ["stale question"],
                            search.gather(["stale question"]),
                            now="2026-08-01T00:00:00Z")

        def fake_call(entrant, prompt, with_usage=False, context=None, via=None):
            if '"queries"' in prompt:
                text = '{"queries": ["fresh question"]}'
            else:
                text = '{"mean": 41.2, "sd": 1.4}'
            return (text, None) if with_usage else text

        saved_call, saved_key = harness.call_provider, harness.has_key
        harness.call_provider, harness.has_key = fake_call, lambda e: True
        try:
            harness.forecast("claude-opus-web", rnd, HIST)
            first = json.load(open(search.round_path("r2", "claude-opus-web")))
            harness.forecast("claude-opus-web", rnd, HIST)
            again = json.load(open(search.round_path("r2", "claude-opus-web")))
        finally:
            harness.call_provider, harness.has_key = saved_call, saved_key
        assert first["queries"] == ["fresh question"], first["queries"]
        assert again == first, "an in-window corpus was re-gathered"


def test_the_arm_still_refuses_to_be_backtested():
    """A search run today over a 2025 release retrieves the published answer.
    Nothing in this module weakens that."""
    try:
        harness.assert_prospective("web")
        assert False, "the backtest accepted live search"
    except ValueError as e:
        assert "already published" in str(e), e


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
