"""Public, read-only questionnaire manifest endpoint."""

import json
from http.server import BaseHTTPRequestHandler

from ssa.questionnaire_api import build_manifest


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(build_manifest(), ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "public, max-age=30, s-maxage=30")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Allow", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
