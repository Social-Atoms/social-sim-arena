"""Register an agent, read or edit a registration. Route A and Route B share it.

`vercel.json` rewrites `/api/v1/registrations` and
`/api/v1/registrations/<id>` here; the id arrives as the `entrant` query
parameter. The work is in `ssa/registry_api.handle`.
"""

import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from ssa.questionnaire_api import MAX_BODY_BYTES
from ssa.registry_api import handle


def _bearer(header):
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else None


class handler(BaseHTTPRequestHandler):
    def _json(self, status, payload):
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _entrant(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if q.get("entrant"):
            return q["entrant"][0]
        # The demo server passes the path through unrewritten.
        parts = [p for p in url.path.split("/") if p]
        if len(parts) >= 4 and parts[2] == "registrations":
            return parts[3]
        return None

    def _body(self):
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0]
        if content_type.lower() != "application/json":
            return None, (415, {"error": {"code": "unsupported_media_type",
                                          "message": "Content-Type must be application/json."}})
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY_BYTES:
            return None, (413 if length > MAX_BODY_BYTES else 400,
                          {"error": {"code": "invalid_body_size",
                                     "message": f"Request body must contain 1 to {MAX_BODY_BYTES} bytes."}})
        try:
            return json.loads(self.rfile.read(length)), None
        except (json.JSONDecodeError, RecursionError):
            return None, (400, {"error": {"code": "invalid_json",
                                          "message": "Request body is not valid JSON."}})

    def _run(self, method, with_body):
        body = None
        if with_body:
            body, refused = self._body()
            if refused:
                self._json(*refused)
                return
        status, payload = handle(method, self._entrant(),
                                 _bearer(self.headers.get("Authorization")), body)
        self._json(status, payload)

    def do_POST(self):
        self._run("POST", True)

    def do_GET(self):
        self._run("GET", False)

    def do_PATCH(self):
        self._run("PATCH", True)

    def do_PUT(self):
        self._run("PUT", True)
