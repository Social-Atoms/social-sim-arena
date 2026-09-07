"""Issue #52 fault matrix and operator state.  No network.

Run: PYTHONPATH=. python tests/test_reliability.py
"""
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

from ssa import model_backtest, provenance, refresh, reliability
from ssa import series as series_registry
from ssa.adapters import civiqs, sce, silverbulletin


NOW = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)
# A round closes at its own lock, and every entrant is called in the 24 hours
# before it, so the fixture's close sits inside that window from NOW.
LOCK = "2026-09-13T00:00:00Z"
DEADLINE = LOCK


def source_row(doc, name):
    return next(r for r in doc["source_states"] if r["source"] == name)


def entrant_row(doc, rid, entrant):
    return next(r for r in doc["entrant_round_states"]
                if r["round_id"] == rid and r["entrant"] == entrant)


def queue(status, entrant="claude-opus", spend=0.2):
    status.entrant_queued(
        "r1", entrant, lock_at=LOCK, deadline=DEADLINE,
        route={"via": "direct", "base": "https://provider.example/v1"},
        estimated_spend=spend)
    status.entrant_started("r1", entrant)


def test_declared_state_vocabularies_are_closed():
    assert reliability.SOURCE_STATES == {
        "healthy", "retryable_failure", "stale", "deadline_risk",
        "unresolvable",
    }
    assert reliability.ENTRANT_STATES == {
        "queued", "running", "succeeded", "retryable_failure",
        "terminal_failure", "missed_lock",
    }


def test_source_403_uses_only_a_valid_same_source_archive_and_preserves_siblings():
    status = reliability.RunStatus(NOW)
    persisted = []

    def good():
        persisted.append("sources/good/2026-09-12.csv")
        return [1, 2], persisted[-1]

    def forbidden():
        raise RuntimeError("source @ https://blocked.example HTTP 403: Forbidden")

    def archived():
        return ["last-valid"], \
            "same-source sources/blocked/2026-09-11.csv sha256=abc"

    values, failures = refresh.run_source_tasks([
        ("good", "https://good.example", good),
        ("blocked", "https://blocked.example", forbidden, archived),
    ], status, next_deadline=DEADLINE, next_lock=LOCK)
    doc = status.as_dict()
    assert values == {"good": [1, 2], "blocked": ["last-valid"]}
    assert failures == []
    assert persisted == ["sources/good/2026-09-12.csv"], \
        "a sibling failure erased or prevented a valid vintage"
    assert source_row(doc, "good")["state"] == "healthy"
    blocked = source_row(doc, "blocked")
    assert blocked["state"] == "stale"
    assert blocked["attempts"] == 1 and blocked["next_retry"] is not None
    assert blocked["alert"] and "not evidence of a new release" in \
        blocked["required_action"]
    status.source_health({
        "source": "blocked", "state": "ok", "fetched_days": 0.1,
        "changed_days": 0.1, "budget_fetch_days": 2,
        "budget_change_days": 7,
    }, next_deadline=DEADLINE, next_lock=LOCK)
    still_blocked = source_row(status.as_dict(), "blocked")
    assert still_blocked["state"] == "stale"
    assert still_blocked["last_error"], \
        "the prior-vintage health clock erased this run's live 403"


def test_source_403_without_a_valid_same_source_archive_is_unresolvable():
    status = reliability.RunStatus(NOW)

    def forbidden():
        raise RuntimeError("source @ https://blocked.example HTTP 403: Forbidden")

    def missing_archive():
        raise RuntimeError("no validated provenance vintage exists")

    values, failures = refresh.run_source_tasks([
        ("blocked", "https://blocked.example", forbidden, missing_archive),
    ], status, next_deadline=DEADLINE, next_lock=LOCK)
    blocked = source_row(status.as_dict(), "blocked")
    assert values == {} and len(failures) == 1
    assert blocked["state"] == "unresolvable"
    assert blocked["attempts"] == 1 and blocked["next_retry"] is None
    assert blocked["alert"] and "do not resolve" in blocked["required_action"]
    status.source_health({
        "source": "blocked", "state": "ok", "fetched_days": 0.1,
        "changed_days": 0.1, "budget_fetch_days": 2,
        "budget_change_days": 7,
    }, next_deadline=DEADLINE, next_lock=LOCK)
    after_health = source_row(status.as_dict(), "blocked")
    assert after_health["state"] == "unresolvable"
    assert "HTTP 403" in after_health["last_error"], \
        "an old healthy manifest clock erased the current live failure"


