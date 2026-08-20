"""The sixteen-cell profile round, end to end. Plain asserts, no pytest, no network.

Run: PYTHONPATH=. python tests/test_profile_round.py

`tests/test_profile_scoring.py` holds the scoring maths. This file holds the
wiring around it -- the schema, the validator, the harness parse, the freeze,
the resolution and the board -- which is where a joint round can silently
degrade into sixteen unrelated numbers without any single module looking wrong.

Every provider call is faked and every write goes to a temp directory: the
assertion that matters in half of these is "zero calls", and the other half
would otherwise write into the repository's own forecasts/ and replies/.
"""
import glob
import importlib.util
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa import harness, profile_round, refresh, scoring

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CELLS = list(profile_round.CELLS)

ROUND = {
    "round_id": "civiqs-profile-test",
    "tracker": "civiqs",
    "series": "civiqs_net_approval",
    "cells": CELLS,
    "question": "Civiqs Trump net approval, full sixteen-cell profile",
    "unit": "net points (approve minus disapprove)",
    "release_at": "2026-08-14T22:00:00Z",
    "release_estimated": True,
    "lock_at": "2026-08-12T22:00:00Z",
    "resolve": "dashboard Friday value per cell, from the daily archive",
    "target_type": "profile_energy",
}

# A distinct level per cell so a scorer that mixed two cells up cannot pass,
# and one point per day through the release so the freeze has something to cut.
LEVELS = {c: -60.0 + 8.0 * i for i, c in enumerate(CELLS)}


def series_fixture(last_day=14, drift=1.0):
    out = {}
    for c in CELLS:
        out[c] = [{"date": f"2026-08-{d:02d}", "value": LEVELS[c] + drift * d}
                  for d in range(1, last_day + 1)]
    out["civiqs_net_approval"] = [{"date": f"2026-08-{d:02d}", "value": -20.0 + d}
                                  for d in range(1, last_day + 1)]
    return out


def a_profile(offset=0.0, sd=4.0, cells=None):
    """A well-formed submission block, optionally biased off the truth."""
    return {c: {"mean": LEVELS[c] + 14.0 + offset, "sd": sd}
            for c in (cells or CELLS)}


def forecast_file(entrant, offset=0.0, sd=4.0):
    return {"round_id": ROUND["round_id"], "entrant": entrant,
            "profile": a_profile(offset, sd), "notes": "test"}


class Scratch:
    """Temp forecasts/, locks/ and reply log for the duration."""

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="ssa-profile-")
        self.saved = (refresh.FORECASTS, refresh.LOCKS,
                      os.environ.get("SSA_REPLIES_DIR"), harness.ALLOW_MOCK)
        refresh.FORECASTS = os.path.join(self.dir, "forecasts")
        refresh.LOCKS = os.path.join(self.dir, "locks")
        os.environ["SSA_REPLIES_DIR"] = os.path.join(self.dir, "replies")
        # A mock must never be able to turn a raised failure into a filed
        # placeholder here; half of these tests are about that failure.
        harness.ALLOW_MOCK = False
        return self

    def __exit__(self, *a):
        (refresh.FORECASTS, refresh.LOCKS, log, harness.ALLOW_MOCK) = self.saved
        if log is None:
            os.environ.pop("SSA_REPLIES_DIR", None)
        else:
            os.environ["SSA_REPLIES_DIR"] = log
        shutil.rmtree(self.dir, ignore_errors=True)

    def file(self, entrant, body):
        d = os.path.join(refresh.FORECASTS, ROUND["round_id"])
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, entrant + ".json"), "w") as f:
            json.dump(body, f)


class Keys:
    """Exactly the named keys present, everything else cleared."""

    NAMES = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPEN_ROUTER")

    def __init__(self, **present):
        self.present = present

    def __enter__(self):
        self.saved = {k: os.environ.get(k) for k in self.NAMES}
        for k in self.NAMES:
            os.environ.pop(k, None)
        os.environ.update(self.present)
        return self

    def __exit__(self, *a):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class Provider:
    """Stands in for call_provider and counts what it was asked."""

    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def __call__(self, entrant, prompt, with_usage=False, context=None, via=None):
        self.prompts.append(prompt)
        text = self.reply(prompt) if callable(self.reply) else self.reply
        usage = {"input_tokens": 900, "output_tokens": 200}
        return (text, usage) if with_usage else text

    def __enter__(self):
        harness.forget_dead_routes()
        self.saved = harness.call_provider
        harness.call_provider = self
        return self

    def __exit__(self, *a):
        harness.call_provider = self.saved
        harness.forget_dead_routes()


