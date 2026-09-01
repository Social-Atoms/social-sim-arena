"""Training-cutoff evidence is policy, not decorative metadata.

Run: PYTHONPATH=. python tests/test_cutoffs.py
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa import cutoffs, harness


def test_default_preserves_every_pre_contract_dated_row():
    dated = [entrant for entrant, row in cutoffs.CUTOFFS.items()
             if row.get("date")]
    kept, dropped = cutoffs.partition(dated)
    assert kept == dated
    assert dropped == []
    assert cutoffs.usable_start("gpt-5.6-sol") == "2026-03-18"
    assert cutoffs.usable_start("gemini-pro") == "2026-03-15"
    assert cutoffs.usable_start("qwen-3.8") == "2026-07-01"


def test_default_backtest_roster_and_call_plan_are_unchanged():
    entrant_arms = [entrant for entrant, *_ in harness.season_entrants()]
    models = {harness.resolve(entrant)[0] for entrant in entrant_arms}
    kept, dropped = cutoffs.partition(sorted(models))
    assert set(kept) == models
    assert dropped == []


def test_raising_the_evidence_bar_is_explicit_and_ordered():
    assert cutoffs.usable_start(
        "gpt-5.6-sol", minimum_confidence="declared") == "2026-03-18"
    assert cutoffs.usable_start(
        "gemini-pro", minimum_confidence="declared") is None
    assert cutoffs.usable_start(
        "qwen-3.8", minimum_confidence="reported") is None
    assert cutoffs.usable_start(
        "qwen-3.8", minimum_confidence="unknown") == "2026-07-01"


def test_partition_and_common_window_enforce_the_same_policy():
    keep, dropped = cutoffs.partition(
        ["gpt-5.6-sol", "gemini-pro"], minimum_confidence="declared")
    assert keep == ["gpt-5.6-sol"]
    assert dropped == ["gemini-pro"]
    try:
        cutoffs.common_start(["gpt-5.6-sol", "gemini-pro"],
                             minimum_confidence="declared")
    except ValueError as e:
        assert "no cutoff" in str(e)
        assert "minimum confidence 'declared'" in str(e)
    else:
        raise AssertionError("a below-policy cutoff entered the common window")
    assert cutoffs.common_start(
        ["gpt-5.6-sol", "gemini-pro"],
        minimum_confidence="reported") == "2026-03-18"


def test_contract_records_the_decision_and_both_arms_share_it():
    model = harness.resolve("gemini-pro-zeroshot")[0]
    assert model == "gemini-pro"
    got = cutoffs.contract(model, minimum_confidence="declared")
    assert got == {
        "date": "2026-02-13",
        "confidence": "reported",
        "source": "Gemini 3.1 Pro, February 13 2026",
        "minimum_confidence": "declared",
        "trusted": False,
        "usable_from": None,
    }


def test_bad_policy_or_misspelled_row_fails_loud():
    try:
        cutoffs.cutoff("gpt-5.6-sol", "certain")
    except ValueError as e:
        assert "unknown cutoff confidence" in str(e)
    else:
        raise AssertionError("unknown evidence policy was silently accepted")


def test_cli_start_override_does_not_bypass_confidence_policy():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.run([
        sys.executable, "tools/run_model_backtest.py",
        "--entrants", "gemini-pro", "--start", "2026-01-01",
        "--minimum-cutoff-confidence", "declared",
    ], cwd=root, capture_output=True, text=True)
    assert proc.returncode != 0
    assert "cutoff evidence below policy for: gemini-pro" in (
        proc.stdout + proc.stderr)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
