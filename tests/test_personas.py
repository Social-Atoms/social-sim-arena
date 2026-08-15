"""Tests for the elicitation conditions: persona sampling, the forecasting
protocol, and the live-search guard. Plain asserts, no pytest, no network.

The persona arm is the one condition where the harness computes the number
rather than the model, so the arithmetic below is the actual estimator and is
checked against the pollsters' own definitions.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa import harness, personas, series as series_registry


def test_panel_is_deterministic_and_weights_sum_to_one():
    a, b = personas.panel(2), personas.panel(2)
    assert [p["id"] for p in a] == [p["id"] for p in b]
    assert [p["party"] for p in a] == [p["party"] for p in b]
    assert len(a) == personas.CELLS * 2
    assert abs(sum(p["weight"] for p in a) - 1.0) < 1e-9
    assert abs(sum(personas.weights_for("A", replicates=2).values()) - 1.0) < 1e-9


def test_registered_voters_are_reweighted_not_dropped():
    """Thinning an already small panel would cost more than it buys, and the
    RV/adult gap is worth several points -- it has to come from somewhere."""
    wa = personas.weights_for("A", replicates=1)
    wr = personas.weights_for("RV", replicates=1)
    assert set(wa) == set(wr), "no persona may disappear"
    pan = {p["id"]: p for p in personas.panel(1)}
    young = [i for i in wa if pan[i]["age"] == "18-29"
             and pan[i]["education"] == "no college degree"]
    old = [i for i in wa if pan[i]["age"] == "65+"
           and pan[i]["education"] == "college graduate"]
    # registration rises steeply with age and education
    assert sum(wr[i] for i in young) < sum(wa[i] for i in young)
    assert sum(wr[i] for i in old) > sum(wa[i] for i in old)


def test_approve_share_divides_by_everyone_asked():
    """Published approve and disapprove sum to about 96-97, so there is a real
    'not sure' residual. Dividing it away would inflate every simulated number
    by three or four points."""
    weights = {"a": 0.25, "b": 0.25, "c": 0.25, "d": 0.25}
    answers = {"a": {"approval": "approve"}, "b": {"approval": "approve"},
               "c": {"approval": "disapprove"}, "d": {"approval": "not sure"}}
    assert personas.approve_share(answers, weights) == 50.0
    # and it is weighted, not counted
    weights = {"a": 0.7, "b": 0.1, "c": 0.1, "d": 0.1}
    assert abs(personas.approve_share(answers, weights) - 80.0) < 1e-9


def test_party_margin_is_signed_and_over_everyone():
    weights = {f"p{i}": 0.2 for i in range(5)}
    answers = {"p0": {"vote": "Democrat"}, "p1": {"vote": "Democrat"},
               "p2": {"vote": "Republican"}, "p3": {"vote": "other"},
               "p4": {"vote": "not sure"}}
    assert abs(personas.party_margin(answers, weights) - 20.0) < 1e-9


def test_ics_reproduces_the_published_scale():
    """The index is a formula, not a question. Its fixed points are the check:
    a panel that answers 'same' to everything sits at the neutral value, and
    one that answers favourably to everything sits at the ceiling."""
    weights = {"a": 1.0}
    neutral = {"a": {"pago": "same", "pexp": "same", "bus12": "uncertain",
                     "bus5": "uncertain", "dur": "uncertain"}}
    assert abs(personas.umich_ics(neutral, weights) - 76.0) < 0.1

    best = {"a": {"pago": "better", "pexp": "better", "bus12": "good",
                  "bus5": "good", "dur": "good"}}
    assert abs(personas.umich_ics(best, weights) - 150.0) < 0.5

    worst = {"a": {"pago": "worse", "pexp": "worse", "bus12": "bad",
                   "bus5": "bad", "dur": "bad"}}
    assert abs(personas.umich_ics(worst, weights) - 2.0) < 0.5


def test_missing_answers_are_excluded_not_counted_as_no():
    weights = {"a": 0.5, "b": 0.5}
    answers = {"a": {"approval": "approve"}}          # b never answered
    assert personas.approve_share(answers, weights) == 100.0, \
        "a respondent who did not answer is not a disapproval"


def test_a_small_panel_reports_that_it_is_small():
    """Error falls as one over the square root of the panel, so the cost of
    resolution is the headline fact about this condition."""
    table = dict((k, se) for k, _, se in personas.resolution())
    assert table[1] > 8, "one respondent per cell cannot resolve a point"
    assert table[32] < table[1] / 4
    # sd must never come out confident
    sd = personas.sd_for(personas.weights_for("A", replicates=1),
                         [{"date": "d", "value": v} for v in range(30)])
    assert sd > 5, sd


def test_survey_instruments_cover_the_series_that_claim_them():
    for sid, spec in series_registry.SERIES.items():
        s = series_registry.survey(sid)
        if s is None:
            continue
        assert s["aggregate"] in personas.AGGREGATORS, sid
        assert s["items"], sid
        for item in s["items"]:
            assert item["key"] and item["text"] and len(item["options"]) >= 2
        if s["aggregate"] == "umich_ics":
            assert [i["key"] for i in s["items"]] == list(personas.ICS_ITEMS)


def test_persona_prompt_never_mentions_forecasting():
    """A respondent told it is feeding a prediction answers as an analyst in a
    costume, which is precisely the thing this condition is compared against."""
    spec = series_registry.survey("yougov_approval")
    p = harness.build_persona_prompt(personas.panel(1)[0], spec)
    low = p.lower()
    for word in ("forecast", "predict", "release", "tracker", "poll average",
                 "sd", "distribution"):
        assert word not in low, f"{word!r} leaked into the respondent prompt"
    assert "approve or disapprove" in low


def test_survey_replies_reject_answers_that_were_not_offered():
    spec = series_registry.survey("yougov_approval")
    assert harness.parse_survey_reply('{"approval": "Approve"}', spec) == \
        {"approval": "approve"}, "options match case-insensitively"
    for bad in ('{"approval": "strongly approve"}', '{"approval": 1}', '{}'):
        try:
            harness.parse_survey_reply(bad, spec)
            assert False, f"{bad} should have been rejected"
        except ValueError:
            pass


def test_web_search_is_refused_wherever_the_answer_is_already_published():
    """In a live round the answer does not exist at lock time. In the backtest
    it has been published for months, so search reads it."""
    harness.assert_prospective("recent10")       # fine
    harness.assert_prospective("persona")        # fine
    try:
        harness.assert_prospective("web")
        assert False, "the backtest must refuse the web condition"
    except ValueError as e:
        assert "already published" in str(e), e


def test_web_is_refused_for_models_whose_vendor_hosts_no_search():
    """Speaking the OpenAI protocol is not the same as serving OpenAI's tools.
    Five hosts here are OpenAI-compatible and have no hosted search; sending
    them the tool would 400, or worse, be ignored -- which would publish a
    'web' arm identical to its closed-book twin."""
    for m in ("grok", "qwen-3.7", "deepseek-pro", "glm"):
        assert m not in harness.WEB_CAPABLE, m
        assert harness.MODELS[m]["api"] == "openai", "the trap is protocol vs vendor"
    for m in ("gpt-5.6-terra", "claude-opus", "gemini-pro"):
        assert m in harness.WEB_CAPABLE, m

    # such a model never appears in a roster ...
    ids = [e for e, *_ in harness.elicitation_entrants(
        variants=("web",), models=harness.active_models())]
    assert "grok-web" not in ids and "glm-web" not in ids
    assert "claude-opus-web" in ids

    # ... and is refused by name if asked for directly
    import os
    os.environ.setdefault("DEEPSEEK_API_KEY", "x")
    try:
        harness.call_provider("deepseek-pro", "p", context="web")
        assert False, "must refuse"
    except ValueError as e:
        assert "vendor-hosted search" in str(e), e


def test_gemini_puts_tools_at_the_body_root():
    """Nested under generationConfig they are accepted and ignored, which would
    produce a 'web' condition that never searched."""
    sent = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.update(json)
        return _FakeJson({"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})

    real = harness.requests.post
    harness.requests.post = fake_post
    try:
        cfg = dict(harness.MODELS["gemini-pro"])
        cfg["params"] = dict(harness.WEB_TOOLS["gemini"], temperature=1)
        harness._call_gemini(cfg, "https://h", "k", "m", "p")
        assert "tools" in sent, "tools must be a sibling of generationConfig"
        assert "tools" not in sent.get("generationConfig", {})
        assert sent["generationConfig"] == {"temperature": 1}
    finally:
        harness.requests.post = real


class _FakeJson:
    def __init__(self, payload):
        self.status_code = 200
        self._p = payload
        self.text = ""

    def json(self):
        return self._p


def test_persona_forecast_runs_the_panel_and_aggregates_it():
    """End to end with a stubbed provider: every persona is asked, the answers
    are pooled by the pollster's rule, and the id carries the condition."""
    calls = []

    def fake_call(entrant, prompt, with_usage=False, context=None):
        calls.append(prompt)
        # Republicans approve, everyone else does not -- so the expected
        # topline is exactly the Republican share of the panel.
        return ('{"approval": "approve"}' if "Republican" in prompt
                else '{"approval": "disapprove"}')

    real_call, real_key = harness.call_provider, harness.has_key
    harness.call_provider = fake_call
    harness.has_key = lambda e: True
    try:
        r = {"round_id": "r1", "series": "yougov_approval",
             "release_at": "2026-08-20T14:00:00Z",
             "baselines": {"persistence": {"mean": 40.0, "sd": 2.0}}}
        hist = [{"date": f"2026-0{1+i%9}-01", "value": 40 + (i % 3)}
                for i in range(20)]
        out = harness.forecast_persona("claude-opus-persona", r, hist)
    finally:
        harness.call_provider, harness.has_key = real_call, real_key

    panel = personas.panel()
    assert len(calls) == len(panel), "every persona must be asked"
    expected = 100 * sum(p["weight"] for p in panel if p["party"] == "Republican")
    assert abs(out["topline"]["mean"] - expected) < 0.05, (out, expected)
    assert out["topline"]["sd"] > 0
    assert out["entrant"] == "claude-opus-persona"
    assert "elicitation=persona" in out["notes"] and "in=" in out["notes"]


