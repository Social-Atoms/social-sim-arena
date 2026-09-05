"""Contracts and static prototype checks for the Issue #42 intake design."""
import json
import re
import os
import unittest

from jsonschema import Draft7Validator, FormatChecker

from ssa import harness


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_json(path):
    with open(os.path.join(ROOT, path)) as f:
        return json.load(f)


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
            "contract_version": "ssa-agent-api-v1",
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
            "contract_version": "ssa-agent-api-v1",
            "probe_status": "passed",
            "answers": [self.answer],
        })
        self.assertTrue(errors(self.participant, body))

    def test_api_endpoint_must_be_https(self):
        body = dict(self.profile, delivery={
            "method": "openai_compatible_api",
            "endpoint": "http://api.example.com/v1",
            "credential_supplied": False,
            "contract_version": "ssa-agent-api-v1",
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
            "contract_version": "ssa-agent-api-v1",
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
        request = load_json("examples/agent-api/request.json")
        prompt = json.loads(request["messages"][0]["content"])
        self.assertValid(self.agent_api_request, prompt)

        response = load_json("examples/agent-api/response.json")
        content = json.loads(response["choices"][0]["message"]["content"])
        self.assertValid(self.agent_api_response, content)
        self.assertIn("reasoning_trace", content)
        self.assertIn("crosstabs", content)

    def test_openai_compatible_auth_is_optional(self):
        calls = []

        class Response:
            status_code = 200
            text = ""

            @staticmethod
            def json():
                return {"choices": [{"message": {"content": "ok"}}]}

        def fake_post(url, **kwargs):
            calls.append((url, kwargs))
            return Response()

        real_post = harness.requests.post
        harness.requests.post = fake_post
        try:
            harness._call_openai({}, "https://agent.example/v1", "",
                                 "ssa-agent", "test")
            harness._call_openai({}, "https://agent.example/v1", "secret",
                                 "ssa-agent", "test")
        finally:
            harness.requests.post = real_post
        self.assertEqual({}, calls[0][1]["headers"])
        self.assertEqual("Bearer secret",
                         calls[1][1]["headers"]["Authorization"])


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
        for marker in ('id="api-test"', 'name="openai_compatible_url"',
                       'id="entrant-id"', 'id="entrant-method"',
                       'id="test-results"', 'id="reg-json"', 'id="route-b"'):
            self.assertIn(marker, self.page)
        for gone in ("track-human", "Human wisdom", "Human Wisdom",
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
        inner = json.loads(request["messages"][0]["content"])
        shown = self.page.split("const FIXTURE = ", 1)[1].split(";", 1)[0]
        self.assertIn("round_id:'ssa-contract-test'", shown)
        self.assertIn("schema_version:'ssa-agent-api-v1'", shown)
        self.assertIn(inner["round"]["question"], shown)
        content = json.loads(response["choices"][0]["message"]["content"])
        self.assertEqual("ssa-agent-api-v1", content["schema_version"])
        self.assertIn('\\"forecast\\":{\\"mean\\":50.0,\\"sd\\":5.0}', self.page)
        for shape in ("Topline", "Population", "Ranking"):
            self.assertIn('<div class="shape"><b>' + shape + "</b>", self.page)

    def test_secret_is_password_and_only_the_explicit_probe_transmits_it(self):
        self.assertIn('id="api-key" name="api_key" type="password"', self.page)
        self.assertIn('type="url" pattern="https://.*" required', self.page)
        self.assertNotIn("<form action=", self.page)
        self.assertIn("const target = chatCompletionUrl(urlInput.value);", self.page)
        # Every other fetch on the page is the arena's own data, never a
        # third party carrying what was typed.
        fetches = re.findall(r"fetch\(([^,)]+)", self.page)
        self.assertEqual(
            sorted(fetches),
            sorted(["'data.json'",
                    "'https://raw.githubusercontent.com/Social-Atoms/social-sim-arena/main/site/data.json'",
                    "target"]))
        self.assertNotIn("localStorage", self.page)

    def test_registration_has_no_key_and_opens_as_a_pull_request(self):
        builder = self.page.split("function registration(){", 1)[1].split(
            "function syncRegistration(){", 1)[0]
        self.assertNotIn("api-key", builder)
        self.assertNotIn("key", builder.lower().replace("kind", ""))
        self.assertIn("kind:'agent_api'", builder)
        self.assertIn("'/new/dev?filename='", self.page)
        self.assertIn("encodeURIComponent('entrants/'+reg.entrant_id+'.json')", self.page)
        self.assertIn("const ready = apiProbePassed &&", self.page)
        self.assertIn("There is no field for your API key, and there will not be one.",
                      self.page)
        self.assertIn('pattern="[a-z0-9][a-z0-9_.-]{1,47}"', self.page)
        with open(os.path.join(ROOT, "schema", "entrant.schema.json")) as f:
            schema = json.load(f)
        self.assertEqual(schema["properties"]["entrant_id"]["pattern"],
                         "^[a-z0-9][a-z0-9_.-]{1,47}$")

    def test_the_bundle_route_is_on_the_same_page(self):
        self.assertIn("/api/v1/bundle-submissions", self.page)
        self.assertIn("tools/validate_bundle.py", self.page)
        self.assertIn('href="leaderboard.html#batches"', self.page)

    def test_the_rest_of_the_site_sends_agents_to_one_page_and_nobody_else(self):
        self.assertEqual(2, self.index_submit.count('class="svrow"'))
        self.assertIn("<b>We call your endpoint</b>", self.index_submit)
        self.assertIn("<b>You upload the week's bundle</b>", self.index_submit)
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
        self.assertIn("audit material", self.page)

    def test_the_pipeline_still_publishes_what_the_page_reads(self):
        self.assertIn('row["target_type"] = r.get("target_type", "continuous_normal")',
                      self.refresh)
        self.assertIn('for k in ("cells", "options")', self.refresh)


if __name__ == "__main__":
    unittest.main()
