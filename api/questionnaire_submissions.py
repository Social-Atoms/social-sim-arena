"""Validated questionnaire intake endpoint backed by private storage."""

import json
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

from ssa.questionnaire_api import (
    IdempotencyConflict,
    MAX_BODY_BYTES,
    StorageUnavailable,
    SubmissionError,
    store_submission,
    validate_submission,
)


def _origin_allowed(origin, host):
    if not origin:
        return True
    allowed = {
        value.strip() for value in
        os.environ.get("SUBMISSION_ALLOWED_ORIGINS", "").split(",")
        if value.strip()
    }
    parsed = urlparse(origin)
    return origin in allowed or parsed.netloc == host


class handler(BaseHTTPRequestHandler):
    def _json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        origin = self.headers.get("Origin")
        if origin and _origin_allowed(origin, self.headers.get("Host", "")):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        origin = self.headers.get("Origin")
        if not _origin_allowed(origin, self.headers.get("Host", "")):
            self._json(403, {"error": {
                "code": "origin_not_allowed",
                "message": "This browser origin is not allowed to submit.",
            }})
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0]
        if content_type.lower() != "application/json":
            self._json(415, {"error": {
                "code": "unsupported_media_type",
                "message": "Content-Type must be application/json.",
            }})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY_BYTES:
            self._json(413 if length > MAX_BODY_BYTES else 400, {"error": {
                "code": "invalid_body_size",
                "message": f"Request body must contain 1 to {MAX_BODY_BYTES} bytes.",
            }})
            return
        try:
            envelope = json.loads(self.rfile.read(length))
            validated = validate_submission(envelope)
            receipt = store_submission(
                validated, self.headers.get("Idempotency-Key", ""))
        except json.JSONDecodeError:
            self._json(400, {"error": {
                "code": "invalid_json",
                "message": "Request body is not valid JSON.",
            }})
            return
        except IdempotencyConflict as error:
            self._json(409, {"error": {
                "code": error.code,
                "message": str(error),
                "details": error.details,
            }})
            return
        except SubmissionError as error:
            self._json(422, {"error": {
                "code": error.code,
                "message": str(error),
                "details": error.details,
            }})
            return
        except StorageUnavailable as error:
            self._json(503, {"error": {
                "code": "submission_storage_unavailable",
                "message": str(error),
            }})
            return
        self._json(201 if not receipt["idempotent_replay"] else 200, receipt)

    def do_OPTIONS(self):
        origin = self.headers.get("Origin")
        if not _origin_allowed(origin, self.headers.get("Host", "")):
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(204)
        self.send_header("Allow", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, Idempotency-Key")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
