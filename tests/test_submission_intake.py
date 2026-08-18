"""Contracts and static prototype checks for the Issue #42 intake design."""
import copy
import json
import os
import unittest

from jsonschema import Draft7Validator, FormatChecker


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_json(path):
    with open(os.path.join(ROOT, path)) as f:
        return json.load(f)


def participant_schema():
    schema = load_json("schema/participant-intake.schema.json")
    # Resolve the one local reference explicitly. It keeps this test independent
    # of jsonschema's resolver API while still checking the real forecast
    # contract embedded in the intake packet.
    schema = copy.deepcopy(schema)
    file_delivery = schema["properties"]["delivery"]["oneOf"][1]
    file_delivery["properties"]["forecast"] = load_json(
        "schema/forecast.schema.json")
    return schema


def errors(schema, body):
    validator = Draft7Validator(schema, format_checker=FormatChecker())
    return sorted(validator.iter_errors(body), key=lambda e: list(e.path))


class SubmissionIntakeContracts(unittest.TestCase):
    def setUp(self):
        self.participant = participant_schema()
        self.human = load_json("schema/human-intake.schema.json")
        self.forecast = {
            "round_id": "yougov-2026-w35-approval",
            "entrant": "acme-agent",
            "topline": {"mean": 39.5, "sd": 1.4},
            "notes": "Acme Agent v3, prospective run",
        }
        self.profile = {
            "participant_type": "startup",
            "organization_name": "Acme Labs",
            "product_name": "Acme Agent",
            "product_description": "A prospective public-opinion forecasting agent.",
            "entrant_id": "acme-agent",
            "contact": {"name": "Ada Researcher", "email": "ada@example.com"},
            "website": "https://example.com/agent",
            "leaderboard_visibility": "public",
        }

    def assertValid(self, schema, body):
        got = errors(schema, body)
        self.assertEqual([], got, "\n".join(e.message for e in got))

    def test_schemas_are_valid_draft7(self):
        Draft7Validator.check_schema(self.participant)
        Draft7Validator.check_schema(self.human)

    def test_hosted_api_is_one_valid_delivery_method(self):
        body = dict(self.profile, delivery={
            "method": "hosted_api",
            "endpoint": "https://api.example.com/v1/forecast",
            "auth_mode": "bearer",
            "credential_supplied": True,
            "agent_version": "v2026.08",
            "integration_notes": "POST one round and return one forecast object.",
        })
        self.assertValid(self.participant, body)

    def test_file_and_commitment_are_one_valid_delivery_method(self):
        body = dict(self.profile, delivery={
            "method": "file_commitment",
            "round_id": self.forecast["round_id"],
            "forecast": self.forecast,
            "commitment": {
                "accepted": True,
                "signer_name": "Ada Researcher",
                "signer_role": "Founder",
                "signed_at": "2026-08-18T12:00:00Z",
                "terms_version": "ssa-participant-v1",
            },
        })
        self.assertValid(self.participant, body)

    def test_delivery_methods_cannot_be_combined(self):
        body = dict(self.profile, delivery={
            "method": "hosted_api",
            "endpoint": "https://api.example.com/v1/forecast",
            "auth_mode": "none",
            "agent_version": "v1",
            "integration_notes": "Return the arena forecast schema.",
            "round_id": self.forecast["round_id"],
            "forecast": self.forecast,
        })
        self.assertTrue(errors(self.participant, body))

    def test_public_packet_has_no_api_key_field(self):
        text = json.dumps(self.participant)
        self.assertNotIn('"api_key"', text)
        self.assertNotIn('"credential"', text)
        self.assertIn('"credential_supplied"', text)

    def test_private_leaderboard_identity_is_explicit(self):
        body = dict(self.profile, leaderboard_visibility="private", delivery={
            "method": "hosted_api",
            "endpoint": "https://api.example.com/v1/forecast",
            "auth_mode": "none",
            "agent_version": "v1",
            "integration_notes": "Return the arena forecast schema.",
        })
        self.assertValid(self.participant, body)

    def test_human_questionnaire_contract(self):
        body = {
            "username": "forecast-fan",
            "email": "human@example.com",
            "round_id": "yougov-2026-w35-approval",
            "topline": {"mean": 40.0, "sd": 2.0},
            "notes": "Recent releases look stable.",
            "consent": {"accepted": True, "terms_version": "ssa-human-v1"},
        }
        self.assertValid(self.human, body)
        body["topline"]["sd"] = 0
        self.assertTrue(errors(self.human, body))


class SubmissionPrototype(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "site", "submit.html")) as f:
            cls.page = f.read()
        with open(os.path.join(ROOT, "site", "index.html")) as f:
            cls.index = f.read()

    def test_page_contains_every_required_track_and_field(self):
        for marker in (
                'id="track-organization"', 'id="track-human"',
                'name="participant_type"', 'name="organization_name"',
                'name="product_name"', 'name="leaderboard_visibility"',
                'value="hosted_api"', 'value="file_commitment"',
                'name="api_key"', 'name="forecast_file"',
                'name="commitment_accept"', 'name="username"',
                'name="email"'):
            self.assertIn(marker, self.page)

    def test_secret_input_is_password_and_prototype_does_not_post(self):
        self.assertIn('id="api-key" name="api_key" type="password"', self.page)
        self.assertNotIn('<form action=', self.page)
        self.assertIn('It does not transmit, upload, or retain any value', self.page)
        self.assertNotIn('localStorage', self.page)

    def test_arena_links_open_the_new_intake(self):
        self.assertIn("submissionLink('organization', r.round_id)", self.index)
        self.assertIn("submissionLink('human', next.round_id)", self.index)
        self.assertNotIn("issues/new?template=human-forecast", self.index)
        self.assertNotIn("/new/main?filename=", self.index)


if __name__ == "__main__":
    unittest.main()