def test_empty_timeout_error_cannot_be_erased_by_legacy_health():
    status = reliability.RunStatus(NOW)
    status.source_started("empty-timeout", route="https://slow.example")
    status.source_failed("empty-timeout", TimeoutError())
    status.source_health({
        "source": "empty-timeout", "state": "ok", "fetched_days": 0.1,
        "changed_days": 0.1, "budget_fetch_days": 2,
        "budget_change_days": 7,
    })
    row = source_row(status.as_dict(), "empty-timeout")
    assert row["state"] == "retryable_failure"
    assert row["last_error"] == "TimeoutError" and row["alert"]


def test_stale_200_is_not_reported_healthy():
    status = reliability.RunStatus(NOW)
    status.source_started("sheet", route="https://sheet.example")
    status.source_failed(
        "sheet", "HTTP 200 but newest row is 30 days old: frozen page",
        evidence="HTTP 200 sha256=abc")
    status.source_health({
        "source": "sheet", "state": "stale", "fetched_days": 0.0,
        "changed_days": 14.0, "budget_fetch_days": 2,
        "budget_change_days": 7, "stale_keys": ["daily-tracker"],
    }, next_deadline=DEADLINE, next_lock=LOCK)
    row = source_row(status.as_dict(), "sheet")
    assert row["state"] == "stale" and row["alert"]
    assert "HTTP 200" in " ".join(row["evidence"])
    assert "stale_keys=daily-tracker" in " ".join(row["evidence"])
    assert "upstream freshness" in row["required_action"]


def test_silverbulletin_stale_200_is_rejected_before_archive_and_stays_red():
    """Production validator -> source orchestrator -> health/operator state."""
    raw, _block = provenance.current("sb_approval")
    rows = silverbulletin.parse(raw.decode("utf-8"))
    persisted = []

    def live_loader():
        validated = refresh.validate_silverbulletin_rows(
            "sb_approval", rows, today=datetime(2026, 10, 1).date())
        persisted.append("would-have-recorded")
        return validated, "new live vintage"

    def archive_loader():
        validated = refresh.validate_silverbulletin_rows(
            "sb_approval", rows, today=datetime(2026, 10, 1).date())
        return validated, "same-source committed archive"

    status = reliability.RunStatus(NOW)
    values, failures = refresh.run_source_tasks([
        ("sb_approval", silverbulletin.APPROVAL_URL,
         live_loader, archive_loader),
    ], status, next_deadline=DEADLINE, next_lock=LOCK)
    assert values == {} and len(failures) == 1
    assert persisted == [], "a stale but changing HTTP 200 body was archived"
    row = source_row(status.as_dict(), "sb_approval")
    assert row["state"] == "stale" and "newest observation" in row["last_error"]
    status.source_health({
        "source": "sb_approval", "state": "ok", "fetched_days": 0.1,
        "changed_days": 0.1, "budget_fetch_days": 2,
        "budget_change_days": 7,
    })
    after_health = source_row(status.as_dict(), "sb_approval")
    assert after_health["state"] == "stale" and after_health["last_error"]


def test_malformed_extraction_never_becomes_a_valid_vintage():
    status = reliability.RunStatus(NOW)
    persisted = []

    def malformed():
        # The loader contract is fetch -> validate -> persist.  Raising here
        # proves the invalid 200 body never reaches the manifest/archive step.
        raw = "<html>login page, still HTTP 200</html>"
        if "Reported Date" not in raw:
            raise RuntimeError(
                "malformed extraction: source structure changed; refusing to guess")
        persisted.append(raw)

    values, failures = refresh.run_source_tasks([
        ("survey", "https://survey.example", malformed),
    ], status, next_deadline=DEADLINE, next_lock=LOCK)
    row = source_row(status.as_dict(), "survey")
    assert values == {} and len(failures) == 1
    assert persisted == [], "malformed bytes replaced the last valid vintage"
    assert row["state"] == "unresolvable" and row["alert"]


def test_registry_derivation_failure_removes_only_its_source_series():
    saved = series_registry.SERIES
    series_registry.SERIES = {
        "good": {"source": "umich"},
        "bad": {"source": "aaii", "value": "spread"},
    }
    try:
        out, failures = series_registry.build_all({
            "umich": [{"date": "2026-08-01", "value": 55.0}],
            "aaii": [],
        }, isolate_failures=True)
    finally:
        series_registry.SERIES = saved
    assert out == {"good": [{"date": "2026-08-01", "value": 55.0}]}
    assert set(failures) == {"aaii"}
    assert "zero points" in str(failures["aaii"])


