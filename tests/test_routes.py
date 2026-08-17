"""Where an entrant is actually reached. No network.

Run: PYTHONPATH=. python tests/test_routes.py

A route is the key, the wire protocol, the host and the model id on that host.
It exists because a vendor account can stop serving in a way no code change
fixes -- on 2026-08-14 the Anthropic organisation was disabled and the OpenAI
account ran out of credits, seven of fifteen entrants dead on every refresh.

The properties worth a test are all about *not lying*: the switch must be
explicit, the entrant id must not move, the endpoint must reach the cache key,
and a condition that depends on the vendor's own tooling must be refused rather
than quietly downgraded.
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa import harness


class Routed:
    """SSA_OPENROUTER set for the duration, restored after."""

    def __init__(self, value):
        self.value = value

    def __enter__(self):
        self.saved = os.environ.get("SSA_OPENROUTER")
        if self.value is None:
            os.environ.pop("SSA_OPENROUTER", None)
        else:
            os.environ["SSA_OPENROUTER"] = self.value
        return self

    def __exit__(self, *a):
        if self.saved is None:
            os.environ.pop("SSA_OPENROUTER", None)
        else:
            os.environ["SSA_OPENROUTER"] = self.saved


class Keys:
    """Exactly the named keys present, everything else cleared."""

    NAMES = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPEN_ROUTER")

    def __init__(self, **present):
        self.present = present

    def __enter__(self):
        self.saved = {k: os.environ.get(k) for k in self.NAMES}
        for k in self.NAMES:
            os.environ.pop(k, None)
        os.environ.update(self.present)
        return self

    def __exit__(self, *a):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


ROUND = {"round_id": "r1", "series": "yougov_approval", "unit": "%",
         "question": "q", "release_at": "2026-08-20T14:00:00Z",
         "baselines": {"persistence": {"mean": 41.0, "sd": 1.5}}}
HIST = [{"date": f"2026-08-{i+1:02d}", "value": 40.0 + i} for i in range(12)]
DISABLED = ('claude-opus-4-8 @ https://api.anthropic.com/v1 HTTP 400: '
            '{"error":{"message":"This organization has been disabled."}}')


class Provider:
    """Stands in for call_provider, recording which route each call took."""

    def __init__(self, fail_direct=None, fail_standby=None):
        self.fail_direct, self.fail_standby = fail_direct, fail_standby
        self.calls = []

    def __call__(self, entrant, prompt, with_usage=False, context=None, via=None):
        self.calls.append(via or harness.route(entrant)["via"])
        bad = self.fail_standby if self.calls[-1] == "openrouter" else self.fail_direct
        if bad:
            raise RuntimeError(bad)
        text = '{"mean": 41.0, "sd": 1.5}'
        usage = {"input_tokens": 900, "output_tokens": 40, "thinking_tokens": None}
        return (text, usage) if with_usage else text

    def __enter__(self):
        harness.forget_dead_routes()
        self.saved = harness.call_provider
        harness.call_provider = self
        # The live path writes every reply to `replies/` on receipt, so a test
        # that files a forecast files a reply too. Send them to a temp dir: a
        # test suite must not leave anything in the tree, and a fresh log per
        # block keeps these tests about the `in=` hash they are testing.
        self.log_dir = tempfile.mkdtemp(prefix="ssa-replies-")
        self.saved_log = os.environ.get("SSA_REPLIES_DIR")
        os.environ["SSA_REPLIES_DIR"] = self.log_dir
        return self

    def __exit__(self, *a):
        harness.call_provider = self.saved
        harness.forget_dead_routes()
        if self.saved_log is None:
            os.environ.pop("SSA_REPLIES_DIR", None)
        else:
            os.environ["SSA_REPLIES_DIR"] = self.saved_log
        shutil.rmtree(self.log_dir, ignore_errors=True)


def test_unset_means_every_entrant_stays_where_it_was():
    """Merging the route changes nothing until somebody sets the variable.
    Code that silently moves an endpoint on deploy is the failure this whole
    module is shaped to avoid."""
    with Routed(None):
        assert harness.openrouter_models() == frozenset()
        for m in harness.MODELS:
            r = harness.route(m)
            assert r["via"] == "direct", m
            assert r["env"] == harness.MODELS[m]["env"], m
            assert r["api"] == harness.MODELS[m]["api"], m
            assert r["model"] == harness.MODELS[m]["model"], m


def test_a_routed_entrant_changes_key_protocol_host_and_model_id():
    with Routed("claude-opus,gpt-5.6-sol"):
        r = harness.route("claude-opus")
        assert r["via"] == "openrouter"
        assert r["env"] == "OPEN_ROUTER"
        # The Messages API is not what OpenRouter serves; getting this wrong is
        # a 404 on every call, at lock time, on a round that does not wait.
        assert r["api"] == "openai", "Anthropic's own protocol is not portable"
        assert r["base"] == harness.OPENROUTER_BASE
        assert r["model"] == "anthropic/claude-opus-4.8"
        # and an entrant not named is untouched, in the same process
        assert harness.route("claude-fable")["via"] == "direct"
        assert harness.route("gpt-5.6-sol")["via"] == "openrouter"


def test_the_entrant_id_does_not_move_when_the_route_does():
    """The id is the forecast path, the entrant record and the leaderboard row.
    A model reached a different way is the same entrant, or every file it ever
    filed is orphaned."""
    with Routed("1"):
        for m in harness.OPENROUTER_MODELS:
            assert harness.entrant_id(m) == m, m
            assert harness.resolve(m)[0] == m, m
        assert len(harness.season_entrants()) == 2 * len(harness.active_models())


def test_the_endpoint_reaches_the_cache_key_so_a_reroute_re_runs():
    """`call_identity` is what both cache keys hash. Two hosts can serve
    different weights under one model name, and at different reasoning depth --
    reusing a reply the current endpoint never produced would put another
    model's forecast under this entrant's name."""
    with Routed(None):
        direct = harness.call_identity("claude-opus")
        direct_hash = harness.prompt_hash("claude-opus", "same prompt")
    with Routed("claude-opus"):
        via = harness.call_identity("claude-opus")
        via_hash = harness.prompt_hash("claude-opus", "same prompt")
    assert direct != via, (direct, via)
    assert "api.anthropic.com" in direct and "openrouter.ai" in via
    assert direct_hash != via_hash, "the same prompt cached across two hosts"


def test_readiness_means_reachable_by_some_route():
    """Reading only the vendor's key would report not-ready for an entrant the
    standby can serve, and ready for one whose route needs a key it lacks."""
    with Keys(OPEN_ROUTER="sk-or-test"):
        with Routed(None):
            # No Anthropic key, but the standby can reach it.
            assert harness.has_key("claude-opus") is True
        with Routed("claude-opus"):
            assert harness.has_key("claude-opus") is True

    with Keys(ANTHROPIC_API_KEY="sk-ant-test"):
        with Routed("claude-opus"):
            assert harness.has_key("claude-opus") is False, \
                "the vendor key does not open the gateway"

    with Keys():
        with Routed(None):
            assert harness.has_key("claude-opus") is False


def test_there_is_no_standby_without_a_key_or_for_an_already_routed_entrant():
    with Keys():
        assert harness.standby_route("claude-opus") is None, "no key, no standby"
    with Keys(OPEN_ROUTER="sk-or-test"):
        with Routed(None):
            assert harness.standby_route("claude-opus")["via"] == "openrouter"
            # Falling back from a host to itself is not a fallback.
        with Routed("claude-opus"):
            assert harness.standby_route("claude-opus") is None
        # A model with no OpenRouter entry has nowhere to fall back to.
        assert harness.standby_route("qwen-3.7") is None


def test_only_a_failure_that_survives_six_hours_moves_an_entrant():
    """A 500 or a read timeout is fixed by waiting, and switching endpoints on
    one would put two endpoints' forecasts in one season for reasons nobody
    recorded. A disabled organisation is not fixed by waiting."""
    terminal = [
        'claude-opus-4-8 @ https://api.anthropic.com/v1 HTTP 400: {"type":'
        '"error","error":{"type":"invalid_request_error","message":"This '
        'organization has been disabled."}}',
        'gpt-5.6-sol @ https://api.openai.com/v1 HTTP 429: {"error":{"message":'
        '"You have no credits remaining.","code":"credit_balance_exhausted"}}',
        'x @ y HTTP 401: {"error":{"code":"account_deactivated"}}',
        'x @ y HTTP 404: model does not exist or you do not have access',
    ]
    transient = [
        "x @ y HTTP 429: Rate limit reached for requests, retry in 20s",
        "x @ y HTTP 500: internal server error",
        "x @ y HTTP 529: overloaded_error",
        "HTTPSConnectionPool(host='api.anthropic.com'): Read timed out.",
        "RemoteDisconnected: Remote end closed connection without response",
    ]
    for t in terminal:
        assert harness.terminal_failure(RuntimeError(t)) is True, t
    for t in transient:
        assert harness.terminal_failure(RuntimeError(t)) is False, t


def test_a_dead_route_is_remembered_for_the_run_and_not_written_down():
    """One terminal failure marks the route, so the remaining entrants on it
    skip straight to the standby instead of each paying a failed request
    first -- 126 of them, on the season as it stands. Nothing is persisted, so
    the next run tests the vendor again."""
    harness.forget_dead_routes()
    with Keys(OPEN_ROUTER="sk-or-test"), Routed(None):
        rt = harness.route("claude-opus")
        assert harness.route_is_down(rt) is False
        harness.mark_route_down(rt, "disabled")
        assert harness.route_is_down(rt) is True
        # Same account and host -> same fingerprint, so one failure covers the
        # other three Claude entrants rather than costing four.
        assert harness.route_is_down(harness.route("claude-fable")) is True
        # A different vendor is untouched.
        assert harness.route_is_down(harness.route("gemini-pro")) is False
    harness.forget_dead_routes()
    assert harness.route_is_down(harness.route("claude-opus")) is False


def test_the_vendor_effort_block_is_replaced_not_carried_over():
    """`reasoning_effort: xhigh` and Anthropic's thinking/output_config pair are
    vendor request shapes. Sending either to OpenRouter is at best ignored and
    at worst a 400, and "ignored" is the bad one -- it would file a forecast at
    a reasoning depth nobody chose."""
    with Routed("claude-opus,gpt-5.6-sol"):
        for m in ("claude-opus", "gpt-5.6-sol"):
            p = harness.route(m)["params"]
            assert p == harness.OPENROUTER_EFFORT, (m, p)
            assert "thinking" not in p and "reasoning_effort" not in p, (m, p)


def test_hosted_search_belongs_to_the_route_not_to_the_model():
    """OpenRouter serves Claude and GPT but does not proxy Anthropic's
    `web_search_20260209` or OpenAI's hosted `web_search`. A host that accepts
    unknown fields and ignores them yields a "web" entrant identical to its
    closed-book twin -- a published comparison between two arms that were never
    different. So a routed model is refused by name."""
    os.environ.setdefault("OPEN_ROUTER", "sk-or-test")
    with Routed("claude-opus"):
        assert "claude-opus" in harness.WEB_CAPABLE, "still capable, direct"
        try:
            harness.call_provider("claude-opus", "p", context="web")
            assert False, "a routed model ran the web condition"
        except ValueError as e:
            assert "does not proxy" in str(e), e


def test_a_typo_raises_rather_than_routing_nothing():
    """A name that matches nothing would leave the outage in place with the
    variable set -- the run fails exactly as it did before, and the log says
    the fix was applied."""
    with Routed("claude-opuss"):
        try:
            harness.openrouter_models()
            assert False, "a typo routed nothing, silently"
        except ValueError as e:
            assert "claude-opuss" in str(e), e
    with Routed("1"):
        assert harness.openrouter_models() == frozenset(harness.OPENROUTER_MODELS)


def test_the_pinned_qwen_snapshot_is_deliberately_not_routable():
    """OpenRouter carries `qwen/qwen3.7-max`, the floating alias, not the dated
    snapshot this entrant is pinned to. An alias that rolls forward mid-season
    swaps the entrant, and scores from before and after are not comparable."""
    assert "qwen-3.7" not in harness.OPENROUTER_MODELS
    assert "qwen-3.8" not in harness.OPENROUTER_MODELS
    assert harness.MODELS["qwen-3.7"]["model"] == "qwen3.7-max-2026-05-20"
    with Routed("1"):
        assert harness.route("qwen-3.7")["via"] == "direct"


def test_every_routable_model_is_a_model_and_every_slug_is_vendor_qualified():
    for key, slug in harness.OPENROUTER_MODELS.items():
        assert key in harness.MODELS, key
        assert "/" in slug and not slug.startswith("/"), (key, slug)
        # `:batch` is a different product with its own latency contract, and
        # `~` prefixes a floating alias. Neither belongs in a pinned roster.
        assert ":" not in slug and not slug.startswith("~"), (key, slug)


def test_the_per_entrant_override_still_wins_over_the_route():
    """SSA_BASE_<ENTRANT> and SSA_MODEL_<ENTRANT> are the escape hatch for a
    self-hosted gateway; the route must not take it away."""
    saved = (os.environ.get("SSA_BASE_CLAUDE_OPUS"),
             os.environ.get("SSA_MODEL_CLAUDE_OPUS"))
    try:
        os.environ["SSA_BASE_CLAUDE_OPUS"] = "https://internal.example/v1"
        os.environ["SSA_MODEL_CLAUDE_OPUS"] = "house-opus"
        with Routed("claude-opus"):
            assert harness.base_url("claude-opus") == "https://internal.example/v1"
            assert harness.model_id("claude-opus") == "house-opus"
    finally:
        for k, v in zip(("SSA_BASE_CLAUDE_OPUS", "SSA_MODEL_CLAUDE_OPUS"), saved):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_the_route_is_written_into_the_forecast():
    """A file has to say which endpoint answered it. `via=` is the only field
    here that exists purely to be read by a human later."""
    with Keys(ANTHROPIC_API_KEY="k", OPEN_ROUTER="sk-or-test"):
        with Provider(), Routed("claude-opus"):
            f = harness.forecast("claude-opus", ROUND, HIST)
        assert "via=openrouter" in f["notes"], f["notes"]
        assert "anthropic/claude-opus-4.8" in f["notes"], f["notes"]
        with Provider(), Routed(None):
            f = harness.forecast("claude-opus", ROUND, HIST)
        assert "via=direct" in f["notes"], f["notes"]
        assert "claude-opus-4-8" in f["notes"], f["notes"]


def test_a_terminal_failure_falls_back_and_a_transient_one_does_not():
    with Keys(ANTHROPIC_API_KEY="k", OPEN_ROUTER="sk-or-test"), Routed(None):
        with Provider(fail_direct=DISABLED) as p:
            f = harness.forecast("claude-opus", ROUND, HIST)
        assert p.calls == ["direct", "openrouter"], p.calls
        assert "via=openrouter" in f["notes"], f["notes"]

        with Provider(fail_direct="x @ y HTTP 503: upstream unavailable") as p:
            try:
                harness.forecast("claude-opus", ROUND, HIST)
                assert False, "a transient failure moved the entrant"
            except RuntimeError as e:
                assert "503" in str(e), e
        assert p.calls == ["direct"], "the standby was billed for a blip"


def test_a_fallback_forecast_carries_the_standbys_hash_so_it_upgrades_itself():
    """The reverse -- storing the direct hash -- would pin the entrant to the
    standby for the rest of the season. This way, the run after the account is
    fixed misses, re-asks the vendor, and the entrant is upgraded without
    anyone having to notice it had been demoted."""
    with Keys(ANTHROPIC_API_KEY="k", OPEN_ROUTER="sk-or-test"), Routed(None):
        with Provider(fail_direct=DISABLED):
            filed = harness.forecast("claude-opus", ROUND, HIST)
        fb_hash = harness.prompt_hash(
            "claude-opus",
            harness.build_prompt(ROUND, HIST, "recent10", "direct"),
            via="openrouter")
        assert f"in={fb_hash}" in filed["notes"], filed["notes"]

        # The account comes back: the direct hash does not match, so the vendor
        # is asked again and the entrant returns to its own route.
        with Provider() as p:
            again = harness.forecast("claude-opus", ROUND, HIST, previous=filed)
        assert p.calls == ["direct"], p.calls
        assert "via=direct" in again["notes"], again["notes"]


def test_the_standby_is_not_billed_twice_for_a_prompt_it_already_answered():
    """A fallback forecast never matches the direct hash, so without checking
    the standby's own hash every six-hourly run would re-buy an answer to a
    prompt that had not changed."""
    with Keys(ANTHROPIC_API_KEY="k", OPEN_ROUTER="sk-or-test"), Routed(None):
        with Provider(fail_direct=DISABLED):
            filed = harness.forecast("claude-opus", ROUND, HIST)
        with Provider(fail_direct=DISABLED) as p:
            again = harness.forecast("claude-opus", ROUND, HIST, previous=filed)
        assert again == filed, "the standby answered the same prompt twice"
        assert p.calls == ["direct"], \
            f"expected one failed vendor probe and no purchase, got {p.calls}"


def test_one_dead_account_costs_one_failed_probe_not_one_per_entrant():
    with Keys(ANTHROPIC_API_KEY="k", OPEN_ROUTER="sk-or-test"), Routed(None):
        with Provider(fail_direct=DISABLED) as p:
            for e in ("claude-opus", "claude-opus-5", "claude-sonnet",
                      "claude-fable"):
                harness.forecast(e, ROUND, HIST)
        assert p.calls.count("direct") == 1, p.calls
        assert p.calls.count("openrouter") == 4, p.calls


def test_both_routes_down_names_both_and_files_nothing():
    with Keys(ANTHROPIC_API_KEY="k", OPEN_ROUTER="sk-or-test"), Routed(None):
        with Provider(fail_direct=DISABLED, fail_standby="HTTP 402: no credit"):
            try:
                harness.forecast("claude-opus", ROUND, HIST)
                assert False, "a forecast was filed with both routes down"
            except RuntimeError as e:
                assert "disabled" in str(e) and "402" in str(e), e


def test_a_model_with_no_standby_still_raises_the_providers_own_error():
    with Keys(OPEN_ROUTER="sk-or-test"), Routed(None):
        os.environ["DASHSCOPE_API_KEY"] = "k"
        with Provider(fail_direct="x @ y HTTP 401: bad key") as p:
            try:
                harness.forecast("qwen-3.7", ROUND, HIST)
                assert False, "qwen fell back somewhere"
            except RuntimeError as e:
                assert "401" in str(e), e
        assert p.calls == ["direct"], p.calls


def test_a_missing_vendor_key_goes_straight_to_the_standby():
    """A key that is not in the environment will not appear halfway through the
    run, and reaching the provider to be told so costs a request."""
    with Keys(OPEN_ROUTER="sk-or-test"), Routed(None):
        with Provider() as p:
            f = harness.forecast("claude-opus", ROUND, HIST)
        assert p.calls == ["openrouter"], p.calls
        assert "via=openrouter" in f["notes"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
