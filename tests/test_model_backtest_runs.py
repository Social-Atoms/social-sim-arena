"""Tests for restoring committed model-backtest JSONL evidence."""
import json
from pathlib import Path
import tempfile

from ssa import harness, model_backtest


SHA_A = "a" * 64
SHA_B = "b" * 64


def record(entrant="grok", digest=SHA_A, mean=40.0, error=None):
    return {
        "entrant": entrant,
        "model": "grok-4.5",
        "series": "yougov_approval",
        "date": "2026-07-01",
        "outcome": 41.0,
        "prompt_sha256": digest,
        "topline": None if error else {"mean": mean, "sd": 1.0},
        "error": error,
        "raw": None if error else json.dumps({"mean": mean, "sd": 1.0}),
        "usage": None,
        "harness": "v1",
    }


def write_run(path, records):
    with open(path, "w") as f:
        for item in records:
            f.write(json.dumps(item) + "\n")


def test_later_run_wins_and_restores_failures():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        runs, cache = root / "runs", root / "cache"
        runs.mkdir()
        write_run(runs / "2026-08-01.jsonl", [
            record(error="RuntimeError: timeout"),
            record(entrant="glm", digest=SHA_B, error="RuntimeError: 429"),
        ])
        write_run(runs / "2026-08-02.jsonl", [record(mean=42.0)])

        counts = model_backtest.restore_runs(str(runs), str(cache))
        assert counts == {
            "files": 2,
            "lines": 3,
            "unique": 2,
            "superseded": 1,
            "restored": 2,
            "skipped_existing": 0,
            "successful": 1,
            "failures": 1,
        }
        restored = json.loads((cache / "grok" / f"{SHA_A}.json").read_text())
        assert restored["topline"]["mean"] == 42.0
        failed = json.loads((cache / "glm" / f"{SHA_B}.json").read_text())
        assert failed["topline"] is None


def test_existing_local_record_is_preserved_unless_overridden():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        runs, cache = root / "runs", root / "cache"
        runs.mkdir()
        write_run(runs / "2026-08-01.jsonl", [record(mean=42.0)])
        target = cache / "grok" / f"{SHA_A}.json"
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps(record(mean=99.0)))

        counts = model_backtest.restore_runs(str(runs), str(cache))
        assert counts["restored"] == 0
        assert counts["skipped_existing"] == 1
        assert json.loads(target.read_text())["topline"]["mean"] == 99.0

        counts = model_backtest.restore_runs(
            str(runs), str(cache), overwrite_existing=True)
        assert counts["restored"] == 1
        assert json.loads(target.read_text())["topline"]["mean"] == 42.0


def test_invalid_digest_is_rejected_without_writing_cache():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        runs, cache = root / "runs", root / "cache"
        runs.mkdir()
        write_run(runs / "bad.jsonl", [record(digest="../not-a-sha")])
        try:
            model_backtest.restore_runs(str(runs), str(cache))
            assert False, "invalid digest should fail"
        except ValueError as e:
            assert "invalid prompt_sha256" in str(e)
        assert not cache.exists()


def test_replies_bought_before_depth_entered_the_identity_are_still_found():
    """When the request parameters joined `harness.call_identity`, the cache
    key of every entrant that sent a reasoning depth moved with it -- 5,042 of
    the 8,828 replies committed under `backtest/runs/`.

    They cannot be re-keyed. A run record carries `prompt_sha256` and a usage
    report, not the prompt, so the new key is not derivable from the file.
    Recognising the old key is the only way to keep them, and dropping them
    would re-buy roughly $39 of identical calls on the next `--execute`.

    The depth is supplied here rather than read off `MODELS`. Every entrant
    stopped sending one on 2026-09-20, so a live lookup would leave this test
    comparing a key to itself and passing while testing nothing -- and the
    replies it guards are still on disk, still keyed the old way.
    """
    entrant, prompt = "grok", "does approval move this week?"
    model = harness.resolve(entrant)[0]
    saved_params = harness.MODELS[model].get("params")
    harness.MODELS[model]["params"] = {"reasoning_effort": "high"}
    try:
        current = model_backtest.cache_key(entrant, prompt)
        legacy = model_backtest.legacy_cache_key(entrant, prompt)
        assert current != legacy, \
            "the identity did not move; nothing to fall back to"

        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / entrant
            cache.mkdir(parents=True)
            (cache / (legacy + ".json")).write_text(json.dumps(record()))
            saved_dir = model_backtest.CACHE_DIR
            try:
                model_backtest.CACHE_DIR = td
                got = model_backtest.cache_read(entrant, prompt)
            finally:
                model_backtest.CACHE_DIR = saved_dir
    finally:
        if saved_params is None:
            harness.MODELS[model].pop("params", None)
        else:
            harness.MODELS[model]["params"] = saved_params
    assert got is not None, "a committed reply was dropped and would be re-bought"
    assert got["entrant"] == entrant


def test_an_entrant_that_sends_no_depth_is_not_looked_up_twice():
    """With no parameter block the legacy key *is* the current key, so the
    fallback must not turn one lookup into two stats of the same path. True
    for seven entrants when this was written and for every one of them since
    2026-09-20, which makes the early return the normal path rather than the
    exception."""
    entrant, prompt = "gemini-pro", "does approval move this week?"
    assert not (harness.MODELS[harness.resolve(entrant)[0]].get("params") or {})
    assert model_backtest.cache_key(entrant, prompt) == \
        model_backtest.legacy_cache_key(entrant, prompt)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
    print(f"all {len(tests)} model-backtest run tests passed")
