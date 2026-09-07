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


def test_the_envelope_states_the_moment_an_answer_stops_counting():
    """The envelope carries the moment an answer stops counting, which is the
    round's own close. It is read straight from `batches.effective_deadline`
    rather than copied from the season file, so a change to the rule reaches
    the endpoint instead of only the validator."""
    r = _round("civiqs-2026-w38-approval")
    env = agent_api.build_envelope("acme-forecast", r)
    due = batches.effective_deadline(r["lock_at"])
    assert env["round"]["lock_at"] == due.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert env["round"]["lock_at"] == r["lock_at"], \
        "the envelope must state the round's own close"
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
                     "forecast": {"mean": 1}}), "needs mean and sd"),
        (json.dumps({"schema_version": "ssa-agent-api-v2",
                     "forecast": {"value": 41.2}}), "needs mean and sd"),
        # Refused by name rather than by "needs mean and sd", because an
        # endpoint written against the older contract is not making a typo and
        # should be told what changed.
        (json.dumps({"schema_version": "ssa-agent-api-v2",
                     "forecast": {"quantiles": {"0.05": 44.0, "0.5": 50.0,
                                                "0.95": 58.0}}}),
         "quantiles are no longer accepted"),
    ]:
        try:
            agent_api.parse_scalar(text)
        except ValueError as err:
            assert expect in str(err), f"{expect!r} not in {err}"
        else:
            raise AssertionError(f"accepted a non-contract reply: {text[:40]}")
    print("ok test_a_reply_that_is_not_the_contract_is_refused")


def test_a_forecast_is_stored_as_it_was_answered():
    """No rounding on the way in, for any entrant.

    `harness._distribution` rounded `mean` and `sd` to two decimals. For the
    mean that is a silent edit to somebody's forecast; for the sd it changes
    what the forecast says: 0.004 passes the "must be above zero" check on the
    line above and was then written down as 0.0, which is a point guess -- the
    one thing the arena refuses -- in a file `schema/forecast.schema.json`
    rejects for `exclusiveMinimum: 0`. Two decimals were never a rule anywhere.

    Lives here rather than in `tests/test_harness.py`, which is kept local and
    unpublished, so CI runs it.
    """
    import jsonschema
    schema = json.load(open(os.path.join(ROOT, "schema", "forecast.schema.json")))

    got = harness.answer({"mean": 41.2345, "sd": 0.004})
    assert got == {"mean": 41.2345, "sd": 0.004}, got
    jsonschema.validate({"round_id": "aaii-2026-09-10", "entrant": "acme-forecast",
                         "topline": got, "notes": "n"}, schema)

    # A real zero is still refused, and says so before anything is filed.
    for sd in (0.0, -1.0):
        try:
            harness.answer({"mean": 41.0, "sd": sd})
            raise AssertionError(f"sd {sd} was accepted")
        except ValueError as err:
            assert "sd out of schema range" in str(err), err

    # Every cell of a profile goes through the same function, so the rule
    # cannot hold for a topline and quietly not hold for a cell.
    cells = ["civiqs_net_approval_dem", "civiqs_net_approval_rep"]
    prof = agent_api.parse_profile(json.dumps(
        {"schema_version": "ssa-agent-api-v2",
         "forecast": {"profile": {cells[0]: {"mean": 1.005, "sd": 0.004},
                                  cells[1]: {"mean": 2.0, "sd": 1.0}}}}), cells)
    assert prof[cells[0]] == {"mean": 1.005, "sd": 0.004}, prof
    jsonschema.validate({"round_id": "civiqs-profile-2026-w38",
                         "entrant": "acme-forecast", "profile": prof,
                         "notes": "n"}, schema)
    print("ok test_a_forecast_is_stored_as_it_was_answered")


