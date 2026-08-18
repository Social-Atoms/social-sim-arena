"""The two condition axes, and the entrant ids that name a cell of them.

Run: python tests/test_conditions.py    (no network)

A condition is a pair -- what the model was shown (context) and how it was
asked (elicitation) -- and the entrant id is how that pair is written down. The
id is load-bearing in three places at once: the forecast path
`forecasts/<round_id>/<entrant>.json`, the entrant record
`entrants/<entrant_id>.json`, and the leaderboard row. So the property these
tests exist to protect is that **separating the axes renamed nothing**.
"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa import harness


def test_the_two_axes_are_disjoint_and_have_defaults():
    assert set(harness.CONTEXT) == {"none", "recent10", "news", "web"}
    assert set(harness.ELICITATION) == {"direct", "superfc", "persona"}
    assert not set(harness.CONTEXT) & set(harness.ELICITATION), \
        "a name on both axes makes an entrant id ambiguous"
    assert harness.DEFAULT_CONTEXT == "recent10"
    assert harness.DEFAULT_ELICITATION == "direct"
    # Only the defaults are elided, or two different cells would share an id.
    assert harness.CONTEXT_SUFFIX["recent10"] == ""
    assert harness.ELICITATION_SUFFIX["direct"] == ""
    assert all(v for k, v in harness.CONTEXT_SUFFIX.items() if k != "recent10")
    assert all(v for k, v in harness.ELICITATION_SUFFIX.items() if k != "direct")


def test_every_id_already_on_disk_still_means_what_it_meant():
    """The one property worth a test of its own. Every forecast and entrant
    record written before the axes were separated must resolve to the same
    condition and rebuild to the same string, or the split silently orphans
    committed files."""
    ids = {os.path.basename(f)[:-5]
           for f in glob.glob("forecasts/*/*.json") + glob.glob("entrants/*.json")}
    assert len(ids) > 30, f"expected the season's ids, found {len(ids)}"
    seen = 0
    for i in sorted(ids):
        try:
            model, ctx, eli = harness.resolve(i)
        except KeyError:
            # Baselines (persistence, ewma, crowd, ...) are not models and were
            # always rejected here. Unchanged behaviour, not a new failure.
            assert i not in harness.MODELS, i
            continue
        assert harness.entrant_id(model, ctx, eli) == i, (i, model, ctx, eli)
        seen += 1
    assert seen > 30, f"only {seen} model ids checked"


def test_the_single_axis_names_resolve_to_the_pair_they_always_meant():
    cases = {
        "claude-opus": ("claude-opus", "recent10", "direct"),
        "claude-opus-zeroshot": ("claude-opus", "none", "direct"),
        "claude-opus-news": ("claude-opus", "news", "direct"),
        "claude-opus-web": ("claude-opus", "web", "direct"),
        "claude-opus-superfc": ("claude-opus", "recent10", "superfc"),
        "claude-opus-zeroshot-persona": ("claude-opus", "none", "persona"),
    }
    for i, want in cases.items():
        assert harness.resolve(i) == want, (i, harness.resolve(i))


def test_a_combination_is_nameable_now_and_was_not_before():
    """`news x superfc` is the cell the flat table could not express: the
    forecasting protocol applied to a model that has also read the news."""
    assert harness.entrant_id("claude-opus", "news", "superfc") \
        == "claude-opus-news-superfc"
    assert harness.resolve("claude-opus-news-superfc") \
        == ("claude-opus", "news", "superfc")
    # Both suffixes present, longest-first stripping, on a model whose own name
    # contains hyphens and a digit.
    assert harness.resolve("gpt-5.6-luna-zeroshot-persona") \
        == ("gpt-5.6-luna", "none", "persona")


def test_persona_can_only_carry_the_context_its_prompt_conveys():
    """`build_persona_prompt` takes a persona and the instrument and nothing
    else -- no series history, no release date. So recent10 x persona and
    none x persona build a byte-identical prompt, and offering both would put
    the same work on the leaderboard twice under different names."""
    assert harness.ELICITATION_CONTEXTS["persona"] == ("none",)
    assert harness.entrant_id("claude-opus", "none", "persona") \
        == "claude-opus-zeroshot-persona"
    for ctx in ("recent10", "news", "web"):
        try:
            harness.entrant_id("claude-opus", ctx, "persona")
            assert False, f"{ctx} x persona was accepted"
        except ValueError as e:
            assert "does not convey" in str(e), e
    # And an id this module cannot build is not an id it will read back.
    try:
        harness.resolve("claude-opus-persona")
        assert False, "the old single-suffix persona id still resolved"
    except KeyError:
        pass
    # A bare name in SSA_ELICITATION still works and now names the real cell.
    assert harness.cell("persona") == ("none", "persona")
    # superfc is unconstrained: the protocol block composes onto any context.
    assert harness.ELICITATION_CONTEXTS["superfc"] == tuple(harness.CONTEXT)


def test_unknown_ids_and_conditions_raise_rather_than_guess():
    for bad in ("claude-opus-nonsense", "not-a-model", "claude-opus-superfc-news"):
        try:
            harness.resolve(bad)
            assert False, f"{bad} resolved"
        except KeyError:
            pass
    for bad in ("nonsense", "news+web", "superfc+persona"):
        try:
            harness.cell(bad)
            assert False, f"cell({bad!r}) accepted"
        except ValueError:
            pass


def test_the_switch_spelling_survives_the_split():
    """SSA_ELICITATION was set before the axes existed. A bare name still pairs
    with the other axis's default, which is exactly what it used to mean."""
    assert harness.cell("news") == ("news", "direct")
    assert harness.cell("superfc") == ("recent10", "superfc")
    # persona pairs with the only context its prompt can carry, not with
    # the axis default -- see ELICITATION_CONTEXTS.
    assert harness.cell("persona") == ("none", "persona")
    assert harness.cell("news+superfc") == ("news", "superfc")
    assert harness.cell("superfc+news") == ("news", "superfc"), "order-free"