def test_main_registry_seam_marks_sce_archive_fallback_degraded():
    """A transport failure that the archive covers is degraded, never green.

    The exemplar used to be the Conference Board, withdrawn on rights grounds
    on 2026-09-03 (issue #68). The contract is the adapter's, not that source's:
    fetch what is there, serve the committed archive when it is not, and say so.
    The Civiqs test below walks the same seam through a different adapter --
    two adapters, because a seam that only one of them exercises is a seam that
    silently stops being checked when that one is removed.
    """
    saved_series = series_registry.SERIES
    saved_fetch = sce.fetch_bytes
    series_registry.SERIES = {
        "sce_1y": {"source": "sce", "sce": {"horizon": "1y"}},
    }
    sce.fetch_bytes = lambda *_a, **_k: (_ for _ in ()).throw(
        RuntimeError("NY Fed HTTP 403: runner blocked"))
    status = reliability.RunStatus(NOW)
    try:
        built, failures, source_failures, diagnostics = \
            refresh.build_and_account_registry_sources(
                [], [], [], {}, set(), status,
                next_deadline=DEADLINE, next_lock=LOCK)
    finally:
        series_registry.SERIES = saved_series
        sce.fetch_bytes = saved_fetch
    assert built["sce_1y"] and failures == {}
    assert source_failures == [] and diagnostics[0]["source"] == "sce"
    row = source_row(status.as_dict(), "sce")
    assert row["state"] == "stale" and row["alert"]
    assert "HTTP 403" in row["last_error"]


def test_main_registry_seam_marks_civiqs_403_archive_fallback_degraded():
    saved_series = series_registry.SERIES
    saved_get, saved_payloads = civiqs._get, civiqs._payloads
    saved_stale = civiqs._stale
    series_registry.SERIES = {
        "civiqs_net": {
            "source": "civiqs",
            "civiqs": {
                "name": "approve_president_trump_2025",
                "net": True, "weekday": 4,
            },
        },
    }
    civiqs._payloads = {}
    civiqs._get = lambda *_a, **_k: (_ for _ in ()).throw(
        RuntimeError("Civiqs HTTP 403: runner blocked"))
    # The archive doubles as the fetch cache, and `series.build_all` gives the
    # adapter the wall clock rather than this test's NOW. So on any day the
    # courier has already archived, `snapshot` serves today's file and returns
    # without a request -- the 403 never happens and the assertions below check
    # nothing. Declaring the cached vintage stale is what puts a live request
    # back in the path, which is the situation this test is about.
    civiqs._stale = lambda *_a, **_k: True
    status = reliability.RunStatus(NOW)
    try:
        built, failures, source_failures, diagnostics = \
            refresh.build_and_account_registry_sources(
                [], [], [], {}, set(), status,
                next_deadline=DEADLINE, next_lock=LOCK)
    finally:
        series_registry.SERIES = saved_series
        civiqs._get, civiqs._payloads = saved_get, saved_payloads
        civiqs._stale = saved_stale
    assert built["civiqs_net"] and failures == {}
    assert source_failures == [] and diagnostics[0]["source"] == "civiqs"
    row = source_row(status.as_dict(), "civiqs")
    assert row["state"] == "stale" and row["alert"]
    assert "HTTP 403" in row["last_error"]


def test_real_adapter_malformed_errors_are_terminal_at_the_registry_seam():
    actual_errors = [
        RuntimeError(
            f"{sce.URL} answered 31 bytes that are not an xlsx "
            "(starts b'<html>'); refusing the body"),
        RuntimeError(
            "the SCE body is not a readable xlsx (BadZipFile); refusing to "
            "parse whatever this is"),
        RuntimeError(
            "Google Trends explore response did not parse as JSON; the "
            "endpoint contract changed and this adapter needs revisiting"),
        RuntimeError("Google Trends explore response carries no widgets"),
        RuntimeError(
            "Google Trends returned an empty timeline; refusing to build an "
            "empty series"),
    ]
    for error in actual_errors:
        assert reliability.terminal_error(error, source=True), error

    # A body that arrived but is not the artifact we asked for is the case
    # that must never reach the archive fallback: the request succeeded, so a
    # transport-shaped retry would loop, and filing it would poison every later
    # archive-first read. The exemplar was the Conference Board until it was
    # withdrawn on rights grounds on 2026-09-03 (issue #68); here a 200 that is
    # a login page rather than the workbook.
    saved_series, saved_fetch = series_registry.SERIES, sce.fetch_bytes
    series_registry.SERIES = {
        "sce_1y": {"source": "sce", "sce": {"horizon": "1y"}},
    }
    sce.fetch_bytes = lambda *_a, **_k: b"<html><body>HTTP 200 login</body></html>"
    status = reliability.RunStatus(NOW)
    try:
        built, failures, source_failures, diagnostics = \
            refresh.build_and_account_registry_sources(
                [], [], [], {}, set(), status,
                next_deadline=DEADLINE, next_lock=LOCK)
    finally:
        series_registry.SERIES, sce.fetch_bytes = saved_series, saved_fetch
    assert built == {} and set(failures) == {"sce"}
    assert len(source_failures) == 1 and diagnostics == []
    row = source_row(status.as_dict(), "sce")
    assert row["state"] == "unresolvable"
    assert "not a readable xlsx" in row["last_error"]