def test_an_endpoint_answers_in_one_shape_and_it_is_the_filed_one():
    """`{mean, sd}`, for a topline and for every profile cell, and nothing else.

    A reply could once be a normal or a quantile set. No endpoint ever sent a
    quantile set and no committed forecast holds one, so what the second shape
    actually bought was a second parser to keep in step with
    `tools/validate_submission.py`. The rule is narrower than the file schema on
    purpose: a hand-committed file may still carry quantiles and
    `scoring.crps_forecast` still scores them, so the claim that formats compete
    on equal terms is untouched -- this is only what a live reply may contain.
    """
    import jsonschema
    from ssa import scoring
    schema = json.load(open(os.path.join(ROOT, "schema", "forecast.schema.json")))
    response_schema = json.load(open(os.path.join(
        ROOT, "schema", "agent-api-response.schema.json")))
    q = {"0.05": 33.0, "0.5": 36.0, "0.95": 40.5}
    cells = ["civiqs_net_approval_dem", "civiqs_net_approval_rep"]

    for body in ({"schema_version": "ssa-agent-api-v2",
                  "forecast": {"quantiles": q}},
                 {"schema_version": "ssa-agent-api-v2",
                  "forecast": {"profile": {cells[0]: {"quantiles": q},
                                           cells[1]: {"mean": 2.0, "sd": 1.0}}}}):
        # The published schema and the parser have to agree, or a participant
        # validates against the contract page and is refused by the arena.
        assert not jsonschema.Draft7Validator(response_schema).is_valid(body), \
            "the response schema still accepts quantiles"
        parse = (agent_api.parse_scalar if "profile" not in body["forecast"]
                 else lambda t: agent_api.parse_profile(t, cells))
        try:
            parse(json.dumps(body))
            raise AssertionError("a quantile reply was accepted")
        except ValueError as err:
            assert "quantiles are no longer accepted" in str(err), err

    top = agent_api.parse_scalar(json.dumps(
        {"schema_version": "ssa-agent-api-v2",
         "forecast": {"mean": 36.0, "sd": 1.5}}))
    prof = agent_api.parse_profile(json.dumps(
        {"schema_version": "ssa-agent-api-v2",
         "forecast": {"profile": {cells[0]: {"mean": -35.0, "sd": 2.0},
                                  cells[1]: {"mean": 2.0, "sd": 1.0}}}}), cells)
    assert top == {"mean": 36.0, "sd": 1.5}, top
    assert scoring.crps_forecast(top, 36.0) > 0
    for body in ({"round_id": "aaii-2026-09-10", "entrant": "acme-forecast",
                  "topline": top, "notes": "n"},
                 {"round_id": "aaii-2026-09-10", "entrant": "acme-forecast",
                  "profile": prof, "notes": "n"}):
        jsonschema.validate(body, schema)
    # …and the file schema is unchanged: a committed quantile forecast still
    # validates and still scores, which is the half that was not narrowed.
    jsonschema.validate({"round_id": "aaii-2026-09-10", "entrant": "human-crowd",
                         "topline": {"quantiles": q}, "notes": "n"}, schema)
    assert scoring.crps_forecast({"quantiles": q}, 36.0) > 0
    print("ok test_an_endpoint_answers_in_one_shape_and_it_is_the_filed_one")


def test_the_round_tells_a_participant_what_it_will_refuse():
    """A ranking round throws away an answer containing an excluded title, and
    `Main_Page` is first on the Wikipedia weekly by an order of magnitude every
    single week. If the envelope does not name the rule, the most obvious answer
    an endpoint can give is the one that scores nothing."""
    import jsonschema
    from ssa import ranking_round
    schema = json.load(open(os.path.join(ROOT, "schema", "agent-api-request.schema.json")))
    season = json.load(open(os.path.join(ROOT, "questions", "season0.json")))["rounds"]
    r = next(x for x in season if x.get("target_type") == "ranking_list")
    env = agent_api.build_envelope("acme-forecast", dict(r, baselines={}), ranking_history=[])
    jsonschema.validate(env, schema)
    block = env["round"]["ranking"]
    spec = ranking_round.spec_for(r)
    assert block["length"] == spec["length"]
    assert block["exclusions"]["rule"] == spec["exclusions"]
    assert "Main_Page" in block["exclusions"]["titles"]
    assert "Special:" in block["exclusions"]["prefixes"]
    assert "underscores" in block["item_format"]
    # And the rule the envelope quotes is the rule the parser applies.
    try:
        agent_api.parse_ranking(json.dumps(
            {"schema_version": "ssa-agent-api-v2",
             "forecast": {"ranking": ["Main_Page"] + [f"A{i}" for i in range(spec["length"] - 1)]}}), spec)
        raise AssertionError("an excluded title was accepted")
    except ValueError as err:
        assert spec["exclusions"] in str(err), err
    print("ok test_the_round_tells_a_participant_what_it_will_refuse")


