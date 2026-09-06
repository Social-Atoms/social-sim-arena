"""The authenticated bundle upload endpoint.

Run: PYTHONPATH=. python tests/test_bundle_api.py

Nothing here reaches the network or writes outside a temporary directory. The
fixtures are the sandbox bundle, whose deadline is years out, so the suite does
not start failing on a date.
"""
import base64
import copy
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import requests

from ssa import bundle, bundle_api, participants
from ssa.bundle_api import accept_upload, upload_key_env

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(ROOT, "examples", "bundle")
ENTRANT = "demo_bundle_entrant"
TOKEN = "an-upload-token"
DEFAULT = object()


def read(name):
    with open(os.path.join(EXAMPLES, name), encoding="utf-8") as fh:
        return json.load(fh)


def before_deadline(bundle_doc):
    due = datetime.fromisoformat(bundle_doc["deadline"].replace("Z", "+00:00"))
    return due - timedelta(days=2)


class BundleUpload(unittest.TestCase):
    def setUp(self):
        self.bundle = read("sandbox-batch.json")
        self.response = read("sandbox-response.json")
        self.now = before_deadline(self.bundle)
        self.store = tempfile.mkdtemp()
        self.entrants = tempfile.mkdtemp()
        self.bundles = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.store)
        self.addCleanup(shutil.rmtree, self.entrants)
        self.addCleanup(shutil.rmtree, self.bundles)
        shutil.copy(os.path.join(EXAMPLES, "sandbox-batch.json"),
                    os.path.join(self.bundles, self.bundle["batch_id"] + ".json"))
        self.register(ENTRANT)
        env = patch.dict(os.environ, {
            "SUBMISSION_STORAGE_DIR": self.store,
            "INTAKE_REPO_TOKEN": "",
            "BLOB_READ_WRITE_TOKEN": "",
            upload_key_env(ENTRANT): TOKEN,
        }, clear=False)
        env.start()
        self.addCleanup(env.stop)

    def register(self, entrant_id, **extra):
        path = os.path.join(self.entrants, entrant_id + ".json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"entrant_id": entrant_id, "display_name": entrant_id,
                       **extra}, fh)

    def upload(self, response=DEFAULT, token=TOKEN, now=None):
        return accept_upload(self.response if response is DEFAULT else response,
                             token, now=now or self.now,
                             entrants_dir=self.entrants,
                             bundles_dir=self.bundles)

    def stored(self):
        return sorted(os.path.relpath(os.path.join(root, name), self.store)
                      for root, _, names in os.walk(self.store)
                      for name in names)

    def test_an_upload_is_receipted_and_stored_under_its_entrant(self):
        status, payload = self.upload()
        self.assertEqual(200, status)
        self.assertEqual(3, payload["receipt"]["accepted"])
        self.assertEqual(3, len(payload["results"]))
        digest = payload["receipt"]["response_sha256"]
        self.assertEqual(
            [os.path.join("bundles", self.bundle["batch_id"], ENTRANT,
                          digest + ".json")], self.stored())

    def test_a_credential_that_does_not_match_is_refused(self):
        cases = {
            "no token": None,
            "wrong token": "not-the-token",
            "empty token": "",
            "token is not a string": ["an-upload-token"],
            "token is not ascii": "an-upload-tokén",
            "token holds a lone surrogate": "an-upload-token\ud800",
        }
        for name, token in cases.items():
            with self.subTest(name):
                self.assertEqual(401, self.upload(token=token)[0])
        unregistered = dict(self.response, entrant_id="nobody")
        self.assertEqual(401, self.upload(unregistered)[0])
        self.assertEqual([], self.stored())

    def test_an_entrant_whose_key_is_not_installed_cannot_upload(self):
        self.register("keyless")
        payload = dict(self.response, entrant_id="keyless")
        self.assertEqual(401, self.upload(payload)[0])
        self.assertEqual([], self.stored())

    def test_a_field_of_the_wrong_type_is_refused_not_raised(self):
        for value in (None, 5, True, 1.5, ["x"], {"a": 1}):
            with self.subTest(entrant_id=value):
                payload = dict(self.response, entrant_id=value)
                self.assertEqual(401, self.upload(payload)[0])
            with self.subTest(batch_id=value):
                payload = dict(self.response, batch_id=value)
                self.assertEqual(422, self.upload(payload)[0])

    def test_a_body_that_is_not_an_object_is_refused(self):
        for value in (None, [], "a string", 5):
            with self.subTest(body=value):
                self.assertEqual(422, self.upload(value)[0])

    def test_a_bundle_error_that_is_not_a_revocation_is_a_client_error(self):
        payload = dict(self.response, schema_version="0.0.1")
        status, body = self.upload(payload)
        self.assertEqual(422, status)
        self.assertNotEqual("entrant_revoked", body["error"]["code"])

    def test_a_revoked_entrant_is_refused_before_a_receipt_exists(self):
        self.register(ENTRANT, status="revoked")
        status, body = self.upload()
        self.assertEqual(403, status)
        self.assertEqual("entrant_revoked", body["error"]["code"])
        self.assertEqual([], self.stored())

    def test_the_same_upload_twice_is_a_replay(self):
        first = self.upload()[1]
        later = self.upload(now=self.now + timedelta(minutes=5))[1]
        self.assertEqual(first["receipt"], later["receipt"])
        self.assertEqual(1, len(self.stored()))

    def test_the_upload_secret_is_the_only_secret_and_a_missing_one_is_closed(self):
        """Route A hands out no credential at all any more (the arena signs
        its requests), so the arena's own signing key must never open this
        door, and an empty upload variable is a closed door, not an open one."""
        self.assertEqual("SSA_UPLOAD_KEY_", bundle_api.UPLOAD_KEY_PREFIX)
        with patch.dict(os.environ, {
                upload_key_env(ENTRANT): "",
                "SSA_SIGNING_KEY": "HLHPLfr2J+BaNVHYXBHNs5CJOSbmgouzCUp2cxcwdy4="}):
            self.assertEqual(401, self.upload(token="HLHPLfr2J+BaNVHYXBHNs5CJOSbmgouzCUp2cxcwdy4=")[0])
            self.assertEqual(401, self.upload(token="")[0])
        self.assertEqual([], self.stored())

    def test_a_registration_that_cannot_be_read_is_not_a_traceback(self):
        for content in ('{"entrant_id": "trunc', '[1, 2, 3]', 'null'):
            with self.subTest(content[:10]):
                path = os.path.join(self.entrants, ENTRANT + ".json")
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(content)
                self.assertEqual(401, self.upload()[0])

    def test_the_stored_record_keeps_the_response_whole(self):
        self.upload()
        with open(os.path.join(self.store, self.stored()[0])) as fh:
            record = json.load(fh)
        self.assertEqual(self.response, record["response"])
        self.assertEqual("ssa-bundle-response-v1", record["record_version"])
        self.assertEqual(record["receipt"]["response_sha256"],
                         record["receipt_hash"])
        with open(os.path.join(self.store, self.stored()[0]), "rb") as fh:
            self.assertEqual(bundle.canonical(record), fh.read())

    def test_a_bundle_that_cannot_be_read_is_not_the_uploaders_fault(self):
        path = os.path.join(self.bundles, self.bundle["batch_id"] + ".json")
        for content in (b"not json", b"\xff\xfe not utf-8"):
            with self.subTest(content[:8]):
                with open(path, "wb") as fh:
                    fh.write(content)
                status, body = self.upload()
                self.assertEqual(503, status)
                self.assertEqual("bundle_unpublishable", body["error"]["code"])

    def test_a_store_that_cannot_be_written_is_a_storage_fault(self):
        with patch.dict(os.environ,
                        {"SUBMISSION_STORAGE_DIR": os.path.join(
                            self.store, "missing", "deeper")}):
            with patch("pathlib.Path.mkdir", side_effect=PermissionError):
                status, body = self.upload()
        self.assertEqual(503, status)
        self.assertEqual("submission_storage_unavailable", body["error"]["code"])

    def test_a_late_upload_is_receipted_with_nothing_accepted(self):
        due = datetime.fromisoformat(
            self.bundle["deadline"].replace("Z", "+00:00"))
        status, payload = self.upload(now=due + timedelta(seconds=1))
        self.assertEqual(200, status)
        self.assertEqual(0, payload["receipt"]["accepted"])
        self.assertEqual({"late"}, {r["reason"] for r in payload["results"]})

    def test_a_batch_id_names_a_published_bundle_or_nothing(self):
        os.mkdir(os.path.join(self.bundles, "batch-2029-02-02.json"))
        cases = {"batch-2029-01-01": 404, "batch-2029-02-02": 404,
                 "batch-2028-01-03\n": 422,
                 "../../../etc/passwd": 422, "batch-2028-01-03/../x": 422}
        for batch_id, expected in cases.items():
            with self.subTest(batch_id):
                payload = dict(self.response, batch_id=batch_id)
                self.assertEqual(expected, self.upload(payload)[0])

    def test_storage_that_is_not_configured_is_not_a_silent_success(self):
        with patch.dict(os.environ, {"SUBMISSION_STORAGE_DIR": ""}):
            status, body = self.upload()
        self.assertEqual(503, status)
        self.assertEqual("submission_storage_unavailable", body["error"]["code"])
        self.assertNotIn("INTAKE_REPO_TOKEN", body["error"]["message"])
        self.assertEqual([], self.stored())

    def test_a_stored_record_that_cannot_be_read_is_a_storage_fault(self):
        digest = self.upload()[1]["receipt"]["response_sha256"]
        path = os.path.join(self.store, "bundles", self.bundle["batch_id"],
                            ENTRANT, digest + ".json")
        for content in ("not json at all",
                        json.dumps({"receipt_hash": digest,
                                    "received_at": "2028-01-01T00:00:00Z"})):
            with self.subTest(content[:12]):
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(content)
                status, body = self.upload()
                self.assertEqual(503, status)
                self.assertEqual("submission_storage_unavailable",
                                 body["error"]["code"])

    def test_a_bundle_the_calendar_refuses_is_not_the_uploaders_fault(self):
        broken = copy.deepcopy(self.bundle)
        broken["deadline"] = "2028-01-04T12:00:00Z"
        with open(os.path.join(self.bundles, broken["batch_id"] + ".json"),
                  "w", encoding="utf-8") as fh:
            json.dump(broken, fh)
        status, body = self.upload()
        self.assertEqual(503, status)
        self.assertEqual("bundle_unpublishable", body["error"]["code"])
        self.assertEqual([], self.stored())


