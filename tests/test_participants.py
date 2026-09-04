"""Route A: who the arena will call, where, with what, and what it refuses."""
import json
import os
import shutil
import sys
import tempfile

import jsonschema

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import agent_api, batches, bundle_api, harness, participants, refresh  # noqa: E402

REG = {
    "entrant_id": "acme-forecast",
    "name": "Acme Forecast",
    "type": "firm",
    "method": "test fixture.",
    "route": {"kind": "agent_api", "base_url": "https://api.acme.test/v1"},
}


class registry:
    """A temporary entrants/ directory, installed everywhere it is read.

    `participants.ENTRANTS` is patched rather than passed, because the point of
    most of these tests is what `harness` does, and harness calls
    `participants.route(entrant)` with no directory argument -- as production
    does.
    """

    def __init__(self, *registrations):
        self.regs = registrations

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="ssa-entrants-")
        for reg in self.regs:
            with open(os.path.join(self.dir, reg["entrant_id"] + ".json"),
                      "w") as fh:
                json.dump(reg, fh)
        self.saved = participants.ENTRANTS
        participants.ENTRANTS = self.dir
        return self

    def __exit__(self, *exc):
        participants.ENTRANTS = self.saved
        shutil.rmtree(self.dir, ignore_errors=True)


def with_key(entrant="acme-forecast", value="sk-test"):
    os.environ[participants.key_env(entrant)] = value


def without_key(entrant="acme-forecast"):
    os.environ.pop(participants.key_env(entrant), None)


# --- the credential ---------------------------------------------------------

def test_the_credential_variable_cannot_be_named_by_the_registration():
    """The whole reason `key_env` is derived rather than declared.

    A registration is a file in a pull request. If it could name the variable
    holding its bearer token, it could name `ANTHROPIC_API_KEY`, and the arena
    would put our provider key in an Authorization header addressed to the
    `base_url` in the same file. It could equally name another participant's.
    Deriving the name from the entrant id makes both unrepresentable.
    """
    schema = json.load(open(os.path.join(ROOT, "schema",
                                         "entrant.schema.json")))
    assert schema["properties"]["route"]["additionalProperties"] is False
    fields = set(schema["properties"]["route"]["properties"])
    assert not (fields & {"key_env", "key", "api_key", "credential",
                          "token", "secret", "authorization"}), \
        f"the route block can name a credential: {sorted(fields)}"

    hostile = dict(REG, route=dict(REG["route"], key_env="ANTHROPIC_API_KEY"))
    try:
        jsonschema.validate(hostile, schema)
    except jsonschema.ValidationError:
        pass
    else:
        raise AssertionError("a registration naming a credential validated")

    assert participants.key_env("acme-forecast") == \
        "SSA_ENTRANT_KEY_ACME_FORECAST"
    with registry(REG):
        assert harness.route("acme-forecast")["env"] == \
            "SSA_ENTRANT_KEY_ACME_FORECAST"
    print("ok test_the_credential_variable_cannot_be_named_by_the_registration")


def test_a_participant_route_is_https_only():
    """Checked here as well as in the schema. The schema runs when a
    registration is opened as a pull request; this runs every time we are about
    to send a bearer token to the address in it."""
    for bad in ("http://api.acme.test/v1",
                "https://api.acme.test/v1?key=leaked",
                "ftp://api.acme.test", ""):
        with registry(dict(REG, route=dict(REG["route"], base_url=bad))):
            try:
                participants.route("acme-forecast")
            except ValueError as err:
                assert "https" in str(err), str(err)
            else:
                raise AssertionError(f"{bad!r} was accepted as a route")
    print("ok test_a_participant_route_is_https_only")


# --- revocation -------------------------------------------------------------