def test_the_starter_server_answers_all_three_shapes_from_the_envelope_alone():
    """The reference implementation is the thing a participant copies, and a
    ranking round it cannot answer is a shape nobody can rehearse. It repeats
    the last week the round handed it, minus what the round excludes -- the
    persistence null, built only from the envelope."""
    import sys
    sys.path.insert(0, os.path.join(ROOT, "examples", "agent-api"))
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from server import forecast_for
    from rehearse_endpoint import ranking_history
    from ssa import ranking_round
    season = json.load(open(os.path.join(ROOT, "questions", "season0.json")))["rounds"]
    # The real frozen history from the committed archive, not a hand-made one:
    # the first version of this test invented `{"week_end", "ranking"}` while
    # production hands over `{"date", "items", "views"}`, so it passed against
    # a shape that does not exist and the live rehearsal failed.
    r, weeks = None, []
    for cand in season:
        if cand.get("target_type") != "ranking_list":
            continue
        weeks = ranking_history(cand)
        if weeks:
            r = cand
            break
    assert r and weeks, "no committed ranking history to rehearse against"
    assert "items" in weeks[-1], sorted(weeks[-1])
    spec = ranking_round.spec_for(r)
    env = agent_api.build_envelope("acme-forecast", dict(r, baselines={}),
                                   ranking_history=weeks)
    reply = forecast_for(env["round"])
    order = reply["ranking"]
    assert len(order) == spec["length"], order
    assert "Main_Page" not in order and not any(t.startswith("Special:") for t in order)
    assert order == [t for t in weeks[-1]["items"] if t != "Main_Page"][:spec["length"]]
    # And what it produced is what the arena accepts for this round.
    parsed = agent_api.parse_ranking(json.dumps(
        {"schema_version": "ssa-agent-api-v2", "forecast": reply}), spec)
    assert parsed == order
    print("ok test_the_starter_server_answers_all_three_shapes_from_the_envelope_alone")


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


