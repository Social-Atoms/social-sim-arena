"""The documented weekly build and mixed-shape sandbox cycle."""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(*args):
    return subprocess.run([sys.executable, *args], cwd=ROOT, text=True,
                          capture_output=True)


def test_documented_reviewed_batch_command_builds_identical_bytes():
    with tempfile.TemporaryDirectory(prefix="ssa-reviewed-batch-") as scratch:
        out = os.path.join(scratch, "batch.json")
        result = run("tools/make_bundle.py", "--batch", "batch-2026-09-14",
                     "--out", out)
        assert result.returncode == 0, result.stderr
        with open(out, "rb") as fh:
            built = fh.read()
    with open(os.path.join(ROOT, "questions", "bundles",
                           "batch-2026-09-14.json"), "rb") as fh:
        committed = fh.read()
    assert built == committed
    assert "OK reviewed manifest" in result.stderr
    assert "OK deterministic bundle" in result.stderr


def test_candidate_validation_checks_conflicts_with_the_reviewed_season():
    with open(os.path.join(ROOT, "questions", "season0.json")) as fh:
        source = json.load(fh)["rounds"][0]
    candidate = dict(source, round_id="duplicate-target-under-another-id")
    with tempfile.TemporaryDirectory(prefix="ssa-candidate-") as scratch:
        path = os.path.join(scratch, "candidate.json")
        with open(path, "w") as fh:
            json.dump([candidate], fh)
        result = run("tools/validate_season.py", path)
    assert result.returncode == 1
    assert "duplicate target" in result.stderr
    assert source["round_id"] in result.stderr


def test_generated_mixed_shape_batch_reaches_resolution_scoring_and_status():
    result = run("tools/run_sandbox_cycle.py", "--json")
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["bundle"]["shapes"] == [
        "continuous_normal", "profile_energy", "ranking_list"]
    assert summary["intake"]["accepted"] == 3
    assert summary["intake"]["rejected"] == 0
    assert summary["intake"]["forecast_blocks_preserved"] == 3
    assert summary["resolution"]["resolved"] == 3
    assert len(summary["resolution"]["methods"]) == 3
    assert all("production" in method or "same freeze" in method
               for method in summary["resolution"]["methods"].values())
    assert len(summary["scoring"]["records"]) == 3
    assert set(summary["status"]["rounds"].values()) == {"resolved"}


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_")]
    for test in tests:
        test()
        print("ok", test.__name__)
    print(f"{len(tests)} passed")
