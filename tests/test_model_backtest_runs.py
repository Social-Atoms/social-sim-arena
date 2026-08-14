"""Tests for restoring committed model-backtest JSONL evidence."""
import json
from pathlib import Path
import tempfile

from ssa import model_backtest


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


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
    print(f"all {len(tests)} model-backtest run tests passed")