def test_revocation_stops_the_call_and_the_roster():
    """Honoured at the point of dialling, not only in the probe."""
    revoked = dict(REG, status="revoked")
    with registry(revoked):
        with_key()
        assert participants.route("acme-forecast") is None
        assert participants.callable_now("acme-forecast") == \
            (False, "registration is revoked")
        # Still a participant: the roster has to be able to say "registered,
        # not called" rather than silently reclassify them as one of our
        # models, whose id would then fail to resolve.
        assert participants.is_participant("acme-forecast")
        assert "acme-forecast" in participants.registered()
        assert not any(e == "acme-forecast" for e, *_ in refresh.season_roster())
    print("ok test_revocation_stops_the_call_and_the_roster")


# --- the roster -------------------------------------------------------------

def test_an_unkeyed_participant_is_not_queued_every_six_hours():
    """Onboarding is not a fault. Queuing an entrant whose key we have not
    installed yet fails the run every cycle for a week and teaches everyone to
    ignore the colour."""
    with registry(REG):
        without_key()
        ok, why = participants.callable_now("acme-forecast")
        assert not ok and "SSA_ENTRANT_KEY_ACME_FORECAST" in why
        assert not any(e == "acme-forecast" for e, *_ in refresh.season_roster())

        with_key()
        assert participants.callable_now("acme-forecast") == (True, "")
        seats = [row for row in refresh.season_roster()
                 if row[0] == "acme-forecast"]
        assert seats == [("acme-forecast", "agent-api", "participant",
                          "participant")]
    print("ok test_an_unkeyed_participant_is_not_queued_every_six_hours")


def test_an_auth_none_endpoint_is_callable_without_a_secret():
    with registry(dict(REG, route=dict(REG["route"], auth="none"))):
        without_key()
        assert harness.route("acme-forecast")["env"] == ""
        assert participants.callable_now("acme-forecast") == (True, "")
        assert harness.has_key("acme-forecast")
    print("ok test_an_auth_none_endpoint_is_callable_without_a_secret")


# --- what a participant must never be routed through ------------------------

def test_a_participant_has_no_standby_and_no_base_override():
    """Two ways their round could reach a host their registration does not
    name, both closed.

    A standby would send it to OpenRouter on our account and file the reply as
    their forecast. `SSA_BASE_<X>` is our escape hatch for a gateway of our
    own; applied here an environment variable would silently redirect someone
    else's endpoint, and the public record would be wrong about where their
    forecast came from.
    """
    with registry(REG):
        with_key()
        assert harness.standby_route("acme-forecast") is None
        os.environ["SSA_BASE_ACME_FORECAST"] = "https://elsewhere.test/v1"
        os.environ["SSA_MODEL_ACME_FORECAST"] = "some-other-model"
        try:
            assert harness.base_url("acme-forecast") == \
                "https://api.acme.test/v1"
            assert harness.model_id("acme-forecast") == "ssa-agent"
        finally:
            os.environ.pop("SSA_BASE_ACME_FORECAST", None)
            os.environ.pop("SSA_MODEL_ACME_FORECAST", None)
        for via in ("openrouter", "direct"):
            try:
                harness.route("acme-forecast", via=via)
            except ValueError as err:
                assert "participant" in str(err)
            else:
                raise AssertionError(f"a participant was routed via {via}")
    print("ok test_a_participant_has_no_standby_and_no_base_override")


# --- the envelope -----------------------------------------------------------

def _round(round_id):
    with open(os.path.join(ROOT, "questions", "season0.json")) as fh:
        season = json.load(fh)
    rounds = season["rounds"] if isinstance(season, dict) else season
    r = next(x for x in rounds if x["round_id"] == round_id)
    return dict(r, baselines={"persistence": {"mean": 1.0, "sd": 2.0}})


def test_every_round_shape_builds_a_valid_envelope():
    schema = json.load(open(os.path.join(ROOT, "schema",
                                         "agent-api-request.schema.json")))
    wanted = {"civiqs-2026-w38-approval": ("topline", "continuous_normal"),
              "civiqs-profile-2026-w38": ("profile", "profile_energy"),
              "wiki-top10-2026-09-27": ("ranking", "ranking_list")}
    for round_id, (board, tt) in wanted.items():
        r = _round(round_id)
        env = agent_api.build_envelope(
            "acme-forecast", r,
            history=[{"date": "2026-09-01", "value": 1.0}],
            profile_history={c: [{"date": "2026-09-01", "value": 1.0}]
                             for c in (r.get("cells") or [])},
            ranking_history=[["a", "b", "c"]])
        jsonschema.validate(env, schema)
        assert env["round"]["board_id"] == board
        assert env["round"]["target_type"] == tt
        if tt == "profile_energy":
            assert len(env["round"]["cells"]) == len(r["cells"])
        if tt == "ranking_list":
            assert env["round"]["ranking"]["length"] > 0
    print("ok test_every_round_shape_builds_a_valid_envelope")