def test_failed_generic_source_cannot_reappear_as_a_derived_margin():
    saved = series_registry.build_all
    series_registry.build_all = lambda *_args, **_kwargs: (
        {}, {"sb_generic": RuntimeError("generic derivation malformed")})
    try:
        out, failures = refresh.build_series(
            [], [{"date": datetime(2026, 8, 1).date(), "value": 1.0}], [],
            isolate_failures=True)
    finally:
        series_registry.build_all = saved
    assert set(failures) == {"sb_generic"}
    assert "generic_ballot_margin" not in out, \
        "a source rejected atomically leaked back through a side derivation"


def test_ranking_feed_failure_is_visible_and_holds_only_its_round():
    def ranking_definition(round_id, kind):
        common = {
            "round_id": round_id, "tracker": kind, "series": kind,
            "question": round_id, "unit": "ordered list",
            "release_at": "2026-09-15T00:00:00Z",
            "release_estimated": False, "lock_at": LOCK,
            "resolve": "source ranking", "target_type": "ranking_list",
        }
        if kind == "wiki_top10":
            common["ranking"] = {
                "kind": kind, "length": 10, "loss": "rbo", "rbo_p": 0.9,
                "week_start": "2026-09-07", "week_end": "2026-09-13",
                "exclusions": "main_page_and_namespaces_v1",
            }
        else:
            common["ranking"] = {
                "kind": kind, "length": 5, "loss": "kendall",
                "week_start": "2026-09-06", "week_end": "2026-09-12",
                "items": ["Tesla", "iPhone", "Samsung", "Netflix", "Disney"],
            }
        return common

    wiki = ranking_definition("wiki-r1", "wiki_top10")
    trends = ranking_definition("trends-r1", "trends_basket")
    season = {"rounds": [wiki, trends]}
    saved_observations = refresh.ranking_round.observations

    def observations(round_, fetch=False, diagnostics=None):
        if round_["round_id"] == "wiki-r1":
            raise RuntimeError("Wikimedia HTTP 403: runner blocked")
        return [{"date": "2026-09-05",
                 "items": ["Tesla", "iPhone", "Samsung", "Netflix", "Disney"]}]

    refresh.ranking_round.observations = observations
    status = reliability.RunStatus(NOW)
    try:
        ranking_obs, failures = refresh.load_ranking_sources(
            season, status, fetch=True, next_deadline=DEADLINE, next_lock=LOCK)
    finally:
        refresh.ranking_round.observations = saved_observations
    assert ranking_obs["wiki-r1"] == [] and ranking_obs["trends-r1"]
    assert len(failures) == 1 and failures[0][0] == "ranking_wikitop"
    doc = status.as_dict()
    assert source_row(doc, "ranking_wikitop")["state"] == "unresolvable"
    assert source_row(doc, "ranking_trends_basket")["state"] == "healthy"
    assert not any(r["source"] == "wikipedia" for r in doc["source_states"]), \
        "ranking wikitop was collapsed into the unrelated scalar pageview source"

    rounds, histories = refresh.build_rounds(
        season, {}, {}, NOW, ranking_obs=ranking_obs)
    calls = []

    def forecast(entrant, round_, **_kwargs):
        calls.append(round_["round_id"])
        return {"round_id": round_["round_id"], "entrant": entrant,
                "ranking": ["Tesla", "iPhone", "Samsung", "Netflix", "Disney"],
                "notes": "filed=2026-09-12T12:00Z, model, via=direct; in=abc"}

    roster = [("claude-opus", "claude-opus", "recent10", "direct")]
    with FilingPatch(forecast, roster, {"claude-opus": 0.1}) as root:
        _written, entrant_failures = refresh.file_baseline_forecasts(
            rounds, histories, NOW, {}, ranking_obs, run_status=status)
        assert not os.path.exists(os.path.join(root, "wiki-r1", "claude-opus.json"))
        assert os.path.exists(os.path.join(root, "trends-r1", "claude-opus.json"))
    assert entrant_failures == [] and calls == ["trends-r1"]
    final = status.as_dict()
    assert entrant_row(final, "wiki-r1", "claude-opus")["state"] == "queued"
    assert entrant_row(final, "trends-r1", "claude-opus")["state"] == "succeeded"


