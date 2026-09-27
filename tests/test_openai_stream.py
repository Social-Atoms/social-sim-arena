"""The OpenAI-protocol call is streamed; the web query turn has per-model settings.

Run: PYTHONPATH=. python tests/test_openai_stream.py

No network: `requests.post` is replaced by fakes that speak the wire format.

Why streamed: buffered reasoning replies put nothing on the connection for
minutes, and the gateway's calls were being cut at five to six minutes of
silence -- reported to us as "provider transport failure" while the gateway
logged them as completed and billed (2026-09-22..25). Why the query-turn
table: glm-5.2, asked for four search queries, reasoned to its 65,536-token
default ceiling and returned nothing; at reasoning_effort "low" with a cap it
answered 5 times in 6. See `QUERY_PARAMS` in ssa/harness.py.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa import harness, reliability


class _Stream:
    """A response in the gateway's shape: SSE lines, or one JSON body."""

    def __init__(self, events=None, status=200, body=None, ctype="text/event-stream"):
        self.status_code = status
        self.headers = {"Content-Type": "application/json" if body is not None else ctype}
        self._events = events or []
        self._body = body

    @property
    def text(self):
        return json.dumps(self._body) if self._body is not None else ""

    def json(self):
        return self._body

    def iter_lines(self, decode_unicode=False):
        for ev in self._events:
            yield "" if ev is None else ("data: " + (ev if isinstance(ev, str) else json.dumps(ev)))


def _chunk(content=None, reasoning=None, finish=None):
    delta = {}
    if content is not None:
        delta["content"] = content
    if reasoning is not None:
        delta["reasoning_content"] = reasoning
    return {"choices": [{"delta": delta, "finish_reason": finish}]}


def _with_post(fake, fn):
    real = harness.requests.post
    harness.requests.post = fake
    try:
        return fn()
    finally:
        harness.requests.post = real


def test_the_answer_is_assembled_from_content_deltas_only():
    sent = {}

    def post(url, headers=None, json=None, timeout=None, stream=False):
        sent.update(json=json, stream=stream)
        return _Stream([_chunk(reasoning="thinking about it..."), None,
                        _chunk(content='{"mean": 3'), _chunk(content='8.5, "sd": 1.5}'),
                        _chunk(finish="stop"),
                        {"choices": [], "usage": {"prompt_tokens": 528, "completion_tokens": 900,
                                                  "completion_tokens_details": {"reasoning_tokens": 700}}},
                        "[DONE]"])

    text, usage = _with_post(post, lambda: harness._call_openai({}, "https://gw/v1", "k", "glm-5.2", "p"))
    assert text == '{"mean": 38.5, "sd": 1.5}', text
    assert "thinking" not in text, "reasoning must never be taken as the answer"
    assert usage == {"input_tokens": 528, "output_tokens": 900, "thinking_tokens": 700}, usage
    assert sent["stream"] is True and sent["json"]["stream"] is True
    assert sent["json"]["stream_options"] == {"include_usage": True}
    # The model's own ceiling still applies: no limit is added by streaming.
    assert "max_tokens" not in sent["json"] and "max_completion_tokens" not in sent["json"]


def test_a_reply_that_hit_its_limit_while_reasoning_is_its_own_failure():
    """The 2026-09-25 glm case: 65,536 reasoning tokens, no content, 'length'."""
    post = lambda *a, **k: _Stream([_chunk(reasoning="x" * 50), _chunk(finish="length")])
    try:
        _with_post(post, lambda: harness._call_openai({}, "https://gw/v1", "k", "glm-5.2", "p"))
    except harness.OutputLimit as e:
        assert reliability.public_entrant_error(e) == "provider output limit", e
    else:
        raise AssertionError("an empty reply at finish_reason=length must raise OutputLimit")


def test_an_empty_reply_for_another_reason_is_an_ordinary_failure():
    post = lambda *a, **k: _Stream([_chunk(finish="stop")])
    try:
        _with_post(post, lambda: harness._call_openai({}, "https://gw/v1", "k", "m", "p"))
    except harness.OutputLimit:
        raise AssertionError("only finish_reason=length is an output limit")
    except RuntimeError as e:
        assert "no text" in str(e), e
    else:
        raise AssertionError("an empty reply must not pass as an answer")