class IntakeBackend(unittest.TestCase):
    """A replay over the backend production uses.

    `_store_local` never reads the record's top-level `received_at`, so the
    rest of this file cannot see that it is there. `_intake_read` refuses a
    record without it, which turns every re-upload into a 503.
    """

    def setUp(self):
        self.case = BundleUpload("run")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.files = {}
        for verb, fake in (("put", self.put), ("get", self.get)):
            call = patch(f"requests.{verb}",
                         lambda *a, _f=fake, **k: _f(*a, **k))
            call.start()
            self.addCleanup(call.stop)
        env = patch.dict(os.environ, {"SUBMISSION_STORAGE_DIR": "",
                                      "INTAKE_REPO_TOKEN": "token"})
        env.start()
        self.addCleanup(env.stop)

    def put(self, url, json=None, headers=None, timeout=None):
        path = url.split("/contents/")[1]
        if path in self.files:
            return _Response(422)
        self.files[path] = base64.b64decode(json["content"])
        return _Response(201)

    def get(self, url, headers=None, timeout=None):
        path = url.split("/contents/")[1]
        if path not in self.files:
            return _Response(404)
        return _Response(200, json.loads(self.files[path]))

    def test_the_same_upload_twice_replays_through_the_intake_repository(self):
        first = self.case.upload()[1]
        later = self.case.upload(now=self.case.now + timedelta(minutes=5))
        self.assertEqual(200, later[0])
        self.assertEqual(first["receipt"], later[1]["receipt"])
        self.assertEqual(1, len(self.files))