def test_wikitop_live_403_with_six_archived_weeks_is_degraded_not_green():
    round_ = {
        "round_id": "wiki-r1", "tracker": "wiki_top10",
        "series": "wiki_top10", "question": "top ten",
        "unit": "ordered list", "release_at": "2026-09-15T00:00:00Z",
        "release_estimated": False, "lock_at": LOCK,
        "resolve": "source ranking", "target_type": "ranking_list",
        "ranking": {
            "kind": "wiki_top10", "length": 10, "loss": "rbo",
            "rbo_p": 0.9, "week_start": "2026-09-07",
            "week_end": "2026-09-13",
            "exclusions": "main_page_and_namespaces_v1",
        },
    }
    saved = refresh.ranking_round.wikipedia_adapter.weekly_top
    calls = []

    def weekly_top(end, *_args, **_kwargs):
        calls.append(end)
        if len(calls) == 7:
            raise RuntimeError("Wikimedia HTTP 403: runner blocked")
        items = [f"Article_{i}" for i in range(10)]
        return items, {item: 100 - i for i, item in enumerate(items)}

    refresh.ranking_round.wikipedia_adapter.weekly_top = weekly_top
    status = reliability.RunStatus(NOW)
    try:
        observations, failures = refresh.load_ranking_sources(
            {"rounds": [round_]}, status, fetch=True,
            next_deadline=DEADLINE, next_lock=LOCK)
    finally:
        refresh.ranking_round.wikipedia_adapter.weekly_top = saved
    assert failures == [] and len(observations["wiki-r1"]) == 6
    row = source_row(status.as_dict(), "ranking_wikitop")
    assert row["state"] == "stale" and row["alert"]
    assert "HTTP 403" in row["last_error"]
    assert "same-source" in " ".join(row["evidence"])


def test_umich_composite_requires_hash_validated_finals_and_preliminary():
    finals = "Month,Year,Index\nJuly,2026,55.2\n"
    preliminary = "Month,Year,Index\nJuly,2026,55.2\nAugust (P),2026,51.0\n"
    raw = refresh.encode_umich_composite(finals, preliminary)
    assert refresh.parse_umich_composite(raw) == [
        {"date": "2026-07-01", "value": 55.2},
        {"date": "2026-08-01", "value": 51.0},
    ]

    missing = json.loads(raw)
    del missing["parts"]["preliminary"]
    try:
        refresh.parse_umich_composite(json.dumps(missing).encode())
        assert False, "a finals-only archive recreated the combined UMich series"
    except RuntimeError as error:
        assert "finals and preliminary" in str(error)

    with tempfile.TemporaryDirectory(prefix="ssa-umich-provenance-") as root:
        saved = provenance.ROOT, provenance.ARCHIVE, provenance.MANIFEST
        provenance.ROOT = root
        provenance.ARCHIVE = os.path.join(root, "sources")
        provenance.MANIFEST = os.path.join(provenance.ARCHIVE, "manifest.json")
        try:
            block = provenance.record(
                "umich", refresh.umich.URL, raw, ext="json",
                fetched_at="2026-09-12T12:00:00Z")
            with open(os.path.join(root, block["file"]), "ab") as handle:
                handle.write(b"tampered")
            try:
                provenance.current("umich", expected_url=refresh.umich.URL)
                assert False, "a tampered UMich composite passed its manifest"
            except RuntimeError as error:
                assert "hash mismatch" in str(error)
        finally:
            provenance.ROOT, provenance.ARCHIVE, provenance.MANIFEST = saved


def test_source_timeout_becomes_deadline_risk_when_the_cadence_is_too_slow():
    status = reliability.RunStatus(NOW)
    status.source_started("near-lock", next_deadline=NOW + timedelta(hours=2))
    status.source_failed(
        "near-lock", "Read timed out", next_deadline=NOW + timedelta(hours=2))
    row = source_row(status.as_dict(), "near-lock")
    assert row["state"] == "deadline_risk"
    assert row["next_retry"] == "2026-09-12T18:00:00Z"
    assert "retry now" in row["required_action"]


def test_provider_429_and_timeout_are_retryable_but_401_is_terminal():
    for entrant, error in (
            ("claude-opus", "HTTP 429: rate limit; retry after 20s"),
            ("grok", "Read timed out after 600 seconds")):
        status = reliability.RunStatus(NOW)
        queue(status, entrant)
        status.entrant_failed("r1", entrant, error)
        row = entrant_row(status.as_dict(), "r1", entrant)
        assert row["state"] == "retryable_failure", (entrant, row)
        assert row["next_retry"] == "2026-09-12T18:00:00Z"
        assert row["attempts"] == 1 and row["alert"]

    status = reliability.RunStatus(NOW)
    queue(status, "gpt-5.6-sol")
    status.entrant_failed("r1", "gpt-5.6-sol",
                          "HTTP 401: invalid_api_key")
    row = entrant_row(status.as_dict(), "r1", "gpt-5.6-sol")
    assert row["state"] == "terminal_failure"
    assert row["next_retry"] is None and "credentials" in row["required_action"]


