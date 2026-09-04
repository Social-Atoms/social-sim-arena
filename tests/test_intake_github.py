"""The intake repository backend.

Run: PYTHONPATH=. python tests/test_intake_github.py
"""
import base64
import contextlib
import json
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import requests

from ssa.questionnaire_api import (IdempotencyConflict, StorageUnavailable,
                                   store_submission)

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 8, 20, 12, 9, tzinfo=timezone.utc)
# The local directory is read first, so leaving it set would run this whole
# file against `_store_local` and pass without touching the backend.
ENV = {"INTAKE_REPO_TOKEN": "token", "SUBMISSION_STORAGE_DIR": "",
       "BLOB_READ_WRITE_TOKEN": ""}


def packet(product="Ada Agent"):
    return {"track": "agent", "submission": {"product_name": product}}


def unreachable(*args, **kwargs):
    raise requests.ConnectionError("boom")


class Response:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self.payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class FakeIntake:
    """The contents API's write-once rule: a create carrying no `sha` is
    refused once the path is taken."""

    def __init__(self, refusal=422):
        self.refusal = refusal
        self.files = {}
        self.puts = []

    def path_of(self, url):
        return url.split("/contents/")[1]

    def put(self, url, json=None, headers=None, timeout=None):
        path = self.path_of(url)
        status = self.refusal if path in self.files else 201
        if status == 201:
            self.files[path] = base64.b64decode(json["content"])
        self.puts.append((path, json, status))
        return Response(status)

    def get(self, url, headers=None, timeout=None):
        path = self.path_of(url)
        if path not in self.files:
            return Response(404)
        return Response(200, json.loads(self.files[path]))


@contextlib.contextmanager
def intake(fake):
    """Both verbs are always stubbed, so a missed patch cannot reach GitHub."""
    with patch("requests.put", lambda *a, **k: fake.put(*a, **k)), \
            patch("requests.get", lambda *a, **k: fake.get(*a, **k)), \
            patch.dict(os.environ, ENV, clear=False):
        yield


class IntakeRepositoryBackend(unittest.TestCase):
    def test_a_packet_is_created_under_its_track_directory(self):
        fake = FakeIntake()
        with intake(fake):
            receipt = store_submission(packet(), "test-key-123", NOW)
        path, body, status = fake.puts[0]
        self.assertEqual(f"registrations/{receipt['submission_id']}.json", path)
        self.assertEqual(201, status)
        self.assertFalse(receipt["idempotent_replay"])
        # `json=` cannot serialise the bytes b64encode returns.
        self.assertIsInstance(body["content"], str)
        self.assertEqual(path, body["message"])
        # Sending one would turn the create into an overwrite.
        self.assertNotIn("sha", body)

    def test_a_replay_returns_the_stored_record(self):
        # GitHub documents both codes for this endpoint and never says which
        # one answers a create on a path that is already taken.
        for refusal in (409, 422):
            with self.subTest(refusal=refusal):
                fake = FakeIntake(refusal)
                with intake(fake):
                    first = store_submission(packet(), "test-key-123", NOW)
                    replay = store_submission(packet(), "test-key-123", LATER)
                self.assertTrue(replay["idempotent_replay"])
                self.assertEqual(first["received_at"], replay["received_at"])
                self.assertEqual([201, refusal],
                                 [status for _, _, status in fake.puts])
                self.assertEqual(1, len(fake.files))

    def test_the_same_key_with_different_bytes_is_a_conflict(self):
        fake = FakeIntake()
        with intake(fake):
            store_submission(packet(), "test-key-123", NOW)
            with self.assertRaises(IdempotencyConflict):
                store_submission(packet("Different Agent"), "test-key-123", NOW)

    def test_an_unlisted_status_fails_closed(self):
        # 200 is the API's update answer, which a create carrying no `sha`
        # cannot earn, so it is as unlisted as 500 is.
        for status in (200, 500):
            with self.subTest(status=status):
                fake = FakeIntake()
                attempts = []

                def refuse(*args, **kwargs):
                    attempts.append(kwargs.get("json"))
                    return Response(status)

                fake.put = refuse
                with intake(fake):
                    with self.assertRaises(StorageUnavailable):
                        store_submission(packet(), "test-key-123", NOW)
                self.assertEqual(1, len(attempts),
                                 "the backend was never reached")

    def test_a_refusal_with_nothing_stored_fails_closed(self):
        """A ref race refuses the create without the path existing. Reporting
        the empty read as success would answer 201 for a packet never written."""
        fake = FakeIntake()
        fake.put = lambda *a, **k: Response(409)
        with intake(fake):
            with self.assertRaises(StorageUnavailable):
                store_submission(packet(), "test-key-123", NOW)

    def test_a_read_that_is_not_our_record_is_not_a_conflict(self):
        """A refused create plus a readable-looking body used to be reported to
        the participant as key reuse, which is a terminal answer."""
        fake = FakeIntake()
        with intake(fake):
            first = store_submission(packet(), "test-key-123", NOW)
            for body in ({"name": "p.json", "sha": "abc", "content": "e30="},
                         {"receipt_hash": first["receipt_hash"]},
                         {"received_at": first["received_at"]},
                         ["not", "an", "object"]):
                with self.subTest(body=body):
                    fake.get = lambda *a, **k: Response(200, body)
                    with self.assertRaises(StorageUnavailable):
                        store_submission(packet(), "test-key-123", NOW)

    def test_the_repository_is_preferred_over_the_blob_store(self):
        """The issue's own words: a deployment with both configured prefers
        the repository."""
        fake = FakeIntake()
        with intake(fake), \
                patch.dict(os.environ, {"BLOB_READ_WRITE_TOKEN": "blob"}), \
                patch("ssa.questionnaire_api._store_blob") as blob:
            store_submission(packet(), "test-key-123", NOW)
        blob.assert_not_called()
        self.assertEqual(1, len(fake.puts))

    def test_a_write_that_cannot_be_reached_fails_closed(self):
        fake = FakeIntake()
        fake.put = unreachable
        with intake(fake):
            with self.assertRaises(StorageUnavailable):
                store_submission(packet(), "test-key-123", NOW)

    def test_a_read_that_cannot_be_reached_fails_closed(self):
        """The read-back is the replay path, and it runs when GitHub is least
        reliable. An escaping timeout would leave the endpoint with no body."""
        fake = FakeIntake()
        with intake(fake):
            store_submission(packet(), "test-key-123", NOW)
            fake.get = unreachable
            with self.assertRaises(StorageUnavailable):
                store_submission(packet(), "test-key-123", NOW)


if __name__ == "__main__":
    unittest.main()
