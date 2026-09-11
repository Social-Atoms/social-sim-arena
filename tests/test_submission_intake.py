"""Contracts and static prototype checks for the Issue #42 intake design."""
import json
import re
import os
import unittest
from unittest.mock import patch

from jsonschema import Draft7Validator, FormatChecker

from ssa import harness


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_json(path):
    with open(os.path.join(ROOT, path)) as f:
        return json.load(f)


def load_text(path):
    with open(os.path.join(ROOT, path)) as f:
        return f.read()


def errors(schema, body):
    validator = Draft7Validator(schema, format_checker=FormatChecker())
    return sorted(validator.iter_errors(body), key=lambda error: list(error.path))


class SubmissionIntakeContracts(unittest.TestCase):
    def setUp(self):
        self.participant = load_json("schema/participant-intake.schema.json")
        self.human = load_json("schema/human-intake.schema.json")
        self.agent_api_request = load_json("schema/agent-api-request.schema.json")
        self.agent_api_response = load_json("schema/agent-api-response.schema.json")
        self.profile = {
            "participant_type": "startup",
            "organization_name": "Acme Labs",
            "product_name": "Acme Agent",
            "contact": {"name": "Ada Researcher", "email": "ada@example.com"},
            "publication_consent": {
                "accepted": True,
                "fields": ["organization_name", "product_name"],
                "terms_version": "ssa-publication-v1",
            },
        }
        self.answer = {
            "round_id": "yougov-2026-w35-approval",
            "target_type": "continuous_normal",
            "response": {"mean": 40.5, "sd": 2.1},
        }

    def assertValid(self, schema, body):
        got = errors(schema, body)
        self.assertEqual([], got, "\n".join(error.message for error in got))

    def test_schemas_are_valid_draft7(self):
        Draft7Validator.check_schema(self.participant)
        Draft7Validator.check_schema(self.human)
        Draft7Validator.check_schema(self.agent_api_request)
        Draft7Validator.check_schema(self.agent_api_response)

    def test_openai_compatible_api_is_one_valid_route(self):
        body = dict(self.profile, delivery={
            "method": "openai_compatible_api",
            "endpoint": "https://api.example.com/v1",
            "credential_supplied": True,
            "contract_version": "ssa-agent-api-v2",
            "probe_status": "passed",
        })
        self.assertValid(self.participant, body)

    def test_questionnaire_and_commitment_are_one_valid_route(self):
        body = dict(self.profile, delivery={
            "method": "questionnaire_commitment",
            "answers": [self.answer],
            "commitment": {
                "accepted": True,
                "terms_version": "ssa-participant-v1",
            },
        })
        self.assertValid(self.participant, body)

    def test_routes_cannot_be_combined(self):
        body = dict(self.profile, delivery={
            "method": "openai_compatible_api",
            "endpoint": "https://api.example.com/v1",
            "credential_supplied": False,
            "contract_version": "ssa-agent-api-v2",
            "probe_status": "passed",
            "answers": [self.answer],
        })
        self.assertTrue(errors(self.participant, body))

    def test_api_endpoint_must_be_https(self):
        body = dict(self.profile, delivery={
            "method": "openai_compatible_api",
            "endpoint": "http://api.example.com/v1",
            "credential_supplied": False,
            "contract_version": "ssa-agent-api-v2",
            "probe_status": "passed",
        })
        self.assertTrue(errors(self.participant, body))

    def test_public_packet_has_no_api_key_field(self):
        text = json.dumps(self.participant)
        self.assertNotIn('"api_key"', text)
        self.assertNotIn('"credential"', text)
        self.assertIn('"credential_supplied"', text)

    def test_publication_consent_records_a_real_choice(self):
        body = dict(self.profile, delivery={
            "method": "openai_compatible_api",
            "endpoint": "https://api.example.com/v1",
            "credential_supplied": False,
            "contract_version": "ssa-agent-api-v2",
            "probe_status": "passed",
        })
        body["publication_consent"] = dict(
            body["publication_consent"], accepted=False)
        self.assertValid(self.participant, body)
        del body["publication_consent"]
        self.assertTrue(errors(self.participant, body))

    def test_human_wisdom_questionnaire_contract(self):
        answer = {
            "round_id": "yougov-2026-w35-approval",
            "target_type": "continuous_normal",
            "response": {"value": 40.5},
        }
        body = {
            "submission_version": 1,
            "board_id": "topline",
            "round_manifest": [answer["round_id"]],
            "username": "forecast-fan",
            "contact_email": "human@example.com",
            "answers": [answer],
            "commitment": {
                "accepted": True,
                "terms_version": "ssa-participant-v1",
            },
            "publication_consent": {
                "accepted": True,
                "field": "username",
                "terms_version": "ssa-publication-v1",
            },
        }
        self.assertValid(self.human, body)
        body["answers"][0]["response"]["sd"] = 2
        self.assertTrue(errors(self.human, body))

    def test_each_question_type_has_a_structured_answer_contract(self):
        samples = [
            {"round_id": "numeric-round", "target_type": "continuous_normal",
             "response": {"value": 40.5}},
            {"round_id": "binary-round", "target_type": "binary_probability",
             "response": {"choice": "Yes"}},
            {"round_id": "choice-round", "target_type": "multiple_choice",
             "response": {"choice": "Option A"}},
            {"round_id": "short-round", "target_type": "short_answer",
             "response": {"text": "One bounded line"}},
            {"round_id": "ranking-round", "target_type": "ranking_list",
             "response": {"ranking": ["First", "Second", "Third"]}},
            {"round_id": "profile-round", "target_type": "profile_energy",
             "response": {"profile": {
                 "cell_one": -4.0,
                 "cell_two": 7.5,
             }}},
        ]
        body = {
            "submission_version": 1,
            "board_id": "topline",
            "round_manifest": [answer["round_id"] for answer in samples],
            "username": "forecast-fan",
            "contact_email": "human@example.com",
            "answers": samples,
            "commitment": {
                "accepted": True,
                "terms_version": "ssa-participant-v1",
            },
            "publication_consent": {
                "accepted": False,
                "field": "username",
                "terms_version": "ssa-publication-v1",
            },
        }
        self.assertValid(self.human, body)

    def test_agent_api_starter_fixtures_match_the_versioned_contract(self):
        # The fixtures are the bodies as they travel: no wrapper on either side.
        prompt = load_json("examples/agent-api/request.json")
        self.assertValid(self.agent_api_request, prompt)

        content = load_json("examples/agent-api/response.json")
        self.assertValid(self.agent_api_response, content)
        self.assertIn("reasoning_trace", content)
        self.assertIn("crosstabs", content)

    def test_the_arena_signs_the_envelope_it_sends(self):
        from ssa import signing
        calls = []

        class Response:
            status_code = 200
            content = (b'{"schema_version": "ssa-agent-api-v2", '
                       b'"forecast": {"mean": 50.0, "sd": 5.0}}')

        def fake_post(url, **kwargs):
            calls.append((url, kwargs))
            return Response()

        private, public = signing.generate()
        real_post = harness.requests.post
        harness.requests.post = fake_post
        try:
            with patch.dict(os.environ, {signing.LIVE_KEY_ENV: private}):
                text, _ = harness._call_agent({}, "https://agent.example/forecast",
                                              "", "acme", '{"round": 1}')
        finally:
            harness.requests.post = real_post
        # the exact URL, the envelope as the whole body, a signature that
        # verifies with the public key over those bytes, and no bearer token
        self.assertEqual("https://agent.example/forecast", calls[0][0])
        self.assertEqual(b'{"round": 1}', calls[0][1]["data"])
        self.assertNotIn("Authorization", calls[0][1]["headers"])
        self.assertEqual("ssa-live", signing.verify(public, calls[0][1]["headers"], b'{"round": 1}'))
        self.assertEqual({"mean": 50.0, "sd": 5.0}, json.loads(text)["forecast"])

