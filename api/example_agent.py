"""A Route A endpoint the arena can call for a rehearsal: the starter fixture
server, deployed. Answers every round shape with the fixed fixture forecast
after verifying the arena's signature, exactly as examples/agent-api/server.py
does locally. Register it as an entrant only for a rehearsal week, then revoke
it: its forecasts are constants and would sit on the board as an entrant.

    https://social-simulation-arena.com/api/example-agent
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "examples", "agent-api"))

from server import SCHEMA_VERSION, forecast_for, load_public_keys, verify_signature  # noqa: E402

KEYS = load_public_keys(os.path.join(ROOT, "site", "keys.json"))


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        # The onboarding page tests an endpoint from the visitor's browser, and
        # a browser sends no cross-origin POST until the preflight is answered.
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, X-SSA-Key-Id, X-SSA-Timestamp, X-SSA-Signature")
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _json(self, status, payload):
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = 0
        raw = self.rfile.read(size) if size > 0 else b""
        try:
            verify_signature(self.headers, raw, KEYS)
        except (ValueError, KeyError) as exc:
            self._json(401, {"error": str(exc)})
            return
        try:
            prompt = json.loads(raw)
            if prompt.get("schema_version") != SCHEMA_VERSION:
                raise ValueError("unsupported schema_version")
            forecast = forecast_for(prompt["round"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})
            return
        self._json(200, {"schema_version": SCHEMA_VERSION, "forecast": forecast,
                         "reasoning_trace": "Non-scored starter-kit fixture, "
                                            "deployed for the rehearsal.",
                         "crosstabs": {}})

    def do_GET(self):
        self._json(200, {"endpoint": "POST a signed ssa-agent-api-v2 request here",
                         "keys": sorted(KEYS)})
