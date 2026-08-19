"""Contracts and static prototype checks for the Issue #42 intake design."""
import json
import os
import unittest

from jsonschema import Draft7Validator, FormatChecker


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

    def assertValid(self, schema, body):
        got = errors(schema, body)
        self.assertEqual([], got, "\n".join(error.message for error in got))

    def test_schemas_are_valid_draft7(self):
        Draft7Validator.check_schema(self.participant)
        Draft7Validator.check_schema(self.human)

    def test_openai_compatible_api_is_one_valid_route(self):
        body = dict(self.profile, delivery={
            "method": "openai_compatible_api",
            "endpoint": "https://api.example.com/v1",
            "credential_supplied": True,
        })
        self.assertValid(self.participant, body)

    def test_questionnaire_and_commitment_are_one_valid_route(self):
        body = dict(self.profile, delivery={
            "method": "questionnaire_commitment",
            "questionnaire": (
                "We combine public releases with an agent workflow and "
                "document every reproducible forecasting step."
            ),
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
            "questionnaire": "This extra questionnaire must make the route invalid.",
        })
        self.assertTrue(errors(self.participant, body))

    def test_api_endpoint_must_be_https(self):
        body = dict(self.profile, delivery={
            "method": "openai_compatible_api",
            "endpoint": "http://api.example.com/v1",
            "credential_supplied": False,
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
        })
        body["publication_consent"] = dict(
            body["publication_consent"], accepted=False)
        self.assertValid(self.participant, body)
        del body["publication_consent"]
        self.assertTrue(errors(self.participant, body))

    def test_human_wisdom_questionnaire_contract(self):
        body = {
            "username": "forecast-fan",
            "contact_email": "human@example.com",
            "questionnaire": (
                "I compare multiple public sources and record reasons before "
                "making each forecast."
            ),
            "publication_consent": {
                "accepted": True,
                "field": "username",
                "terms_version": "ssa-publication-v1",
            },
        }
        self.assertValid(self.human, body)
        body["questionnaire"] = "too short"
        self.assertTrue(errors(self.human, body))


class SubmissionPrototype(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "site", "submit.html")) as f:
            cls.page = f.read()
        with open(os.path.join(ROOT, "site", "index.html")) as f:
            cls.index = f.read()
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
                'name="questionnaire"', 'name="publication_consent"'):
            self.assertIn(marker, self.page)

    def test_custom_participant_picker_replaces_native_select(self):
        self.assertNotIn("<select", self.page.lower())
        self.assertIn('role="combobox"', self.page)
        self.assertIn('role="listbox"', self.page)
        self.assertEqual(4, self.page.count('class="select-option"'))
        for key in ("ArrowDown", "ArrowUp", "Enter", "Escape", "Home", "End"):
            self.assertIn(key, self.page)

    def test_secret_is_password_and_prototype_does_not_post(self):
        self.assertIn('id="api-key" name="api_key" type="password"', self.page)
        self.assertIn('type="url" pattern="https://.*" required', self.page)
        self.assertNotIn('<form action=', self.page)
        self.assertIn('It does not transmit, upload, or retain any value', self.page)
        self.assertNotIn('localStorage', self.page)
        self.assertIn("credential_supplied:Boolean(byId('api-key').value)", self.page)
        self.assertIn("accepted:byId('agent-publication-consent').checked", self.page)
        self.assertIn("accepted:byId('human-publication-consent').checked", self.page)

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