def test_terminal_direct_failure_plus_standby_success_records_both_attempts():
    status = reliability.RunStatus(NOW)
    queue(status)
    status.entrant_succeeded(
        "r1", "claude-opus", route="openrouter",
        artifact="forecasts/r1/claude-opus.json",
        fallback_error="HTTP 401 on the configured direct route")
    row = entrant_row(status.as_dict(), "r1", "claude-opus")
    assert row["state"] == "succeeded" and row["attempts"] == 2
    assert row["route"] == "openrouter" and row["alert"]
    assert "standby route" in " ".join(row["evidence"])


class FilingPatch:
    def __init__(self, forecast, roster, costs, ceiling=10.0):
        self.forecast = forecast
        self.roster = roster
        self.costs = costs
        self.ceiling = ceiling

    def __enter__(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ssa-faults-")
        self.saved = (refresh.FORECASTS, refresh.season_roster,
                      refresh.harness.forecast, refresh.price_jobs,
                      refresh.MAX_SPEND, refresh.FILING_WORKERS)
        refresh.FORECASTS = self.temp.name
        refresh.season_roster = lambda: list(self.roster)
        refresh.harness.forecast = self.forecast

        def price(jobs, *_args, **_kwargs):
            priced = [(r, e, p, self.costs.get(e, 0.1)) for r, e, p in jobs]
            return priced, sum(j[3] for j in priced)

        refresh.price_jobs = price
        refresh.MAX_SPEND = self.ceiling
        refresh.FILING_WORKERS = 2
        return self.temp.name

    def __exit__(self, *args):
        (refresh.FORECASTS, refresh.season_roster,
         refresh.harness.forecast, refresh.price_jobs,
         refresh.MAX_SPEND, refresh.FILING_WORKERS) = self.saved
        self.temp.cleanup()


def scalar_round():
    return {
        "round_id": "r1", "series": "yougov_approval", "status": "open",
        "lock_at": LOCK, "release_at": "2026-09-15T00:00:00Z",
        "baselines": {"persistence": {"mean": 41.0, "sd": 1.5,
                                        "method": "last value"}},
    }


def test_partial_model_success_lands_and_the_failed_model_creates_no_artifact():
    def forecast(entrant, round_, **_kwargs):
        if entrant == "grok":
            raise RuntimeError("HTTP 429: rate limit")
        return {"round_id": round_["round_id"], "entrant": entrant,
                "topline": {"mean": 41.2, "sd": 1.4},
                "notes": "filed=2026-09-12T12:00Z, model, via=direct; in=abc"}

    roster = [("claude-opus", "claude-opus", "recent10", "direct"),
              ("grok", "grok", "recent10", "direct")]
    status = reliability.RunStatus(NOW)
    with FilingPatch(forecast, roster, {"claude-opus": 0.1, "grok": 0.1}) as root:
        written, failures = refresh.file_baseline_forecasts(
            [scalar_round()], {"r1": []}, NOW, run_status=status)
        assert os.path.exists(os.path.join(root, "r1", "claude-opus.json"))
        assert not os.path.exists(os.path.join(root, "r1", "grok.json"))
    doc = status.as_dict()
    assert written == 2, "one baseline plus one successful entrant"
    assert len(failures) == 1 and "grok" in failures[0]
    assert entrant_row(doc, "r1", "claude-opus")["state"] == "succeeded"
    assert entrant_row(doc, "r1", "grok")["state"] == "retryable_failure"


def test_aaii_live_failure_with_valid_archive_does_not_block_unrelated_forecast():
    """End-to-end isolation: source attempt -> archive -> entrant artifact."""
    status = reliability.RunStatus(NOW)

    def aaii_live():
        raise RuntimeError("AAII HTTP 403: Imperva refused this runner")

    def aaii_archive():
        return [{"date": "2026-09-10", "spread": 2.0}], \
            "same-source sources/aaii/2026-09-11.html sha256=abc"

    values, source_failures = refresh.run_source_tasks([
        ("aaii", "https://aaii.example", aaii_live, aaii_archive),
    ], status, next_deadline=DEADLINE, next_lock=LOCK)
    assert source_failures == [] and values["aaii"]

    def forecast(entrant, round_, **_kwargs):
        return {"round_id": round_["round_id"], "entrant": entrant,
                "topline": {"mean": 41.2, "sd": 1.4},
                "notes": "filed=2026-09-12T12:00Z, model, via=direct; in=abc"}

    roster = [("claude-opus", "claude-opus", "recent10", "direct")]
    with FilingPatch(forecast, roster, {"claude-opus": 0.1}) as root:
        _written, entrant_failures = refresh.file_baseline_forecasts(
            [scalar_round()], {"r1": []}, NOW, run_status=status)
        assert os.path.exists(os.path.join(root, "r1", "claude-opus.json")), \
            "an AAII outage blocked an unrelated YouGov entrant-round"
    assert entrant_failures == []
    doc = status.as_dict()
    assert source_row(doc, "aaii")["state"] == "stale"
    assert entrant_row(doc, "r1", "claude-opus")["state"] == "succeeded"


def test_aaii_without_archive_holds_only_aaii_and_files_yougov_end_to_end():
    """Source failure -> partial series -> round hold -> unrelated artifact."""
    status = reliability.RunStatus(NOW)
    sb_raw, _block = provenance.current("sb_approval")
    sb_rows = silverbulletin.parse(sb_raw.decode("utf-8"))

    def good_approval():
        return sb_rows, "same test fixture as Silver Bulletin source"

    def aaii_live():
        raise RuntimeError("AAII HTTP 403: Imperva refused this runner")

    def no_archive():
        raise RuntimeError("no validated AAII vintage exists")

    values, source_failures = refresh.run_source_tasks([
        ("sb_approval", "https://silver.example", good_approval),
        ("aaii", "https://aaii.example", aaii_live, no_archive),
    ], status, next_deadline=DEADLINE, next_lock=LOCK)
    assert set(values) == {"sb_approval"} and len(source_failures) == 1

    all_sources = {spec["source"] for spec in series_registry.SERIES.values()}
    unavailable = all_sources - {"sb_approval"}
    approval = silverbulletin.approval_polls(rows=values["sb_approval"])
    series, registry_failures = refresh.build_series(
        approval, [], [], {"sb_approval": values["sb_approval"]},
        unavailable_sources=unavailable, isolate_failures=True)
    assert registry_failures == {}
    assert "yougov_approval" in series
    assert "aaii_bull_bear_spread" not in series

    def round_for(round_id, series_id):
        return {
            "round_id": round_id, "tracker": series_id,
            "series": series_id, "question": round_id, "unit": "points",
            "release_at": "2026-09-15T00:00:00Z",
            "release_estimated": False, "lock_at": LOCK,
            "resolve": "next release",
        }

    season = {"rounds": [round_for("yougov-r1", "yougov_approval"),
                          round_for("aaii-r1", "aaii_bull_bear_spread")]}
    calls = []

    def forecast(entrant, round_, **_kwargs):
        calls.append(round_["round_id"])
        return {"round_id": round_["round_id"], "entrant": entrant,
                "topline": {"mean": 41.2, "sd": 1.4},
                "notes": "filed=2026-09-12T12:00Z, model, via=direct; in=abc"}

    roster = [("claude-opus", "claude-opus", "recent10", "direct")]
    with tempfile.TemporaryDirectory(prefix="ssa-partial-locks-") as lock_root:
        saved_locks = refresh.LOCKS
        refresh.LOCKS = lock_root
        try:
            rounds, histories = refresh.build_rounds(
                season, series, {}, NOW, ranking_obs={})
            with FilingPatch(forecast, roster, {"claude-opus": 0.1}) as root:
                written, entrant_failures = refresh.file_baseline_forecasts(
                    rounds, histories, NOW, series, {}, run_status=status)
                assert os.path.exists(os.path.join(
                    root, "yougov-r1", "claude-opus.json"))
                assert not os.path.exists(os.path.join(
                    root, "aaii-r1", "claude-opus.json"))
        finally:
            refresh.LOCKS = saved_locks
    assert written > 0 and entrant_failures == []
    assert calls == ["yougov-r1"], "the affected AAII round reached the provider"
    doc = status.as_dict()
    assert source_row(doc, "aaii")["state"] == "unresolvable"
    assert entrant_row(doc, "yougov-r1", "claude-opus")["state"] == "succeeded"
    held = entrant_row(doc, "aaii-r1", "claude-opus")
    assert held["state"] == "queued" and held["attempts"] == 0
    assert "validated source" in held["required_action"]


def test_budget_withholding_stays_queued_and_spends_nothing():
    def forecast(*_args, **_kwargs):
        raise AssertionError("a withheld job reached the provider")

    roster = [("claude-opus", "claude-opus", "recent10", "direct")]
    status = reliability.RunStatus(NOW)
    with FilingPatch(forecast, roster, {"claude-opus": 4.0}, ceiling=0.0) as root:
        written, failures = refresh.file_baseline_forecasts(
            [scalar_round()], {"r1": []}, NOW, run_status=status)
        assert not os.path.exists(os.path.join(root, "r1", "claude-opus.json"))
    row = entrant_row(status.as_dict(), "r1", "claude-opus")
    assert written == 1, "only the free persistence baseline is written"
    assert failures and "spend ceiling" in " ".join(failures)
    assert row["state"] == "queued" and row["attempts"] == 0
    assert row["estimated_spend"] == 4.0 and row["alert"]


def test_a_missing_answer_after_the_round_closed_is_missed_lock():
    # A round that closed with nothing filed for an entrant is a missed lock,
    # not a queued job. The status has to say so; `round.status` alone cannot.
    after_deadline = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)
    roster = [("claude-opus", "claude-opus", "recent10", "direct")]
    status = reliability.RunStatus(after_deadline)
    with FilingPatch(lambda *_a, **_k: None, roster, {}) as root:
        refresh.file_baseline_forecasts(
            [scalar_round()], {"r1": []}, after_deadline, run_status=status)
        assert not os.path.exists(os.path.join(root, "r1", "claude-opus.json"))
    row = entrant_row(status.as_dict(), "r1", "claude-opus")
    assert row["state"] == "missed_lock" and row["attempts"] == 0
    assert row["next_deadline"] == LOCK and "do not file late" in row["required_action"]