def test_persona_forecast_refuses_a_panel_that_mostly_refused():
    """A model that will not play certain personas does not give a small panel,
    it gives a biased one -- publishing the survivors would hide that."""
    def fake_call(entrant, prompt, with_usage=False, context=None):
        if "Republican" in prompt:
            raise RuntimeError("declined")
        return '{"approval": "disapprove"}'

    real_call, real_key = harness.call_provider, harness.has_key
    harness.call_provider = fake_call
    harness.has_key = lambda e: True
    try:
        r = {"round_id": "r1", "series": "yougov_approval",
             "release_at": "2026-08-20T14:00:00Z",
             "baselines": {"persistence": {"mean": 40.0, "sd": 2.0}}}
        harness.forecast_persona("claude-opus-persona", r, [])
        assert False, "a third of the panel missing must not be published"
    except RuntimeError as e:
        assert "below the" in str(e), e
    finally:
        harness.call_provider, harness.has_key = real_call, real_key


def test_a_series_without_an_instrument_is_refused_not_invented():
    real_key = harness.has_key
    harness.has_key = lambda e: True
    try:
        r = {"round_id": "r1", "series": "house_seats",
             "release_at": "2026-11-03T14:00:00Z",
             "baselines": {"persistence": {"mean": 218.0, "sd": 10.0}}}
        harness.forecast_persona("claude-opus-persona", r, [])
        assert False, "no instrument means no honest question"
    except (RuntimeError, KeyError) as e:
        assert "survey" in str(e) or "house_seats" in str(e), e
    finally:
        harness.has_key = real_key


def test_superforecaster_protocol_is_added_without_changing_the_data():
    r = {"round_id": "r1", "series": "yougov_approval", "unit": "%",
         "question": "q", "release_at": "2026-08-20T14:00:00Z"}
    hist = [{"date": "2026-08-0%d" % (i + 1), "value": 40 + i} for i in range(9)]
    base = harness.build_prompt(r, hist, "recent10", "direct")
    # superfc is an *elicitation*, so it composes with any context rather than
    # replacing one. That it can now be asked for on top of `news` or `none`
    # too is the whole point of separating the axes.
    sfc = harness.build_prompt(r, hist, "recent10", "superfc")
    web = harness.build_prompt(r, hist, "web", "direct")
    assert "Outside view" in sfc and "Pre-mortem" in sfc
    assert "Outside view" not in base
    # every condition on this axis shows the same data, so a difference in
    # score is elicitation and not information
    for pt in hist:
        assert str(pt["value"]) in sfc and str(pt["value"]) in base
    assert web == base, "web differs in the request, not the prompt"


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all persona/elicitation tests passed")