def test_the_prompt_composes_on_both_axes_independently():
    r = {"round_id": "r1", "series": "yougov_approval", "unit": "%",
         "question": "q", "release_at": "2026-08-20T14:00:00Z"}
    hist = [{"date": f"2026-08-{i+1:02d}", "value": 40.0 + i} for i in range(12)]
    news = {"asof": "2026-08-18T14:00:00Z", "text": "SOMETHING HAPPENED"}

    def has(ctx, eli):
        p = harness.build_prompt(r, hist, ctx, eli,
                                 news=news if ctx == "news" else None)
        return ("SOMETHING HAPPENED" in p, "Pre-mortem" in p)

    # The context decides the corpus, the elicitation decides the protocol, and
    # neither reaches across.
    assert has("recent10", "direct") == (False, False)
    assert has("recent10", "superfc") == (False, True)
    assert has("news", "direct") == (True, False)
    assert has("news", "superfc") == (True, True)


def test_only_the_context_can_leak_an_outcome():
    """Live search reads a published answer; a protocol cannot. So the
    backtest refusal keys on the context axis and lets every elicitation
    through."""
    for ok in ("none", "recent10", "news", "superfc", "persona", "direct"):
        harness.assert_prospective(ok)
    try:
        harness.assert_prospective("web")
        assert False, "the backtest must refuse live search"
    except ValueError as e:
        assert "already published" in str(e), e


def test_the_season_roster_is_unchanged_by_the_split():
    ids = [e for e, *_ in harness.season_entrants()]
    assert len(ids) == 2 * len(harness.active_models()), len(ids)
    assert set(ids) == {m for m in harness.active_models()} | \
        {m + "-zeroshot" for m in harness.active_models()}
    assert all(len(row) == 4 for row in harness.season_entrants()), \
        "a roster row carries both axes now"


def test_the_local_allowlist_narrows_the_roster_and_rejects_a_typo():
    """A local .env holds every provider's key, and OpenAI and Anthropic do not
    serve mainland China -- calling them from there is what disabled both
    accounts on 2026-08-14. Deleting keys works until someone pastes them back,
    so the constraint is explicit and beside them."""
    import os
    saved = os.environ.get("SSA_MODELS")
    try:
        os.environ.pop("SSA_MODELS", None)
        everything = harness.active_models()
        assert len(everything) > 5, everything

        os.environ["SSA_MODELS"] = "deepseek-pro, deepseek-flash"
        assert harness.active_models() == ["deepseek-pro", "deepseek-flash"]
        assert len(harness.season_entrants()) == 4, "two models, two season cells"

        os.environ["SSA_MODELS"] = "deepseek-pro,not-a-model"
        try:
            harness.active_models()
            assert False, "a typo narrowed the roster silently"
        except ValueError as e:
            assert "unknown model" in str(e), e
    finally:
        if saved is None:
            os.environ.pop("SSA_MODELS", None)
        else:
            os.environ["SSA_MODELS"] = saved


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