class SubmissionPrototype(unittest.TestCase):
    """The submit page is agents only.

    It follows the shape a forecaster expects from an onboarding page -- how
    it works, test your endpoint, register -- and the things it must not do
    are the things a browser page is worst at: hold a secret, invent a
    registration field, or transmit anything the participant did not ask for.
    """
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "site", "submit.html")) as f:
            cls.page = f.read()
        with open(os.path.join(ROOT, "site", "index.html")) as f:
            cls.index = f.read()
        with open(os.path.join(ROOT, "site", "leaderboard.html")) as f:
            cls.board = f.read()
        with open(os.path.join(ROOT, "site", "docs.html")) as f:
            cls.docs = f.read()
        with open(os.path.join(ROOT, "ssa", "refresh.py")) as f:
            cls.refresh = f.read()
        cls.index_submit = cls.index.split(
            '<div class="page" id="page-submit">', 1)[1].split(
                '<div class="page" id="page-exam">', 1)[0]

    def test_page_is_agents_only_and_has_no_form_to_fill_in(self):
        # Two panels, in Prophet Arena's order: test the API, then the model
        # information, and one submit button that opens the pull request.
        for marker in ('<h3>Test your API</h3>', 'id="api-test"', 'name="endpoint_url"',
                       '>Forecast endpoint URL <', '>Entrant id <', 'id="entrant-id"',
                       '<h3>Your details</h3>', '>Display name <', '>Company / organization <',
                       'id="entrant-org"', 'id="entrant-github"', 'id="reg-json"',
                       '>Submit for review</a>', '<h3>Test results</h3>', '<h3>API response</h3>'):
            self.assertIn(marker, self.page)
        # Gone: the second route, the type radio, the method line, the
        # calendar, and everything from the questionnaire era.
        for gone in ('id="route-b"', "Route B", "entrant_type", 'id="entrant-method"',
                     'id="calendar"', "loadCalendar", "bundle",
                     "track-human", "Human wisdom", "Human Wisdom",
                     "human_questionnaire_mode", "Fill in this form",
                     "questionnaire_commitment", "<textarea", "<select"):
            self.assertNotIn(gone, self.page)

    def test_how_it_works_shows_the_starter_fixture_verbatim(self):
        with open(os.path.join(ROOT, "examples", "agent-api", "request.json")) as f:
            request = json.load(f)
        with open(os.path.join(ROOT, "examples", "agent-api", "response.json")) as f:
            response = json.load(f)
        # The page's fixture is the starter kit's fixture with a browser
        # request id; the round is byte-identical.
        inner = request
        shown = self.page.split("const FIXTURE = ", 1)[1].split(";", 1)[0]
        self.assertIn("round_id:'ssa-contract-test'", shown)
        self.assertIn("schema_version:'ssa-agent-api-v2'", shown)
        self.assertIn(inner["round"]["question"], shown)
        self.assertEqual("ssa-agent-api-v2", response["schema_version"])
        self.assertIn('"forecast": {"mean": 50.0, "sd": 5.0}', self.page)
        for shape in ("Topline", "Population", "Ranking"):
            self.assertIn('<div class="shape"><b>' + shape + "</b>", self.page)

    def test_the_page_asks_for_no_key_and_only_the_explicit_probe_sends_anything(self):
        # Season 0 Route A carries no credential in either direction: the
        # arena signs, the participant verifies. A key field would let an
        # endpoint pass the browser test behind a key the cron never sends.
        for token in ('id="api-key"', 'api_key', 'Bearer'):
            self.assertNotIn(token, self.page)
        self.assertIn('type="url" pattern="https://.*" required', self.page)
        self.assertNotIn("<form action=", self.page)
        self.assertIn("const target = endpointUrl(urlInput.value);", self.page)
        # Every other fetch on the page is the arena's own data, never a
        # third party carrying what was typed.
        fetches = re.findall(r"fetch\(([^,)]+)", self.page)
        self.assertEqual(fetches, ["target"])
        self.assertNotIn("localStorage", self.page)

    def test_the_browser_test_signs_with_the_published_test_key(self):
        from ssa import signing
        self.assertIn("seed:'" + signing.TEST_PRIVATE_KEY + "'", self.page)
        self.assertIn("{name:'Ed25519'}", self.page)
        self.assertIn("const signed = await signHeaders(bodyText);", self.page)
        self.assertIn("body:bodyText", self.page, "the signed bytes must be the sent bytes")
        with open(os.path.join(ROOT, "site", "keys.json")) as f:
            keys = json.load(f)
        test = next(k for k in keys["keys"] if k["key_id"] == signing.TEST_KEY_ID)
        self.assertEqual(signing.TEST_PUBLIC_KEY, test["public_key"])
        self.assertEqual(signing.TEST_PRIVATE_KEY, test["private_key"])

    def test_registration_is_a_pull_request_by_its_owner_with_no_secret_in_it(self):
        builder = self.page.split("function registration(){", 1)[1].split(
            "function syncRegistration(){", 1)[0]
        self.assertNotIn("api-key", builder)
        self.assertNotIn("key", builder.lower().replace("kind", ""))
        self.assertIn("kind:'agent_api'", builder)
        self.assertIn("if (github) reg.github = github;", builder)
        self.assertIn("'/new/main?filename='", self.page)
        self.assertIn("encodeURIComponent('entrants/'+reg.entrant_id+'.json')", self.page)
        self.assertIn("const ready = endpointOk && idOk && ghOk", self.page)
        self.assertNotIn("/api/v1/registrations", self.page)
        self.assertIn('pattern="[a-z0-9][a-z0-9_.-]{1,47}"', self.page)
        with open(os.path.join(ROOT, "schema", "entrant.schema.json")) as f:
            schema = json.load(f)
        self.assertEqual(schema["properties"]["entrant_id"]["pattern"],
                         "^[a-z0-9][a-z0-9_.-]{1,47}$")

    def test_a_browser_side_block_does_not_lock_a_working_endpoint_out(self):
        """Most API servers answer no CORS preflight, so the browser refuses to
        send the test POST. That is the browser's rule; the cron is
        server-to-server and never preflights. If that state locked the submit
        button, a working endpoint could not register from this page at all."""
        self.assertIn("let browserBlocked = false;", self.page)
        self.assertIn("browserBlocked = true;", self.page)
        self.assertIn("const endpointOk = (apiProbePassed || browserBlocked)", self.page)
        self.assertIn("Blocked by the browser, not by us", self.page)
        self.assertIn("tools/probe_agent_api.py --url", self.page)
        # And the starter server everyone copies answers the preflight, so the
        # page works for anyone who follows the example.
        server = load_text("examples/agent-api/server.py")
        self.assertIn("def do_OPTIONS(self):", server)
        self.assertIn('"Access-Control-Allow-Origin", "*"', server)
        deployed = load_text("api/example_agent.py")
        self.assertIn("def do_OPTIONS(self):", deployed)
        self.assertIn('"Access-Control-Allow-Origin", "*"', deployed)

    def test_the_registration_file_is_the_prophet_arena_shape_with_a_github_owner(self):
        builder = self.page.split("function registration(){", 1)[1].split(
            "function syncRegistration(){", 1)[0]
        for field in ("entrant_id:", "name:", "organization:", "type: 'participant'",
                      "reg.contact = contact", "reg.github = github", "kind:'agent_api'"):
            self.assertIn(field, builder)
        self.assertNotIn("method", builder)
        schema = load_json("schema/entrant.schema.json")
        self.assertIn("participant", schema["properties"]["type"]["enum"])
        self.assertIn("organization", schema["properties"])
        self.assertNotIn("method", schema["required"])

    def test_the_rest_of_the_site_sends_agents_to_one_page_and_nobody_else(self):
        self.assertEqual(2, self.index_submit.count('class="svrow'))   # agent forecaster, human forecaster
        self.assertIn("<b>Agent forecaster ", self.index_submit)
        self.assertIn("<b>Human forecaster", self.index_submit)
        for page in (self.index, self.board, self.docs):
            self.assertNotIn("route-b", page)
            self.assertNotIn("weekly-route", page)
        for page in (self.index_submit, self.board, self.docs):
            self.assertNotIn("Human Wisdom", page)
            self.assertNotIn("Human wisdom", page)
        self.assertNotIn("submissionLink('human')", self.index)
        self.assertNotIn("submissionLink('human')", self.board)
        self.assertIn("submissionLink('agent')", self.index)
        self.assertNotIn("?round=", self.index)

    def test_optional_private_audit_record_stays_in_the_schema(self):
        """A participant may attach a redacted reasoning summary, a tool-call
        log or a trace URL to a questionnaire submission. It is private audit
        material, never a scoring input, and it is optional: the page says so
        and the schema accepts a submission with and without it."""
        body = dict(participant_type="startup", organization_name="Acme Labs",
                    product_name="Acme Agent",
                    contact={"name": "Ada Researcher", "email": "ada@example.com"},
                    publication_consent={"accepted": True,
                                         "fields": ["organization_name", "product_name"],
                                         "terms_version": "ssa-publication-v1"},
                    audit_trail={
                        "consent": {"accepted": True, "terms_version": "ssa-audit-v1"},
                        "reasoning_summary": "Used the latest released tracker as an anchor.",
                        "tool_call_log": "2026-09-01T12:00Z fetch tracker archive",
                        "trace_url": "https://example.com/private/run/42"},
                    delivery={
                        "method": "questionnaire_commitment",
                        "answers": [{"round_id": "yougov-2026-w35-approval",
                                     "target_type": "continuous_normal",
                                     "response": {"mean": 40.5, "sd": 2.1}}],
                        "commitment": {"accepted": True, "terms_version": "ssa-participant-v1"}})
        schema = load_json("schema/participant-intake.schema.json")
        self.assertEqual([], errors(schema, body))
        body.pop("audit_trail")
        self.assertEqual([], errors(schema, body))

    def test_the_pipeline_still_publishes_what_the_page_reads(self):
        self.assertIn('row["target_type"] = r.get("target_type", "continuous_normal")',
                      self.refresh)
        self.assertIn('for k in ("cells", "options")', self.refresh)


if __name__ == "__main__":
    unittest.main()
