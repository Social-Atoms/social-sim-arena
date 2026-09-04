"""Authenticated bundle upload endpoint. Route B's last step."""

import json
from http.server import BaseHTTPRequestHandler

from ssa.bundle_api import accept_upload
from ssa.questionnaire_api import MAX_BODY_BYTES


def _bearer(header):
    """The token from an Authorization header, or None."""
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else None


class handler(BaseHTTPRequestHandler):
    def _json(self, status, payload):
        # Escaped, not raw: a refused answer echoes the round_id it arrived
        # with, and a lone surrogate has no UTF-8 encoding.
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
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
            body = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, RecursionError):
            self._json(400, {"error": {
                "code": "invalid_json",
                "message": "Request body is not valid JSON.",
            }})
            return
        status, payload = accept_upload(
            body, _bearer(self.headers.get("Authorization")))
        self._json(status, payload)