def good_reply(cells=None, offset=0.0):
    return json.dumps({c: {"mean": LEVELS[c] + 14.0 + offset, "sd": 4.0}
                       for c in (cells or CELLS)})


# --- the schema ------------------------------------------------------------

def load_schema():
    with open(os.path.join(ROOT, "schema", "forecast.schema.json")) as f:
        return json.load(f)


def accepts(doc):
    import jsonschema
    try:
        jsonschema.validate(doc, load_schema())
        return True
    except Exception:
        return False


def test_the_schema_takes_a_profile_and_still_takes_a_topline():
    base = {"round_id": "civiqs-profile-test", "entrant": "some-model"}
    assert accepts({**base, "profile": a_profile()})
    assert accepts({**base, "topline": {"mean": -24.0, "sd": 2.0}})
    # a cell may use either accepted distribution format
    q = a_profile()
    q[CELLS[0]] = {"quantiles": {"0.1": -40.0, "0.5": -35.0, "0.9": -30.0}}
    assert accepts({**base, "profile": q})


def test_the_schema_refuses_the_shapes_that_would_not_score():
    base = {"round_id": "civiqs-profile-test", "entrant": "some-model"}
    # exactly one answer: a submission carrying both is ambiguous about which
    # question it answers, and one carrying neither answers nothing
    assert not accepts({**base, "topline": {"mean": 1.0, "sd": 1.0},
                        "profile": a_profile()})
    assert not accepts(dict(base))
    # point forecasts are rejected by design, in a cell as in a topline
    for bad in ({"mean": -20.0}, {"mean": -20.0, "sd": 0}, {"mean": -20.0, "sd": -1}):
        p = a_profile()
        p[CELLS[0]] = bad
        assert not accepts({**base, "profile": p}), bad
    # and the schema is still closed
    assert not accepts({**base, "profile": a_profile(), "extra": 1})


# --- the validator ---------------------------------------------------------

def load_validator():
    path = os.path.join(ROOT, "tools", "validate_submission.py")
    spec = importlib.util.spec_from_file_location("validate_submission_t", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeRepo:
    """A throwaway repo root the validator can be pointed at.

    questions/season0.json is the real file's shape with two rounds -- one
    scalar, one profile -- and is never the repository's own, which this suite
    must not touch.
    """

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="ssa-validate-")
        os.makedirs(os.path.join(self.dir, "questions"))
        os.makedirs(os.path.join(self.dir, "schema"))
        shutil.copy(os.path.join(ROOT, "schema", "forecast.schema.json"),
                    os.path.join(self.dir, "schema", "forecast.schema.json"))
        scalar = {"round_id": "civiqs-scalar-test", "tracker": "civiqs",
                  "series": "civiqs_net_approval", "question": "q", "unit": "u",
                  "release_at": "2099-01-03T22:00:00Z", "release_estimated": True,
                  "lock_at": "2099-01-01T22:00:00Z", "resolve": "r",
                  "target_type": "continuous_normal"}
        prof = dict(ROUND, release_at="2099-01-03T22:00:00Z",
                    lock_at="2099-01-01T22:00:00Z")
        with open(os.path.join(self.dir, "questions", "season0.json"), "w") as f:
            json.dump({"season": 0, "rounds": [scalar, prof]}, f)
        self.vs = load_validator()
        self.vs.ROOT = self.dir
        return self

    def __exit__(self, *a):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, round_id, entrant, body):
        d = os.path.join(self.dir, "forecasts", round_id)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, entrant + ".json")
        with open(p, "w") as f:
            json.dump(body, f)
        return p

    def check(self, round_id, entrant, body):
        """(ok, message). The validator exits rather than returning."""
        p = self.write(round_id, entrant, body)
        import io
        import contextlib
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                self.vs.validate(p)
        except SystemExit:
            return False, buf.getvalue()
        return True, buf.getvalue()


