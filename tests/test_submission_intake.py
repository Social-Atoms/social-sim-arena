"""Contracts and static prototype checks for the Issue #42 intake design."""
import json
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
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "site", "submit.html")) as f:
            cls.page = f.read()
        with open(os.path.join(ROOT, "site", "index.html")) as f:
            cls.index = f.read()
        with open(os.path.join(ROOT, "ssa", "refresh.py")) as f:
            cls.refresh = f.read()
        cls.index_submit = cls.index.split(
            '<div class="page" id="page-submit">', 1)[1].split(
                '<div class="page" id="page-exam">', 1)[0]

    def test_page_contains_two_tracks_and_required_fields(self):
        for marker in (
                'id="track-agent"', 'id="track-human"',
                'name="participant_type"', 'name="organization_name"',
                'name="product_name"', 'name="contact_name"',
                'name="contact_email"', 'name="openai_compatible_url"',
                'name="api_key"', 'value="openai_compatible_api"',
                'value="questionnaire_commitment"',
                'name="commitment_accept"', 'name="username"',
                'id="agent-questionnaire"', 'id="human-questionnaire"',
                'id="api-test"', 'id="human-board-picker"',
                'id="human-save"', 'data-board="topline"',
                'data-board="profile"', 'data-board="ranking"',
                'name="publication_consent"'):
            self.assertIn(marker, self.page)

    def test_questionnaires_render_live_questions_with_type_presets(self):
        self.assertNotIn("<textarea", self.page.lower())
        self.assertIn("fetch('data.json'", self.page)
        self.assertIn("round.question", self.page)
        self.assertIn("round.target_type", self.page)
        for marker in (
                "continuous_normal", "Expected value", "Uncertainty (SD)",
                "binary_probability", "Probability of Yes (%)",
                "One point estimate", ">Yes</span>", ">No</span>",
                "multiple_choice", "choice-grid", "short_answer",
                "One concise line", "ranking_list", "ranking-item",
                "profile_energy", "profile-mean", "profile-sd", "profile-value",
                "data-unsupported"):
            self.assertIn(marker, self.page)
        self.assertIn("answers:collectAnswers('agent')", self.page)
        self.assertIn("answers:collectAnswers('human')", self.page)
        self.assertIn('row["target_type"] = r.get("target_type", "continuous_normal")',
                      self.refresh)
        self.assertIn('for k in ("cells", "options")', self.refresh)

    def test_custom_participant_picker_replaces_native_select(self):
        self.assertNotIn("<select", self.page.lower())
        self.assertIn('role="combobox"', self.page)
        self.assertIn('role="listbox"', self.page)
        self.assertEqual(4, self.page.count('class="select-option"'))
        for key in ("ArrowDown", "ArrowUp", "Enter", "Escape", "Home", "End"):
            self.assertIn(key, self.page)

    def test_secret_is_password_and_only_the_explicit_probe_transmits_it(self):
        self.assertIn('id="api-key" name="api_key" type="password"', self.page)
        self.assertIn('type="url" pattern="https://.*" required', self.page)
        self.assertNotIn('<form action=', self.page)
        self.assertIn('Test connection', self.page)
        self.assertIn("fetch(chatCompletionUrl(urlInput.value)", self.page)
        self.assertIn("localStorage.setItem(HUMAN_DRAFT_KEY", self.page)
        self.assertNotIn("human-email').value", self.page.split(
            "localStorage.setItem(HUMAN_DRAFT_KEY", 1)[0].split(
                "byId('human-save')", 1)[1])
        self.assertIn("credential_supplied:Boolean(byId('api-key').value)", self.page)
        self.assertIn("probe_status:'passed'", self.page)
        self.assertIn("accepted:byId('agent-publication-consent').checked", self.page)
        self.assertIn("accepted:byId('human-publication-consent').checked", self.page)

    def test_human_is_one_board_with_a_frozen_manifest_and_own_contract(self):
        self.assertIn("questionsForTrack('human')", self.page)
        self.assertIn("board_id:selectedHumanBoard", self.page)
        self.assertIn("round_manifest:[...humanManifest]", self.page)
        self.assertIn("response=track==='human'", self.page)
        self.assertIn("{choice:card.querySelector", self.page)
        with open(os.path.join(ROOT, "docs", "submission-design.md")) as f:
            design = f.read()
        self.assertIn("human_scoring.py", design)

    def test_index_submit_page_has_exactly_two_submit_paths(self):
        self.assertEqual(2, self.index_submit.count('class="svrow"'))
        self.assertIn('<b>Your predictive agent</b>', self.index_submit)
        self.assertIn(
            'For startups, research groups, institutions, and individual researchers.',
            self.index_submit)
        self.assertIn('<b>Human wisdom</b>', self.index_submit)
        self.assertIn("Humanity's last glory.", self.index_submit)
        self.assertEqual(2, self.index_submit.count('>Submit</a>'))

    def test_arena_links_open_the_new_single_page_tracks(self):
        self.assertIn("submissionLink('agent')", self.index)
        self.assertIn("submissionLink('human')", self.index)
        self.assertNotIn("submissionLink('organization'", self.index)
        self.assertNotIn("?round=", self.index)


if __name__ == "__main__":
    unittest.main()
