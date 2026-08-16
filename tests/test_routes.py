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
import sys

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


def test_readiness_asks_about_the_key_the_route_needs():
    """Reading the vendor's key would report ready for an entrant that cannot
    be called, and not ready for one that can."""
    saved = {k: os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "OPEN_ROUTER")}
    try:
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ["OPEN_ROUTER"] = "sk-or-test"
        with Routed(None):
            assert harness.has_key("claude-opus") is False
        with Routed("claude-opus"):
            assert harness.has_key("claude-opus") is True

        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
        os.environ.pop("OPEN_ROUTER", None)
        with Routed("claude-opus"):
            assert harness.has_key("claude-opus") is False, \
                "the vendor key does not open the gateway"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


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
    import ssa.harness as h
    saved_call, saved_key = h.call_provider, h.has_key
    os.environ.setdefault("OPEN_ROUTER", "sk-or-test")
    try:
        h.has_key = lambda e: True
        h.call_provider = lambda *a, **k: '{"mean": 41.0, "sd": 1.5}'
        r = {"round_id": "r1", "series": "yougov_approval", "unit": "%",
             "question": "q", "release_at": "2026-08-20T14:00:00Z",
             "baselines": {"persistence": {"mean": 41.0, "sd": 1.5}}}
        hist = [{"date": f"2026-08-{i+1:02d}", "value": 40.0 + i} for i in range(12)]
        with Routed("claude-opus"):
            f = h.forecast("claude-opus", r, hist)
        assert "via=openrouter" in f["notes"], f["notes"]
        assert "anthropic/claude-opus-4.8" in f["notes"], f["notes"]
        with Routed(None):
            f = h.forecast("claude-opus", r, hist)
        assert "via=direct" in f["notes"], f["notes"]
    finally:
        h.call_provider, h.has_key = saved_call, saved_key


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
