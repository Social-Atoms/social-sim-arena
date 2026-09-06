"""Route A: who the arena will call, where, with what, and what it refuses."""
import json
import os
import shutil
import sys
import tempfile

import jsonschema

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import signing  # noqa: E402
from ssa import agent_api, batches, bundle_api, harness, participants, refresh  # noqa: E402

REG = {
    "entrant_id": "acme-forecast",
    "name": "Acme Forecast",
    "type": "firm",
    "method": "test fixture.",
    "route": {"kind": "agent_api", "url": "https://api.acme.test/forecast"},
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


SIGNING_PRIVATE, SIGNING_PUBLIC = signing.generate()


def with_key():
    """The arena's own signing key, the only secret Route A has."""
    os.environ[signing.LIVE_KEY_ENV] = SIGNING_PRIVATE


def without_key():
    os.environ.pop(signing.LIVE_KEY_ENV, None)


# --- the credential ---------------------------------------------------------

def test_the_registration_carries_no_credential_and_cannot_name_one():
    """A registration is a file in a pull request. If it could name a
    credential it could name `ANTHROPIC_API_KEY`, and the arena would put our
    provider key in a header addressed to the `url` in the same file. Route A
    has no per-entrant credential at all: the arena signs, the entrant
    verifies with the published key."""
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

    assert "auth" not in fields, "the route block has no auth mode any more"
    with registry(REG):
        assert harness.route("acme-forecast")["env"] == "", \
            "a participant route must name no environment variable"
        assert harness.route("acme-forecast")["api"] == "agent"
    print("ok test_the_registration_carries_no_credential_and_cannot_name_one")


def test_a_participant_route_is_https_only():
    """Checked here as well as in the schema. The schema runs when a
    registration is opened as a pull request; this runs every time we are about
    to send a bearer token to the address in it."""
    for bad in ("http://api.acme.test/forecast",
                "https://api.acme.test/forecast?key=leaked",
                "ftp://api.acme.test", ""):
        with registry(dict(REG, route=dict(REG["route"], url=bad))):
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

def test_without_a_signing_key_no_participant_is_called():
    """The arena never sends an unsigned request: a verifying endpoint would
    refuse it and could not tell our misconfiguration from an attack. So with
    no signing key installed, participants are left off the roster, and the
    reason names the variable."""
    with registry(REG):
        without_key()
        ok, why = participants.callable_now("acme-forecast")
        assert not ok and signing.LIVE_KEY_ENV in why
        assert not any(e == "acme-forecast" for e, *_ in refresh.season_roster())

        with_key()
        assert participants.callable_now("acme-forecast") == (True, "")
        seats = [row for row in refresh.season_roster()
                 if row[0] == "acme-forecast"]
        assert seats == [("acme-forecast", "agent-api", "participant",
                          "participant")]
    print("ok test_without_a_signing_key_no_participant_is_called")


def test_a_participant_request_is_signed_over_the_bytes_sent():
    """What travels: the envelope as the whole body, and three headers whose
    signature verifies with the published public key over exactly those
    bytes. A body re-serialised on the way would not verify, which is the
    property a participant relies on."""
    calls = []

    class Response:
        status_code = 200
        content = (b'{"schema_version": "ssa-agent-api-v2", '
                   b'"forecast": {"mean": 1.0, "sd": 1.0}}')

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    with_key()
    real_post = harness.requests.post
    harness.requests.post = fake_post
    try:
        harness._call_agent({}, "https://api.acme.test/forecast", "", "acme",
                            '{"request_id":"acme:r1","round":{}}')
    finally:
        harness.requests.post = real_post
    url, kw = calls[0]
    assert url == "https://api.acme.test/forecast"
    assert kw["data"] == b'{"request_id":"acme:r1","round":{}}'
    assert "Authorization" not in kw["headers"], "no bearer token travels"
    assert signing.verify(SIGNING_PUBLIC, kw["headers"], kw["data"]) == "ssa-live"
    try:
        signing.verify(SIGNING_PUBLIC, kw["headers"], kw["data"] + b" ")
        raise AssertionError("a changed body verified")
    except signing.SignatureError:
        pass
    without_key()
    try:
        harness._call_agent({}, "https://api.acme.test/forecast", "", "acme", "{}")
        raise AssertionError("an unsigned request was sent")
    except RuntimeError as err:
        assert signing.LIVE_KEY_ENV in str(err)
    print("ok test_a_participant_request_is_signed_over_the_bytes_sent")


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
                "https://api.acme.test/forecast"
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
    good = json.dumps({"schema_version": "ssa-agent-api-v2",
                       "forecast": {"mean": 50.0, "sd": 5.0}})
    assert agent_api.parse_scalar(good) == {"mean": 50.0, "sd": 5.0}

    for text, expect in [
        ("just a number: 50", "not JSON"),
        ("[1, 2]", "not an object"),
        (json.dumps({"forecast": {"mean": 1, "sd": 1}}), "schema_version"),
        (json.dumps({"schema_version": "v2",
                     "forecast": {"mean": 1, "sd": 1}}), "schema_version"),
        (json.dumps({"schema_version": "ssa-agent-api-v2"}), "no `forecast`"),
        (json.dumps({"schema_version": "ssa-agent-api-v2",
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
    whole = json.dumps({"schema_version": "ssa-agent-api-v2", "forecast": {
        "profile": {c: {"mean": 1.0, "sd": 1.0} for c in cells}}})
    assert set(agent_api.parse_profile(whole, cells)) == set(cells)

    partial = json.dumps({"schema_version": "ssa-agent-api-v2", "forecast": {
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
    """`harness.forecast` files a labelled placeholder when a provider key is
    missing and SSA_ALLOW_MOCK is set. Under someone else's entrant id that is
    us inventing their forecast, so an uncallable participant raises."""
    r = _round("civiqs-2026-w38-approval")
    with registry(REG):
        without_key()
        saved = harness.ALLOW_MOCK
        harness.ALLOW_MOCK = True
        try:
            harness.forecast("acme-forecast", r)
        except RuntimeError as err:
            assert "no signing key" in str(err), str(err)
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
        return parse(json.dumps({"schema_version": "ssa-agent-api-v2",
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


def test_every_shape_files_through_the_real_transport_and_validates():
    """The whole path below `forecast`, nothing mocked but the socket: the
    envelope is signed and POSTed, the reply is parsed, and the filed dict
    validates against the forecast schema under the key the scorer reads.
    This is the test that would have caught two shipped bugs: `call_provider`
    resolving a participant id as one of our models, and a profile filed
    under `cells` instead of `profile`."""
    import jsonschema
    from ssa import profile_round, ranking_round
    schema = json.load(open(os.path.join(ROOT, "schema", "forecast.schema.json")))
    season = json.load(open(os.path.join(ROOT, "questions", "season0.json")))["rounds"]
    picks = {}
    for x in season:
        tt = x.get("target_type", "continuous_normal")
        picks.setdefault(tt, x)
    assert set(picks) == {"continuous_normal", "profile_energy", "ranking_list"}, picks.keys()

    def answer(url, **kw):
        env = json.loads(kw["data"])
        rd = env["round"]
        if rd["target_type"] == "profile_energy":
            fc = {"profile": {c: {"mean": 10.0, "sd": 2.0} for c in rd["cells"]}}
        elif rd["target_type"] == "ranking_list":
            spec = rd["ranking"]
            items = spec.get("items") or [f"Item_{i}" for i in range(spec["length"])]
            fc = {"ranking": items[:spec["length"]]}
        else:
            fc = {"mean": 42.0, "sd": 3.0}

        class Response:
            status_code = 200
            content = json.dumps({"schema_version": "ssa-agent-api-v2", "forecast": fc}).encode()
        return Response()

    log_dir = tempfile.mkdtemp(prefix="ssa-replies-")
    with registry(REG):
        with_key()
        real_post = harness.requests.post
        harness.requests.post = answer
        # The reply log is real too, so point it away from the repository.
        os.environ["SSA_REPLIES_DIR"] = log_dir
        try:
            for tt, x in picks.items():
                r = dict(x, baselines={"persistence": {"mean": 1.0, "sd": 2.0}})
                kw = {}
                if profile_round.is_profile(r):
                    kw["profile_history"] = {c: [] for c in profile_round.cells_for(r)}
                if ranking_round.is_ranking(r):
                    kw["ranking_history"] = []
                body = harness.forecast("acme-forecast", r, previous=None, **kw)
                jsonschema.validate(body, schema)
                key = {"continuous_normal": "topline", "profile_energy": "profile",
                       "ranking_list": "ranking"}[tt]
                assert key in body, (tt, body.keys())
                assert body["entrant"] == "acme-forecast" and "via=participant" in body["notes"]
        finally:
            harness.requests.post = real_post
            os.environ.pop("SSA_REPLIES_DIR", None)
            shutil.rmtree(log_dir, ignore_errors=True)
    print("ok test_every_shape_files_through_the_real_transport_and_validates")


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
    # `upload_key_env` folds `-` and `.` to `_`, and ids use both. Two ids
    # sharing a variable would let one upload token file as either.
    names = [bundle_api.upload_key_env(entrant) for entrant in live]
    assert len(set(names)) == len(names), sorted(
        n for n in names if names.count(n) > 1)
    print(f"ok test_a_registration_without_a_route_is_unchanged "
          f"({len(live)} committed registrations still validate)")


def test_a_reply_over_the_size_cap_is_refused_unread():
    """A megabyte is not a forecast. The body is read to the cap plus one
    byte and refused; nothing past it is downloaded or parsed."""
    class Big:
        status_code = 200
        content = b"[" + b"1," * (harness.AGENT_MAX_REPLY_BYTES // 2) + b"1]"

    with_key()
    real_post = harness.requests.post
    harness.requests.post = lambda url, **kw: Big()
    try:
        harness._call_agent({}, "https://api.acme.test/forecast", "", "acme", "{}")
        raise AssertionError("an oversized reply was accepted")
    except RuntimeError as err:
        assert "exceeds" in str(err), err
    finally:
        harness.requests.post = real_post
    print("ok test_a_reply_over_the_size_cap_is_refused_unread")


def test_an_endpoint_failing_three_times_is_not_called_again_this_run():
    """The per-run stop: after MAX_CONSECUTIVE_FAILURES the remaining rounds
    are recorded as failures without a request, and a success resets it."""
    from ssa import agent_api
    calls = []

    def failing_ask(entrant, prompt, previous, round_id, parse=None):
        calls.append(round_id)
        raise RuntimeError("down")

    with registry(REG):
        with_key()
        real_ask = harness._ask
        harness._ask = failing_ask
        agent_api._failures_this_run.clear()
        try:
            rounds = [x for x in json.load(open(os.path.join(
                ROOT, "questions", "season0.json")))["rounds"]
                if x.get("target_type", "continuous_normal") == "continuous_normal"][:5]
            for i, r in enumerate(rounds):
                try:
                    agent_api.forecast("acme-forecast", r)
                    raise AssertionError("a failing endpoint filed something")
                except RuntimeError as err:
                    if i >= agent_api.MAX_CONSECUTIVE_FAILURES:
                        assert "not called" in str(err), err
            assert len(calls) == agent_api.MAX_CONSECUTIVE_FAILURES, calls
        finally:
            harness._ask = real_ask
            agent_api._failures_this_run.clear()
    print("ok test_an_endpoint_failing_three_times_is_not_called_again_this_run")


if __name__ == "__main__":
    test_the_registration_carries_no_credential_and_cannot_name_one()
    test_a_participant_route_is_https_only()
    test_revocation_stops_the_call_and_the_roster()
    test_without_a_signing_key_no_participant_is_called()
    test_a_participant_request_is_signed_over_the_bytes_sent()
    test_a_participant_has_no_standby_and_no_base_override()
    test_every_round_shape_builds_a_valid_envelope()
    test_the_envelope_states_the_participant_deadline_not_our_lock()
    test_the_request_id_is_stable_so_a_retry_is_the_same_question()
    test_a_reply_that_is_not_the_contract_is_refused()
    test_a_profile_reply_is_all_cells_or_none()
    test_a_participant_forecast_is_never_mocked()
    test_filing_goes_through_the_shared_runner_and_caches_like_one()
    test_every_shape_files_through_the_real_transport_and_validates()
    test_a_registration_without_a_route_is_unchanged()
    test_a_reply_over_the_size_cap_is_refused_unread()
    test_an_endpoint_failing_three_times_is_not_called_again_this_run()
    print("17 passed")