def test_a_labelled_mock_is_visible_locally_but_never_scored():
    round_ = dict(scalar_round(), status="resolved")
    resolved = {"r1": {"value": 42.0}}
    with tempfile.TemporaryDirectory(prefix="ssa-mock-score-") as root:
        saved = refresh.FORECASTS
        refresh.FORECASTS = root
        try:
            os.makedirs(os.path.join(root, "r1"))
            real = {"round_id": "r1", "entrant": "real",
                    "topline": {"mean": 41.0, "sd": 1.5},
                    "notes": "filed=2026-09-12T12:00Z, via=direct; in=abc"}
            mock = {"round_id": "r1", "entrant": "mock",
                    "topline": {"mean": 42.0, "sd": 0.1},
                    "notes": "MOCK: provider failed; in=def"}
            for body in (real, mock):
                with open(os.path.join(root, "r1", body["entrant"] + ".json"), "w") as f:
                    json.dump(body, f)
            board = refresh.build_leaderboard([round_], resolved)
            refresh.count_forecasts([round_])
        finally:
            refresh.FORECASTS = saved
    assert {r["entrant"] for r in board} == {"real"}
    assert round_["n_forecasts"] == 1 and set(round_["forecasts"]) == {"real"}


def historical_web_task():
    return {
        "entrant": "claude-opus-web", "series": "yougov_approval",
        "date": "2026-08-01", "outcome": 41.0, "prompt": "historical",
    }