def test_http_errors_keep_the_providers_reason():
    post = lambda *a, **k: _Stream(status=400, body={"error": {"message": "model not allowed"}})
    try:
        _with_post(post, lambda: harness._call_openai({}, "https://gw/v1", "k", "m", "p"))
    except RuntimeError as e:
        assert "HTTP 400" in str(e) and "model not allowed" in str(e), e
    else:
        raise AssertionError("an HTTP error must raise")


def test_the_max_tokens_rename_retry_still_works_streamed():
    calls = []

    def post(url, headers=None, json=None, timeout=None, stream=False):
        calls.append(dict(json))
        if len(calls) == 1:
            return _Stream(status=400, body={"error": "use max_completion_tokens instead"})
        return _Stream([_chunk(content="ok"), _chunk(finish="stop")])

    cfg = {"params": {"max_tokens": 100}}
    text, _ = _with_post(post, lambda: harness._call_openai(cfg, "https://gw/v1", "k", "m", "p"))
    assert text == "ok" and len(calls) == 2
    assert "max_completion_tokens" in calls[1] and "max_tokens" not in calls[1], calls[1]


def test_an_endpoint_that_ignores_stream_is_read_as_before():
    body = {"choices": [{"message": {"content": "buffered answer"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2}}
    post = lambda *a, **k: _Stream(body=body)
    text, usage = _with_post(post, lambda: harness._call_openai({}, "https://gw/v1", "k", "m", "p"))
    assert text == "buffered answer" and usage["output_tokens"] == 2, (text, usage)


def test_a_stream_error_event_raises():
    post = lambda *a, **k: _Stream([_chunk(content="par"), {"error": {"message": "upstream overloaded"}}])
    try:
        _with_post(post, lambda: harness._call_openai({}, "https://gw/v1", "k", "m", "p"))
    except RuntimeError as e:
        assert "upstream overloaded" in str(e), e
    else:
        raise AssertionError("an error event must not be filed as a partial answer")


def _retrieve_with(entrant, replies):
    """Run `_retrieve` with the provider and the search index faked; return the
    params each query call carried."""
    from ssa.adapters import search as sa
    seen = []
    saved = (harness._provider_text, sa.for_round, sa.gather, sa.record_round)
    frozen = {}

    def provider(ent, prompt, params=None):
        seen.append(params)
        nxt = replies.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    harness._provider_text = provider
    sa.for_round = lambda rid, ent: frozen.get((rid, ent))
    sa.gather = lambda queries: [{"query": q, "results": []} for q in queries]
    sa.record_round = lambda rid, ent, q, rec: frozen.__setitem__((rid, ent), {"queries": q})
    r = {"round_id": "r1", "series": "s", "lock_at": "2026-10-01T14:00:00Z",
         "release_at": "2026-10-03T14:00:00Z", "question": "q", "unit": "%"}
    real_build = harness.build_query_prompt
    harness.build_query_prompt = lambda *a, **k: "PROMPT"
    try:
        out = harness._retrieve(entrant, r, [{"date": "2026-09-01", "value": 1.0}])
    finally:
        harness._provider_text, sa.for_round, sa.gather, sa.record_round = saved
        harness.build_query_prompt = real_build
    return out, seen


def test_glm_query_turn_is_capped_and_retried_once():
    good = '{"queries": ["iPhone 18 release date"]}'
    out, seen = _retrieve_with("glm-web-superfc", [harness.OutputLimit("hit cap"), good])
    assert out == {"queries": ["iPhone 18 release date"]}, out
    assert seen == [harness.QUERY_PARAMS["glm"]] * 2, seen
    assert harness.QUERY_PARAMS["glm"] == {"reasoning_effort": "low", "max_tokens": 16000}


def test_glm_query_turn_gives_up_after_the_second_limit():
    try:
        _retrieve_with("glm-web", [harness.OutputLimit("1"), harness.OutputLimit("2"), "unused"])
    except harness.OutputLimit:
        pass
    else:
        raise AssertionError("two limits in a row must surface as a failure")


def test_other_models_query_turn_is_unchanged():
    good = '{"queries": ["tesla deliveries"]}'
    out, seen = _retrieve_with("grok-web", [good])
    assert seen == [None], "no extra params for a model without a query-turn entry"
    try:
        _retrieve_with("deepseek-pro-web", [harness.OutputLimit("x"), good])
    except harness.OutputLimit:
        pass
    else:
        raise AssertionError("models without an entry are not retried")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
