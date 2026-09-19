"""The gate that stops a gateway id from silently changing what we score.

Run: PYTHONPATH=. python tests/test_ppapi_catalogue.py

`tools/check_ppapi_catalogue.py` compares each model id the season scores
against the sponsor gateway's own catalogue. One verdict matters more than the
others: a stem that matches with a different version is a *different model*
answering under an entrant's name, and nothing downstream can detect it --
the forecast validates, the score publishes, and the leaderboard row still
carries the old name. The others are conveniences. This one is the reason the
file exists, so it has a test.

No network: `classify` is pure and the catalogue is supplied.
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location(
    "check_ppapi_catalogue",
    os.path.join(ROOT, "tools", "check_ppapi_catalogue.py"))
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

from ssa import harness

# Shaped like the real thing: vendor-native ids, no vendor prefix, and two
# models offered at a version the season does not score.
CATALOGUE = [
    "claude-opus-5", "claude-fable-5.1", "deepseek-v4-pro", "glm-5.2",
    "gemini-3.1-pro-preview", "gemini-3.8-flash", "grok-4.6", "kimi-k3",
    "qwen-3.8-max", "gpt-6-astra", "gpt-5.6-sol", "MiniMax-M3",
]


def test_a_version_mismatch_is_refused_not_respelled():
    """The case worth the whole file. `grok-4.5` and `grok-4.6` share a stem,
    so any fuzzy match calls them the same model; they are not, and an entrant
    that changes model mid-season has scores incomparable with its own."""
    for ours, theirs in (("grok-4.5", "grok-4.6"),
                         ("gemini-3.6-flash", "gemini-3.8-flash")):
        verdict, candidate = check.classify(ours, CATALOGUE)
        assert verdict == "VERSION", (ours, verdict, candidate)
        assert candidate == theirs, (ours, candidate)


def test_an_exact_id_is_safe_to_route():
    for ours in ("claude-opus-5", "deepseek-v4-pro", "glm-5.2",
                 "gemini-3.1-pro-preview"):
        assert check.classify(ours, CATALOGUE) == ("exact", ours), ours


def test_a_respelling_is_offered_but_not_asserted():
    """The gateway writes ids its own way -- no vendor prefix, its own
    separators. Same model, so it is surfaced; a human still agrees to it,
    which is why the printed block comments these out."""
    assert check.classify("kimi/kimi-k3", CATALOGUE) == ("respelled", "kimi-k3")
    assert check.classify("qwen3.8-max", CATALOGUE) == ("respelled",
                                                        "qwen-3.8-max")
    assert check.classify("MiniMax/MiniMax-M3", CATALOGUE) == ("respelled",
                                                              "MiniMax-M3")


def test_absent_leaves_the_entrant_where_it_is():
    """No candidate is not an error. The entrant keeps its direct route, which
    is the behaviour an empty `PPAPI_MODELS` already gives everything."""
    for ours in ("gpt-5.6-terra", "gpt-5.6-luna", "claude-sonnet-5"):
        assert check.classify(ours, CATALOGUE) == ("absent", ""), ours


def test_every_model_the_season_scores_gets_a_verdict():
    """A model the tool skips is a model nobody checked, which is
    indistinguishable from one that was checked and refused."""
    for name, cfg in harness.MODELS.items():
        verdict, _ = check.classify(cfg["model"], CATALOGUE)
        assert verdict in ("exact", "respelled", "VERSION", "absent"), name


def test_the_version_reader_does_not_confuse_a_name_for_a_number():
    """`MiniMax-M3` and `gpt-6-astra` carry digits that are part of the name,
    not a version to compare. Splitting them wrongly would turn a respelling
    into a refusal and quietly keep a routable model off the gateway."""
    assert check.version_of("grok-4.5") == ("4", "5")
    assert check.version_of("kimi/kimi-k3") == ("3",)
    assert check.stem_of("grok-4.5") == check.stem_of("grok-4.6")
    assert check.stem_of("kimi/kimi-k3") == check.stem_of("kimi-k3")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