def test_web_is_refused_by_plan_executor_cache_replay_and_scorer():
    series = {"yougov_approval": [
        {"date": f"2026-07-{day:02d}", "value": 40.0 + day / 10}
        for day in range(1, 12)]}
    calls = [
        lambda: model_backtest.plan(
            series, ["claude-opus-web"], "2026-07-01", warmup=1),
        lambda: model_backtest.run_task(historical_web_task(), use_cache=False),
        lambda: model_backtest.replay([historical_web_task()]),
        lambda: model_backtest.score([historical_web_task()], series, warmup=1),
    ]
    for call in calls:
        try:
            call()
            assert False, "a historical web path accepted a prospective-only row"
        except ValueError as error:
            assert "already published" in str(error), error


def populate_in_order(order):
    status = reliability.RunStatus(NOW)
    for name in order:
        status.source_started(name, route=f"https://{name}.example")
        status.source_succeeded(name, evidence=f"{name}.json")
    for entrant in order:
        status.entrant_queued(
            "r1", entrant, lock_at=LOCK, deadline=DEADLINE,
            route="direct", estimated_spend=0.1)
    return status


def test_operator_json_and_text_are_deterministic_under_concurrency_order():
    a = populate_in_order(["zeta", "alpha"])
    b = populate_in_order(["alpha", "zeta"])
    assert a.as_dict() == b.as_dict()
    assert a.summary() == b.summary()
    assert [r["source"] for r in a.as_dict()["source_states"]] == ["alpha", "zeta"]
    assert [r["entrant"] for r in a.as_dict()["entrant_round_states"]] == ["alpha", "zeta"]


def test_second_refresh_pass_carries_first_pass_entrant_failure():
    first = reliability.RunStatus(NOW)
    queue(first, "grok")
    first.entrant_failed("r1", "grok", "HTTP 429: rate limit")
    second = reliability.RunStatus(NOW + timedelta(minutes=5))
    second.carry_entrant_states(first.as_dict())
    second.source_succeeded("aaii", evidence="fresh.html")
    row = entrant_row(second.as_dict(), "r1", "grok")
    assert row["state"] == "retryable_failure" and row["attempts"] == 1
    assert row["last_error"] == "HTTP 429: rate limit"


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_")]
    for test in tests:
        test()
        print("ok", test.__name__)
    print(f"{len(tests)} reliability tests passed")
