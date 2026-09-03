"""An example forecast is a template, and a template is checked too.

`forecasts/_*/` holds the file a new entrant copies. The validator used to
return on sight of one, printing "skipped lock check" while skipping every
check, so an example could name a round that does not exist, or answer a
profile round with a single number, and still be reported as OK.

Run: PYTHONPATH=. python tests/test_validate_submission.py
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Both rounds lock in the past, which is what an example names on purpose: a
# fixed round keeps the example from expiring every week.
SCALAR = {"round_id": "past-scalar-round", "tracker": "t", "series": "s",
          "question": "q", "unit": "u", "release_at": "2020-01-08T12:00:00Z",
          "lock_at": "2020-01-06T12:00:00Z", "resolve": "r",
          "target_type": "continuous_normal"}
PROFILE = dict(SCALAR, round_id="past-profile-round",
               target_type="profile_energy", cells=["cell_one", "cell_two"])

GOOD = {"round_id": SCALAR["round_id"], "entrant": "demo",
        "topline": {"mean": 40.0, "sd": 1.5}}


def load_validator():
    path = os.path.join(ROOT, "tools", "validate_submission.py")
    spec = importlib.util.spec_from_file_location(
        "validate_submission_example", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeRepo:
    """A throwaway repo root the validator can be pointed at."""

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="ssa-validate-example-")
        os.makedirs(os.path.join(self.dir, "questions"))
        os.makedirs(os.path.join(self.dir, "schema"))
        shutil.copy(os.path.join(ROOT, "schema", "forecast.schema.json"),
                    os.path.join(self.dir, "schema", "forecast.schema.json"))
        with open(os.path.join(self.dir, "questions", "season0.json"), "w") as f:
            json.dump({"season": 0, "rounds": [SCALAR, PROFILE]}, f)
        self.vs = load_validator()
        self.vs.ROOT = self.dir
        return self

    def __exit__(self, *a):
        shutil.rmtree(self.dir, ignore_errors=True)

    def check(self, round_dir, fname, body):
        """(ok, message). The validator exits rather than returning."""
        d = os.path.join(self.dir, "forecasts", round_dir)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, fname)
        with open(path, "w") as f:
            f.write(body if isinstance(body, str) else json.dumps(body))
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                self.vs.validate(path)
        except SystemExit:
            return False, buf.getvalue()
        return True, buf.getvalue()


def test_an_example_passes_the_checks_it_can_pass():
    with FakeRepo() as repo:
        ok, msg = repo.check("_example", "demo.json", GOOD)
        assert ok, msg
        assert "deadline not checked" in msg, msg
    print("ok test_an_example_passes_the_checks_it_can_pass")


def test_an_example_directory_no_longer_skips_the_checks_it_can_fail():
    """One case per check the bypass was hiding."""
    cases = (
        ("not valid JSON", "{ not json"),
        ("a distribution with no sd and no quantiles",
         dict(GOOD, topline={"mean": 40.0})),
        ("a round nobody published", dict(GOOD, round_id="no-such-round")),
        ("a topline answering a profile round",
         dict(GOOD, round_id=PROFILE["round_id"])),
    )
    with FakeRepo() as repo:
        for what, body in cases:
            ok, _ = repo.check("_example", "demo.json", body)
            assert not ok, f"an example carrying {what} was reported OK"
    print("ok test_an_example_directory_no_longer_skips_the_checks_it_can_fail")


def test_an_example_is_not_held_to_a_deadline():
    """And the deadline check still fires everywhere else -- without that half,
    the test above could be passing because the round is simply still open."""
    with FakeRepo() as repo:
        ok, _ = repo.check("_example", "demo.json", GOOD)
        assert ok
        late, msg = repo.check(SCALAR["round_id"], "demo.json", GOOD)
        assert not late and "late" in msg, msg
    print("ok test_an_example_is_not_held_to_a_deadline")


def test_a_missing_jsonschema_never_prints_a_bare_ok():
    """The degraded path is a convenience; a false pass is not.

    Without `jsonschema` the validator can only check a few required keys. It
    cannot see `additionalProperties: false`, a wrong type, or a bad pattern --
    so a file CI will reject reaches this code and finds nothing wrong. That is
    acceptable only while the participant is told. Printing plain `OK` hours
    before a batch deadline, for a file that is about to be rejected, is worse
    than the traceback the fallback exists to avoid.
    """
    # `_example` so the deadline never decides the outcome: what is under test
    # is the schema check, and a late-round refusal would pass this test while
    # proving nothing about it.
    extra = dict(GOOD, submitted_at="2020-01-01T00:00:00Z")
    with FakeRepo() as repo:
        ok, msg = repo.check("_example", "demo.json", extra)
        assert not ok, "the real schema should refuse an unknown field"
        assert "Additional properties" in msg or "additionalProperties" in msg

    with FakeRepo() as repo:
        real = repo.vs.__builtins__["__import__"] \
            if isinstance(repo.vs.__builtins__, dict) \
            else repo.vs.__builtins__.__import__

        def blocked(name, *a, **k):
            if name == "jsonschema" or name.startswith("jsonschema."):
                raise ImportError("blocked for the test")
            return real(name, *a, **k)

        if isinstance(repo.vs.__builtins__, dict):
            repo.vs.__builtins__["__import__"] = blocked
        else:
            repo.vs.__builtins__.__import__ = blocked
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ok, msg = repo.check("_example", "demo.json", extra)

    assert ok, "the fallback should still answer rather than crash"
    assert "OK" in msg, msg
    assert "SCHEMA NOT CHECKED" in msg, \
        f"a degraded check reported a plain OK: {msg!r}"
    warning = err.getvalue()
    assert "jsonschema" in warning and "NOT checked" in warning, warning
    assert "pip install" in warning, "the warning must say how to fix it"
    print("ok test_a_missing_jsonschema_never_prints_a_bare_ok")


if __name__ == "__main__":
    test_an_example_passes_the_checks_it_can_pass()
    test_an_example_directory_no_longer_skips_the_checks_it_can_fail()
    test_an_example_is_not_held_to_a_deadline()
    test_a_missing_jsonschema_never_prints_a_bare_ok()
    print("4 passed")
