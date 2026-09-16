"""Issue-to-PR participant intake contracts.

Run: PYTHONPATH=. python tests/test_participant_issue.py
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools import participant_issue as intake

ROOT = Path(__file__).resolve().parents[1]


def request_body(payload):
    return (intake.MARKER + "\n## Participant request\n\n```json\n" +
            json.dumps(payload, indent=2) + "\n```\n")


def event(payload, author="owner", labels=(), association="NONE"):
    return {"issue": {
        "number": 42,
        "body": request_body(payload),
        "user": {"login": author},
        "author_association": association,
        "labels": [{"name": label} for label in labels],
    }}


def registration(entrant_id="new-agent", github="owner"):
    return {
        "entrant_id": entrant_id,
        "name": "New Agent",
        "organization": "Forecast Lab",
        "type": "participant",
        "github": github,
        "route": {"kind": "agent_api", "url": "https://agent.example/forecast"},
    }


class Repo:
    def __enter__(self):
        self.path = Path(tempfile.mkdtemp(prefix="ssa-participant-issue-"))
        (self.path / "entrants").mkdir()
        (self.path / "schema").mkdir()
        for name in ("entrant.schema.json", "participant-request.schema.json",
                     "reserved-entrant-ids.json"):
            shutil.copy(ROOT / "schema" / name, self.path / "schema" / name)
        existing = registration("acme", "owner")
        existing.update({"name": "Acme v1", "homepage": "https://acme.example",
                         "status": "active"})
        (self.path / "entrants" / "acme.json").write_text(
            json.dumps(existing, indent=2) + "\n")
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        self.saved_root = intake.ROOT
        self.saved_submission_root = intake.submission.ROOT
        self.saved_reserved = intake.submission.RESERVED_FILE
        intake.ROOT = self.path
        intake.submission.ROOT = str(self.path)
        intake.submission.RESERVED_FILE = str(
            self.path / "schema" / "reserved-entrant-ids.json")
        return self

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.path, check=True,
                              capture_output=True, text=True)

    def __exit__(self, *exc):
        intake.ROOT = self.saved_root
        intake.submission.ROOT = self.saved_submission_root
        intake.submission.RESERVED_FILE = self.saved_reserved
        shutil.rmtree(self.path, ignore_errors=True)


class ParticipantIssueIntake(unittest.TestCase):
    def test_a_new_registration_waits_for_a_maintainer_label(self):
        payload = {"schema_version": "ssa-participant-request-v1",
                   "operation": "register", "entrant": registration()}
        with Repo():
            pending = intake.decide(event(payload), "main")
            approved = intake.decide(
                event(payload, labels=[intake.APPROVAL_LABEL]), "main")
        self.assertFalse(pending.ready)
        self.assertTrue(approved.ready)
        self.assertEqual("entrants/new-agent.json", approved.target)

    def test_registration_identity_is_the_issue_author(self):
        payload = {"schema_version": "ssa-participant-request-v1",
                   "operation": "register", "entrant": registration(github="someone-else")}
        with Repo(), self.assertRaisesRegex(intake.IntakeError, "opened by `@owner`"):
            intake.decide(event(payload), "main")

    def test_an_existing_id_cannot_be_registered_again(self):
        payload = {"schema_version": "ssa-participant-request-v1",
                   "operation": "register", "entrant": registration("acme")}
        with Repo(), self.assertRaisesRegex(intake.IntakeError, "already registered"):
            intake.decide(event(payload), "main")

    def test_update_merges_only_editable_fields_and_preserves_owner_and_status(self):
        payload = {
            "schema_version": "ssa-participant-request-v1",
            "operation": "update",
            "entrant_id": "acme",
            "changes": {
                "name": "Acme v2",
                "organization": "New Lab",
                "contact": None,
                "route": {"kind": "agent_api", "url": "https://new.example/forecast"},
            },
        }
        with Repo():
            decision = intake.decide(event(payload), "main")
        self.assertTrue(decision.ready)
        self.assertEqual("owner", decision.document["github"])
        self.assertEqual("active", decision.document["status"])
        self.assertEqual("https://acme.example", decision.document["homepage"])
        self.assertNotIn("contact", decision.document)
        self.assertEqual("https://new.example/forecast",
                         decision.document["route"]["url"])

    def test_only_the_current_owner_can_update_or_revoke(self):
        for operation in ("update", "revoke"):
            payload = {"schema_version": "ssa-participant-request-v1",
                       "operation": operation, "entrant_id": "acme"}
            if operation == "update":
                payload["changes"] = {"name": "Nope"}
            with self.subTest(operation=operation), Repo(), \
                    self.assertRaisesRegex(intake.IntakeError, "belongs to `@owner`"):
                intake.decide(event(payload, author="outsider"), "main")

    def test_revoke_keeps_the_record_and_sets_the_off_switch(self):
        payload = {"schema_version": "ssa-participant-request-v1",
                   "operation": "revoke", "entrant_id": "acme"}
        with Repo():
            decision = intake.decide(event(payload), "main")
        self.assertTrue(decision.ready)
        self.assertEqual("revoked", decision.document["status"])
        self.assertEqual("owner", decision.document["github"])
        self.assertIn("route", decision.document)

    def test_the_parser_accepts_one_versioned_json_block_and_nothing_ambiguous(self):
        payload = {"schema_version": "ssa-participant-request-v1",
                   "operation": "revoke", "entrant_id": "acme"}
        self.assertEqual(payload, intake.parse_request(request_body(payload)))
        for bad in ("", intake.MARKER + "\n```json\n{}\n```\n```json\n{}\n```",
                    intake.MARKER + "\n```json\n{nope}\n```"):
            with self.subTest(body=bad), self.assertRaises(intake.IntakeError):
                intake.parse_request(bad)

    def test_a_maintainer_may_handle_an_owner_request_but_still_needs_registration_approval(self):
        update = {"schema_version": "ssa-participant-request-v1",
                  "operation": "update", "entrant_id": "acme",
                  "changes": {"name": "Maintained"}}
        register = {"schema_version": "ssa-participant-request-v1",
                    "operation": "register",
                    "entrant": registration("staff-agent", "some-owner")}
        with Repo():
            changed = intake.decide(event(update, author="staff", association="MEMBER"), "main")
            pending = intake.decide(event(register, author="staff", association="MEMBER"), "main")
        self.assertTrue(changed.ready)
        self.assertFalse(pending.ready)


if __name__ == "__main__":
    unittest.main()