class _Response:
    """Only what `_store_github` and `_intake_read` touch."""

    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self.payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class Shell(unittest.TestCase):
    """The handler, loaded by path because `api/` is not a package."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "_ssa_bundle_handler", os.path.join(ROOT, "api",
                                                "bundle_submissions.py"))
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def write(self, payload):
        handler = self.module.handler.__new__(self.module.handler)
        handler.headers = {}
        handler.wfile = io.BytesIO()
        handler.send_response = lambda *a: None
        handler.send_header = lambda *a: None
        handler.end_headers = lambda: None
        handler._json(200, payload)
        return handler.wfile.getvalue()

    def test_a_reply_a_participant_provoked_can_always_be_written(self):
        """A refused answer echoes the round_id it arrived with, and a lone
        surrogate is legal JSON input with no UTF-8 encoding."""
        body = self.write({"results": [{"round_id": "\ud800"}]})
        self.assertIn(b"ud800", body)

    def post(self, raw, content_type="application/json", length=None,
             authorization=None):
        handler = self.module.handler.__new__(self.module.handler)
        handler.headers = {"Content-Type": content_type,
                           "Content-Length": str(length or len(raw)),
                           "Authorization": authorization}
        handler.rfile = io.BytesIO(raw)
        handler.wfile = io.BytesIO()
        handler.status = None
        handler.send_response = lambda status: setattr(handler, "status", status)
        handler.send_header = lambda *a: None
        handler.end_headers = lambda: None
        handler.do_POST()
        return handler.status

    def test_a_body_the_parser_cannot_reach_the_end_of_is_a_bad_request(self):
        for raw in (b"[" * 60000, b"{oops"):
            with self.subTest(raw[:6]):
                self.assertEqual(400, self.post(raw))

    def test_the_handler_guards_the_request_before_reading_it(self):
        self.assertEqual(415, self.post(b"{}", content_type="text/plain"))
        self.assertEqual(400, self.post(b""))
        self.assertEqual(413, self.post(b"{}", length=600000))

    def test_the_handler_passes_the_token_and_not_the_whole_header(self):
        seen = []
        self.module.accept_upload = lambda body, token: seen.append(token) or (
            200, {})
        self.post(b"{}", authorization="Bearer the-token")
        self.assertEqual(["the-token"], seen)

    def test_only_a_bearer_scheme_yields_a_token(self):
        for header, expected in (("Bearer k", "k"), ("bearer k", "k"),
                                 ("BEARER k", "k"), ("Basic k", None),
                                 ("k", None), ("", None), (None, None)):
            with self.subTest(header):
                self.assertEqual(expected, self.module._bearer(header))


class Deployment(unittest.TestCase):
    """Vercel traces imports, so the files this path opens by name need saying.

    Without them the endpoint deploys and fails: no validator is a 500 on
    every upload, no `entrants/` a 401 on every upload.
    """

    def test_vercel_ships_what_this_path_opens_by_name(self):
        config = json.loads(
            (Path(ROOT) / "vercel.json").read_text(encoding="utf-8"))
        build = next(b for b in config["builds"]
                     if b["use"] == "@vercel/python")
        for name in ("tools/validate_submission.py", "entrants/*.json",
                     "questions/bundles/*.json"):
            self.assertIn(name, build["config"]["includeFiles"])
        routes = {r["source"]: r["destination"] for r in config["rewrites"]}
        self.assertEqual("/api/bundle_submissions.py",
                         routes["/api/v1/bundle-submissions"])


if __name__ == "__main__":
    unittest.main()