def test_the_validator_pairs_each_answer_with_its_own_round_type():
    """A profile forecast passes on a profile round and a scalar one fails --
    and the mirror image. A single number is not a weak entry to a profile
    round, it is an answer to a different question."""
    with FakeRepo() as repo:
        ok, msg = repo.check("civiqs-profile-test", "good-model",
                             {"round_id": "civiqs-profile-test",
                              "entrant": "good-model", "profile": a_profile()})
        assert ok, msg

        ok, msg = repo.check("civiqs-profile-test", "scalar-model",
                             {"round_id": "civiqs-profile-test",
                              "entrant": "scalar-model",
                              "topline": {"mean": -24.0, "sd": 2.0}})
        assert not ok and "profile round" in msg, msg

        ok, msg = repo.check("civiqs-scalar-test", "good-scalar",
                             {"round_id": "civiqs-scalar-test",
                              "entrant": "good-scalar",
                              "topline": {"mean": -24.0, "sd": 2.0}})
        assert ok, msg

        ok, msg = repo.check("civiqs-scalar-test", "profile-model",
                             {"round_id": "civiqs-scalar-test",
                              "entrant": "profile-model", "profile": a_profile()})
        assert not ok and "scalar round" in msg, msg


def test_the_validator_refuses_a_profile_with_a_hole_or_an_invention():
    with FakeRepo() as repo:
        short = a_profile()
        dropped = short.pop(CELLS[7])
        ok, msg = repo.check("civiqs-profile-test", "short-model",
                             {"round_id": "civiqs-profile-test",
                              "entrant": "short-model", "profile": short})
        assert not ok and CELLS[7] in msg, msg

        invented = a_profile()
        invented["civiqs_net_approval_martians"] = dropped
        ok, msg = repo.check("civiqs-profile-test", "invent-model",
                             {"round_id": "civiqs-profile-test",
                              "entrant": "invent-model", "profile": invented})
        assert not ok and "did not ask for" in msg, msg


def test_the_validator_holds_a_profile_cell_to_the_quantile_rules():
    """The semantic rules a JSON schema cannot express apply to every cell, not
    only to a topline."""
    with FakeRepo() as repo:
        p = a_profile()
        p[CELLS[0]] = {"quantiles": {"0.1": -40.0, "0.9": -30.0}}   # no median
        ok, msg = repo.check("civiqs-profile-test", "nomedian",
                             {"round_id": "civiqs-profile-test",
                              "entrant": "nomedian", "profile": p})
        assert not ok and "median" in msg, msg

        p[CELLS[0]] = {"quantiles": {"0.1": -30.0, "0.5": -35.0, "0.9": -40.0}}
        ok, msg = repo.check("civiqs-profile-test", "backwards",
                             {"round_id": "civiqs-profile-test",
                              "entrant": "backwards", "profile": p})
        assert not ok and "non-decreasing" in msg, msg


# --- the harness parse -----------------------------------------------------

def test_a_complete_reply_parses_and_a_partial_one_never_does():
    got = harness.parse_profile(good_reply(), CELLS)
    assert len(got) == 16
    assert got[CELLS[0]] == {"mean": round(LEVELS[CELLS[0]] + 14.0, 2), "sd": 4.0}

    # fifteen of sixteen: rejected whole, and the missing cell is named
    fifteen = json.dumps({k: v for k, v in json.loads(good_reply()).items()
                          if k != CELLS[3]})
    try:
        harness.parse_profile(fifteen, CELLS)
    except ValueError as e:
        assert CELLS[3] in str(e) and "missing" in str(e), str(e)
    else:
        raise AssertionError("a fifteen-cell reply must not parse")