def test_the_refresh_loop_files_a_participant_with_our_models_removed():
    """`refresh.file_baseline_forecasts`, the loop the cron runs, with the
    roster reduced to participants (SSA_ELICITATION=only:) and the socket
    faked: the participant's forecast lands as a file. This is the layer above
    `harness.forecast`, where the third `resolve(entrant)` on a participant id
    lived, and the one a manual dev run of the workflow found."""
    import glob
    import datetime as dt

    def answer(url, **kw):
        class R:
            status_code = 200
            content = (b'{"schema_version": "ssa-agent-api-v2", '
                       b'"forecast": {"mean": 42.0, "sd": 3.0}}')
        return R()

    season = json.load(open(os.path.join(ROOT, "questions", "season0.json")))
    scalar = next(x for x in season["rounds"]
                  if x.get("target_type", "continuous_normal") == "continuous_normal")
    # An open round inside the call window: every entrant is called between
    # 24 h and 30 min before the round closes, so the close is 12 h after `now`.
    now = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
    r = dict(scalar, lock_at="2026-09-02T00:00:00Z", release_at="2026-09-04T00:00:00Z",
             release_estimated=True)
    series = {r["series"]: [{"date": f"2026-08-{d:02d}", "value": 40.0 + d / 10} for d in range(1, 29)]}
    scratch = tempfile.mkdtemp(prefix="ssa-loop-")
    saved = (refresh.FORECASTS, refresh.LOCKS, os.environ.get("SSA_REPLIES_DIR"),
             os.environ.get("SSA_ELICITATION"), harness.requests.post)
    refresh.FORECASTS = os.path.join(scratch, "forecasts")
    refresh.LOCKS = os.path.join(scratch, "locks")
    os.environ["SSA_REPLIES_DIR"] = os.path.join(scratch, "replies")
    os.environ["SSA_ELICITATION"] = "only:"
    harness.requests.post = answer
    try:
        with registry(REG):
            with_key()
            rows, hist = refresh.build_rounds({"season": 0, "rounds": [r]}, series, {}, now)
            assert rows[0]["status"] == "open", rows[0]["status"]
            assert refresh.season_roster() == [("acme-forecast", "agent-api", "participant", "participant")]
            written, failures = refresh.file_baseline_forecasts(rows, hist, now, series)
            assert not failures, failures
            paths = glob.glob(os.path.join(refresh.FORECASTS, r["round_id"], "acme-forecast.json"))
            assert paths, written
            body = json.load(open(paths[0]))
            assert body["topline"] == {"mean": 42.0, "sd": 3.0} and "via=participant" in body["notes"]
    finally:
        (refresh.FORECASTS, refresh.LOCKS, log, eli, harness.requests.post) = saved
        for k, v in (("SSA_REPLIES_DIR", log), ("SSA_ELICITATION", eli)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(scratch, ignore_errors=True)
    print("ok test_the_refresh_loop_files_a_participant_with_our_models_removed")


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


# --- the hour cap and the failure log ---------------------------------------

class trickling:
    """A real endpoint that answers, slowly, forever.

    A fake `requests.post` cannot exercise this: the whole point is that the
    body arrives below the chunk size, so what is being tested is a blocking
    socket read and the thread that interrupts it. So this is a real HTTP
    server on a real loopback port.
    """

    def __init__(self, piece=b'{"mean": 5', every=0.05):
        import http.server
        import socketserver
        import threading
        import time

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                for _ in range(10000):
                    try:
                        self.wfile.write(piece)
                        self.wfile.flush()
                        time.sleep(every)
                    except Exception:              # the arena hung up
                        return

            def log_message(self, *a):
                pass

        self.server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/forecast"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def test_a_trickling_endpoint_is_cut_off_at_the_deadline():
    """One call may take an hour, not a day.

    `TIMEOUT`'s read half is per socket read, so an endpoint sending a few bytes
    at a time resets it forever and holds a filing worker until the six-hour job
    limit. The cap is wall-clock, and the bytes that did arrive are kept: a
    truncated body is how an operator tells a slow endpoint from a proxy error
    page.
    """
    import time

    with_key()
    saved = harness.CALL_DEADLINE_SECONDS
    harness.CALL_DEADLINE_SECONDS = 1.0
    try:
        with trickling() as endpoint:
            started = time.monotonic()
            try:
                harness._call_agent({}, endpoint.url, "", "acme", "{}")
                raise AssertionError("a never-ending reply was accepted")
            except harness.PartialReply as err:
                took = time.monotonic() - started
                assert took < 5, f"cut off after {took:.1f}s, cap was 1s"
                assert err.partial, "the bytes that arrived were thrown away"
                assert b'{"mean"' in err.partial, err.partial[:40]
    finally:
        harness.CALL_DEADLINE_SECONDS = saved
    print("ok test_a_trickling_endpoint_is_cut_off_at_the_deadline")


def test_a_failed_call_is_written_down_and_can_never_be_replayed():
    """Every failure lands in `replies/<round>/failures/`, with whatever body
    had arrived -- and can never come back as an answer.

    The runner is deleted minutes after the run, so an exception that only
    reached its log is gone. Two properties are asserted together because the
    second is what makes the first safe: the record is durable, and it carries
    no `reply` key and lives where `replies.lookup` cannot address it, so the
    thing that replays paid replies can never replay a failure as a forecast.
    """
    from ssa import replies

    saved_dir = os.environ.get("SSA_REPLIES_DIR")
    tmp = tempfile.mkdtemp(prefix="ssa-failures-")
    os.environ["SSA_REPLIES_DIR"] = tmp
    saved = (harness.CALL_DEADLINE_SECONDS, harness.call_provider,
             harness.route, harness.standby_route, harness.call_identity,
             harness.model_id)
    harness.CALL_DEADLINE_SECONDS = 1.0
    try:
        with trickling() as endpoint:
            harness.call_identity = lambda e, via=None: f"endpoint @ {endpoint.url}"
            harness.model_id = lambda e, via=None: "endpoint"
            harness.route = lambda e, via=None: {
                "base": endpoint.url, "via": "participant",
                "env": "SSA_NO_SUCH_KEY", "model": "endpoint"}
            harness.standby_route = lambda e: None
            harness.call_provider = lambda e, prompt, with_usage=False, \
                context=None, via=None: harness._call_agent(
                    {}, endpoint.url, "", "endpoint", prompt)
            with_key()
            prompt = '{"round":"r1"}'
            for _ in range(2):
                try:
                    harness._ask("acme-forecast", prompt, None, "r1")
                    raise AssertionError("a cut-off reply was filed")
                except harness.PartialReply:
                    pass

            ih = harness.prompt_hash("acme-forecast", prompt)
            got = json.load(open(replies.failure_path("r1", "acme-forecast", ih)))
            assert got["attempts_total"] == 2, got["attempts_total"]
            first = got["attempts"][0]
            assert first["error_type"] == "PartialReply", first
            assert '{"mean"' in first["partial"], first
            assert first["partial_bytes"] > 0, first
            assert "reply" not in first, "a failure was stored as a reply"
            assert replies.lookup("r1", "acme-forecast", ih) is None, \
                "the reply log can see a failure record"
            assert replies.failures("r1", "acme-forecast", ih), "not readable"
    finally:
        (harness.CALL_DEADLINE_SECONDS, harness.call_provider, harness.route,
         harness.standby_route, harness.call_identity,
         harness.model_id) = saved
        shutil.rmtree(tmp, ignore_errors=True)
        if saved_dir is None:
            os.environ.pop("SSA_REPLIES_DIR", None)
        else:
            os.environ["SSA_REPLIES_DIR"] = saved_dir
    print("ok test_a_failed_call_is_written_down_and_can_never_be_replayed")


if __name__ == "__main__":
    test_the_registration_carries_no_credential_and_cannot_name_one()
    test_a_participant_route_is_https_only()
    test_revocation_stops_the_call_and_the_roster()
    test_without_a_signing_key_no_participant_is_called()
    test_a_participant_request_is_signed_over_the_bytes_sent()
    test_a_participant_has_no_standby_and_no_base_override()
    test_every_round_shape_builds_a_valid_envelope()
    test_the_envelope_states_the_moment_an_answer_stops_counting()
    test_the_request_id_is_stable_so_a_retry_is_the_same_question()
    test_a_reply_that_is_not_the_contract_is_refused()
    test_an_endpoint_answers_in_one_shape_and_it_is_the_filed_one()
    test_a_forecast_is_stored_as_it_was_answered()
    test_the_round_tells_a_participant_what_it_will_refuse()
    test_the_starter_server_answers_all_three_shapes_from_the_envelope_alone()
    test_a_profile_reply_is_all_cells_or_none()
    test_a_participant_forecast_is_never_mocked()
    test_filing_goes_through_the_shared_runner_and_caches_like_one()
    test_every_shape_files_through_the_real_transport_and_validates()
    test_the_refresh_loop_files_a_participant_with_our_models_removed()
    test_a_registration_without_a_route_is_unchanged()
    test_a_reply_over_the_size_cap_is_refused_unread()
    test_an_endpoint_failing_three_times_is_not_called_again_this_run()
    test_a_trickling_endpoint_is_cut_off_at_the_deadline()
    test_a_failed_call_is_written_down_and_can_never_be_replayed()
    print("24 passed")