def test_the_envelope_states_the_participant_deadline_not_our_lock():
    """A round locks 0 to 7 days after the deadline its answers were due. An
    envelope carrying the later moment tells an endpoint it has until Wednesday
    when its answer stopped counting on Monday."""
    r = _round("civiqs-2026-w38-approval")
    env = agent_api.build_envelope("acme-forecast", r)
    due = batches.effective_deadline(r["lock_at"])
    assert env["round"]["lock_at"] == due.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert env["round"]["lock_at"] < r["lock_at"], \
        "the fixture must be a round whose lock is after its deadline"
    print("ok test_the_envelope_states_the_participant_deadline_not_our_lock")


def test_the_request_id_is_stable_so_a_retry_is_the_same_question():
    """The contract promises idempotency by entrant, round and input hash. A
    random request_id would make every retry a new question to an endpoint that
    dedupes on it."""
    r = _round("civiqs-2026-w38-approval")
    a = agent_api.build_envelope("acme-forecast", r)
    b = agent_api.build_envelope("acme-forecast", r)
    assert a == b and a["request_id"] == "acme-forecast:civiqs-2026-w38-approval"
    print("ok test_the_request_id_is_stable_so_a_retry_is_the_same_question")


# --- the reply --------------------------------------------------------------

def test_a_reply_that_is_not_the_contract_is_refused():
    good = json.dumps({"schema_version": "ssa-agent-api-v1",
                       "forecast": {"mean": 50.0, "sd": 5.0}})
    assert agent_api.parse_scalar(good) == {"mean": 50.0, "sd": 5.0}

    for text, expect in [
        ("just a number: 50", "not JSON"),
        ("[1, 2]", "not an object"),
        (json.dumps({"forecast": {"mean": 1, "sd": 1}}), "schema_version"),
        (json.dumps({"schema_version": "v2",
                     "forecast": {"mean": 1, "sd": 1}}), "schema_version"),
        (json.dumps({"schema_version": "ssa-agent-api-v1"}), "no `forecast`"),
        (json.dumps({"schema_version": "ssa-agent-api-v1",
                     "forecast": {"mean": 1}}), "needs `mean` and `sd`"),
    ]:
        try:
            agent_api.parse_scalar(text)
        except ValueError as err:
            assert expect in str(err), f"{expect!r} not in {err}"
        else:
            raise AssertionError(f"accepted a non-contract reply: {text[:40]}")
    print("ok test_a_reply_that_is_not_the_contract_is_refused")


def test_a_profile_reply_is_all_cells_or_none():
    cells = ["a", "b", "c"]
    whole = json.dumps({"schema_version": "ssa-agent-api-v1", "forecast": {
        "profile": {c: {"mean": 1.0, "sd": 1.0} for c in cells}}})
    assert set(agent_api.parse_profile(whole, cells)) == set(cells)

    partial = json.dumps({"schema_version": "ssa-agent-api-v1", "forecast": {
        "profile": {c: {"mean": 1.0, "sd": 1.0} for c in cells[:2]}}})
    try:
        agent_api.parse_profile(partial, cells)
    except ValueError as err:
        assert "missing 1 of 3" in str(err), str(err)
    else:
        raise AssertionError("a profile with a hole was accepted; the energy "
                             "score is a norm over the whole vector")
    print("ok test_a_profile_reply_is_all_cells_or_none")


# --- filing -----------------------------------------------------------------