def test_junk_and_invention_are_refused_loudly():
    bad = [
        "I'm afraid I can't help with that.",
        "",
        "{",
        '{"mean": -24.0, "sd": 2.0}',                       # a scalar answer
        json.dumps({c: -20.0 for c in CELLS}),              # bare numbers
        json.dumps({c: {"mean": -20.0} for c in CELLS}),    # no sd
        json.dumps({c: {"mean": -20.0, "sd": 0} for c in CELLS}),
        # beyond even the count-scale garbage bound; a merely-wide sd like 99
        # is legal now and CRPS punishes it, see harness._distribution
        json.dumps({c: {"mean": -20.0, "sd": 2e7} for c in CELLS}),
    ]
    for text in bad:
        try:
            harness.parse_profile(text, CELLS)
        except ValueError:
            pass
        else:
            raise AssertionError(f"junk parsed: {text[:60]}")

    # a cell nobody asked for taints the whole reply
    extra = json.loads(good_reply())
    extra["civiqs_net_approval_martians"] = {"mean": 1.0, "sd": 1.0}
    try:
        harness.parse_profile(json.dumps(extra), CELLS)
    except ValueError as e:
        assert "not asked for" in str(e), str(e)
    else:
        raise AssertionError("an invented cell must not parse")


def test_prose_around_a_nested_object_still_parses():
    """The scalar parser's regex cannot see a nested object; the profile parser
    must, without loosening the scalar one."""
    text = "Here is my profile.\n```json\n" + good_reply() + "\n```\nHope it helps."
    assert len(harness.parse_profile(text, CELLS)) == 16
    # and the scalar parser is untouched: it still reads a flat object
    assert harness.parse_forecast('{"mean": 41.5, "sd": 2}') == {"mean": 41.5, "sd": 2.0}


# --- the harness call ------------------------------------------------------

def test_one_call_buys_all_sixteen_cells_and_the_hash_stops_the_second():
    """One call per entrant per round, and the input-hash guard is the scalar
    path's, unchanged: a refresh that changes nothing must buy nothing."""
    with Scratch(), Keys(ANTHROPIC_API_KEY="k"), Provider(good_reply()) as prov:
        hist = profile_round.frozen_history(ROUND, series_fixture())
        body = harness.forecast("claude-opus", ROUND, profile_history=hist)
        assert len(prov.prompts) == 1, "a profile round is one call, not sixteen"
        assert set(body["profile"]) == set(CELLS)
        assert "profile" in body and "topline" not in body
        assert "in=" in body["notes"] and "profile 16 cells" in body["notes"]
        # the prompt asked for every cell by the exact key the reply must use
        for c in CELLS:
            assert c in prov.prompts[0], c

        again = harness.forecast("claude-opus", ROUND, profile_history=hist,
                                 previous=body)
        assert again == body
        assert len(prov.prompts) == 1, "a cached profile round must cost nothing"


def test_a_partial_reply_is_a_failure_not_a_filed_forecast():
    """Never fill in a missing cell. A substituted cell is a forecast the
    entrant did not make, scored as though it had been."""
    short = json.dumps({k: v for k, v in json.loads(good_reply()).items()
                        if k != CELLS[2]})
    with Scratch(), Keys(ANTHROPIC_API_KEY="k"), Provider(short):
        hist = profile_round.frozen_history(ROUND, series_fixture())
        try:
            harness.forecast("claude-opus", ROUND, profile_history=hist)
        except RuntimeError as e:
            assert CELLS[2] in str(e), str(e)
        else:
            raise AssertionError("a partial profile must not be filed")


def test_a_profile_round_is_never_mocked_even_with_mock_enabled():
    """The scalar path may file a labelled placeholder without keys. Sixteen of
    them would be a fabricated joint structure sitting in the headline
    section."""
    with Scratch(), Keys():
        harness.ALLOW_MOCK = True
        try:
            hist = profile_round.frozen_history(ROUND, series_fixture())
            try:
                harness.forecast("claude-opus", ROUND, profile_history=hist)
            except RuntimeError as e:
                assert "never mocked" in str(e), str(e)
            else:
                raise AssertionError("a keyless profile round must raise")
        finally:
            harness.ALLOW_MOCK = False


def test_the_reply_log_replays_a_profile_without_paying_again():
    with Scratch(), Keys(ANTHROPIC_API_KEY="k"):
        hist = profile_round.frozen_history(ROUND, series_fixture())
        with Provider(good_reply()) as prov:
            harness.forecast("claude-opus", ROUND, profile_history=hist)
            assert len(prov.prompts) == 1
        # the same prompt, no previous file, and a provider that would raise
        with Provider(lambda p: (_ for _ in ()).throw(
                AssertionError("must not call the provider"))) as prov:
            body = harness.forecast("claude-opus", ROUND, profile_history=hist)
            assert prov.prompts == []
            assert "replayed from the reply log" in body["notes"]
            assert len(body["profile"]) == 16


# --- the freeze, the resolution, the board ---------------------------------

def test_the_baselines_are_frozen_at_lock_cell_by_cell():
    """The scalar rounds' invariant, applied sixteen times: once a release
    lands in a cell's series, an unfrozen persistence null would contain the
    very value it is scored against."""
    series = series_fixture()
    hist = profile_round.frozen_history(ROUND, series)
    for c in CELLS:
        assert all(p["date"] < "2026-08-12" for p in hist[c]), c
        assert hist[c][-1]["date"] == "2026-08-11", c
    per = profile_round.persistence_profile(hist, CELLS)
    for c in CELLS:
        # the 11th, not the 14th: the answer is not in the null
        assert per[c]["mean"] == round(LEVELS[c] + 11.0, 2), c

    # and build_rounds attaches exactly that, with no scalar null to confuse it
    season = {"season": 0, "rounds": [ROUND]}
    rows, _ = refresh.build_rounds(season, series, {},
                                   refresh.parse_iso("2026-08-16T00:00:00Z"))
    row = rows[0]
    assert row["baselines"] is None, "a vector round has no scalar denominator"
    assert row["scoreable"] is True
    assert row["profile"]["cells"] == CELLS
    got = row["profile"]["baselines"]["persistence"]
    assert got[CELLS[0]]["mean"] == round(LEVELS[CELLS[0]] + 11.0, 2)


def test_a_round_resolves_from_the_cells_own_series_at_the_release():
    series = series_fixture()
    res = profile_round.resolution(ROUND, series)
    assert res["vector"] == [LEVELS[c] + 14.0 for c in CELLS]
    assert set(res["observed_dates"].values()) == {"2026-08-14"}
    assert res["release_date"] == "2026-08-14"


def test_resolution_refuses_a_hole_and_a_series_that_never_published():
    # one cell missing entirely
    series = series_fixture()
    series[CELLS[5]] = []
    try:
        profile_round.resolution(ROUND, series)
    except ValueError as e:
        assert CELLS[5] in str(e), str(e)
    else:
        raise AssertionError("a missing cell must refuse the whole round")

    # every cell present, but nothing has published since the lock: resolving
    # would hand the persistence null the exact value it forecast
    stale = series_fixture(last_day=11)
    try:
        profile_round.resolution(ROUND, stale)
    except ValueError as e:
        assert "predates the lock" in str(e), str(e)
    else:
        raise AssertionError("a stale series must not resolve")


def test_the_board_scores_energy_and_skill_and_ranks_the_better_profile_first():
    """The integration: freeze, resolve, score, rank. The entrant that knows
    the profile must beat the one that is biased, and both are measured against
    per-cell persistence."""
    series = series_fixture()
    season = {"season": 0, "rounds": [ROUND]}
    now = refresh.parse_iso("2026-08-16T00:00:00Z")
    with Scratch() as sc:
        rows, _ = refresh.build_rounds(season, series, {}, now)
        sc.file("sharp", forecast_file("sharp", offset=0.0, sd=3.0))
        sc.file("biased", forecast_file("biased", offset=12.0, sd=3.0))
        board = refresh.build_profile_leaderboard(rows, {}, series)

    assert board["scored_rounds"] == 1, board["skipped"]
    entries = {e["entrant"]: e for e in board["rounds"][0]["entries"]}
    assert set(entries) == {"sharp", "biased"}
    assert entries["sharp"]["energy"] < entries["biased"]["energy"]
    # sharp sits on the answer, so it beats persistence; biased is 12 points off
    assert entries["sharp"]["skill"] > 0, entries["sharp"]
    assert entries["biased"]["skill"] < entries["sharp"]["skill"]
    # skill is defined against the per-cell persistence null, not against zero
    per = board["rounds"][0]["persistence_energy"]
    assert per > 0
    close = abs(entries["sharp"]["skill"]
                - (1 - entries["sharp"]["energy"] / per))
    assert close < 1e-3, close
    # the decomposition RQ3 turns on is published beside the score
    assert "level" in entries["sharp"] and "structure" in entries["sharp"]
    # matched holds the entrants who answered every scored round
    assert {e["entrant"] for e in board["matched"]} == {"sharp", "biased"}
    assert board["board"][0]["entrant"] == "sharp"