def test_a_participant_forecast_is_never_mocked():
    """`harness.forecast` files a labelled placeholder when a key is missing
    and SSA_ALLOW_MOCK is set. Under someone else's entrant id that is us
    inventing their forecast."""
    r = _round("civiqs-2026-w38-approval")
    with registry(REG):
        without_key()
        saved = harness.ALLOW_MOCK
        harness.ALLOW_MOCK = True
        try:
            harness.forecast("acme-forecast", r)
        except RuntimeError as err:
            assert "no credential" in str(err), str(err)
        else:
            raise AssertionError("a forecast was invented for a participant "
                                 "we could not reach")
        finally:
            harness.ALLOW_MOCK = saved
    print("ok test_a_participant_forecast_is_never_mocked")


def test_filing_goes_through_the_shared_runner_and_caches_like_one():
    """The reason this reuses `_ask`: idempotency by input hash, which the
    contract requires, is inherited rather than reimplemented."""
    r = _round("civiqs-2026-w38-approval")
    calls = []

    def fake_ask(entrant, prompt, previous, round_id, parse=None):
        calls.append(prompt)
        return parse(json.dumps({"schema_version": "ssa-agent-api-v1",
                                 "forecast": {"mean": -23.0, "sd": 1.5}})), \
            "participant", harness.prompt_hash(entrant, prompt), False

    with registry(REG):
        with_key()
        saved = harness._ask
        harness._ask = fake_ask
        try:
            body = harness.forecast("acme-forecast", r)
            assert body["entrant"] == "acme-forecast"
            assert body["topline"] == {"mean": -23.0, "sd": 1.5}
            assert "via=participant" in body["notes"]
            assert "in=" in body["notes"]
            # Second pass with the file on disk: no second call.
            again = harness.forecast("acme-forecast", r, previous=body)
            assert again is body and len(calls) == 1
        finally:
            harness._ask = saved
    print("ok test_filing_goes_through_the_shared_runner_and_caches_like_one")


def test_a_registration_without_a_route_is_unchanged():
    """Every registration written before this field must keep meaning exactly
    what it meant: an entrant that hands its forecasts over itself."""
    plain = {k: v for k, v in REG.items() if k != "route"}
    with registry(plain):
        assert not participants.is_participant("acme-forecast")
        assert participants.route("acme-forecast") is None
        assert participants.registered() == []
    schema = json.load(open(os.path.join(ROOT, "schema",
                                         "entrant.schema.json")))
    live = []
    directory = os.path.join(ROOT, "entrants")
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(directory, name)) as fh:
            jsonschema.validate(json.load(fh), schema)
        live.append(name[:-5])
    assert live, "no registrations found to re-validate"
    # `key_env` folds `-` and `.` to `_`, and ids use both. Two ids sharing a
    # variable would send one entrant's token to the other's endpoint, and let
    # one upload token file as either.
    for derive in (participants.key_env, bundle_api.upload_key_env):
        names = [derive(entrant) for entrant in live]
        assert len(set(names)) == len(names), sorted(
            n for n in names if names.count(n) > 1)
    print(f"ok test_a_registration_without_a_route_is_unchanged "
          f"({len(live)} committed registrations still validate)")


if __name__ == "__main__":
    test_the_credential_variable_cannot_be_named_by_the_registration()
    test_a_participant_route_is_https_only()
    test_revocation_stops_the_call_and_the_roster()
    test_an_unkeyed_participant_is_not_queued_every_six_hours()
    test_an_auth_none_endpoint_is_callable_without_a_secret()
    test_a_participant_has_no_standby_and_no_base_override()
    test_every_round_shape_builds_a_valid_envelope()
    test_the_envelope_states_the_participant_deadline_not_our_lock()
    test_the_request_id_is_stable_so_a_retry_is_the_same_question()
    test_a_reply_that_is_not_the_contract_is_refused()
    test_a_profile_reply_is_all_cells_or_none()
    test_a_participant_forecast_is_never_mocked()
    test_filing_goes_through_the_shared_runner_and_caches_like_one()
    test_a_registration_without_a_route_is_unchanged()
    print("14 passed")