def test_a_malformed_submission_is_named_and_excluded_never_repaired():
    series = series_fixture()
    season = {"season": 0, "rounds": [ROUND]}
    now = refresh.parse_iso("2026-08-16T00:00:00Z")
    with Scratch() as sc:
        rows, _ = refresh.build_rounds(season, series, {}, now)
        sc.file("good", forecast_file("good"))
        holed = forecast_file("holed")
        holed["profile"].pop(CELLS[9])
        sc.file("holed", holed)
        sc.file("scalar", {"round_id": ROUND["round_id"], "entrant": "scalar",
                           "topline": {"mean": -24.0, "sd": 2.0}})
        board = refresh.build_profile_leaderboard(rows, {}, series)

    named = {e["entrant"] for e in board["rounds"][0]["entries"]}
    assert named == {"good"}, named
    why = " ".join(s[1] for s in board["skipped"])
    assert CELLS[9] in why, why
    assert "profile" in why


def test_an_explicit_resolution_wins_over_the_recomputed_one():
    """Once written, a resolution is the scoring authority and is never
    recomputed underneath the scores it already fixed."""
    series = series_fixture()
    season = {"season": 0, "rounds": [ROUND]}
    now = refresh.parse_iso("2026-08-16T00:00:00Z")
    hand = {ROUND["round_id"]: {
        "values": {c: LEVELS[c] + 99.0 for c in CELLS},
        "method": "hand-checked from the dashboard screenshots"}}
    with Scratch() as sc:
        rows, _ = refresh.build_rounds(season, series, {}, now)
        sc.file("good", forecast_file("good"))
        board = refresh.build_profile_leaderboard(rows, hand, series)
    got = board["rounds"][0]["outcome"]
    assert got[CELLS[0]] == LEVELS[CELLS[0]] + 99.0, got[CELLS[0]]
    assert board["rounds"][0]["resolution"]["source"] == "resolved.json"


def test_a_profile_round_is_refused_by_the_scalar_resolver():
    """A profile round names an anchor series it shares with the scalar rounds
    on the same tracker. Without the refusal, whichever is past its release
    first claims the other's observation."""
    from ssa import resolve as resolver
    series = series_fixture()
    now = refresh.parse_iso("2026-08-16T00:00:00Z")
    res, why = resolver.resolve_round(ROUND, series, now)
    assert res is None and "profile round" in why, why

    scalar = dict(ROUND, round_id="civiqs-scalar", target_type="continuous_normal")
    scalar.pop("cells")
    season = {"season": 0, "rounds": [ROUND, scalar]}
    new, skipped = resolver.resolve_all(season, series, {}, now)
    assert ROUND["round_id"] not in new
    assert "civiqs-scalar" in new, (new, skipped)


def test_the_filed_null_is_a_vector_in_the_submission_format():
    """The null is scored by exactly the code an entrant's file goes through,
    so it has to be filed in exactly the shape an entrant files."""
    series = series_fixture()
    season = {"season": 0, "rounds": [dict(ROUND, lock_at="2026-08-20T22:00:00Z",
                                           release_at="2026-08-22T22:00:00Z")]}
    now = refresh.parse_iso("2026-08-16T00:00:00Z")
    with Scratch():
        rows, hist = refresh.build_rounds(season, series, {}, now)
        assert rows[0]["status"] == "open"
        written, failures = refresh.file_baseline_forecasts(
            rows, hist, now, series)
        paths = glob.glob(os.path.join(refresh.FORECASTS, ROUND["round_id"],
                                       "*.json"))
        assert paths, (written, failures)
        with open(paths[0]) as f:
            body = json.load(f)
    assert os.path.basename(paths[0]) == "persistence.json"
    assert "topline" not in body and set(body["profile"]) == set(CELLS)
    assert accepts(body), "the filed null must satisfy the submission schema"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
