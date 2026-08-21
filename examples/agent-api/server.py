"""Dependency-free SSA Agent API contract example.

Run from the repository root:
    python examples/agent-api/server.py

This is intentionally a fixture server, not production authentication or an
actual predictive agent.
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


HOST = "127.0.0.1"
PORT = 8787


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(size))
            prompt = json.loads(body["messages"][0]["content"])
            if prompt["schema_version"] != "ssa-agent-api-v1":
                raise ValueError("unsupported schema_version")
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})
            return

        content = {
            "schema_version": "ssa-agent-api-v1",
            "forecast": {"mean": 50.0, "sd": 5.0},
            "reasoning_trace": "Non-scored starter-kit fixture.",
            "crosstabs": {},
        }
        response = {
            "id": "chatcmpl-ssa-contract-test",
            "object": "chat.completion",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": json.dumps(content)},
                "finish_reason": "stop",
            }],
        }
        self._json(200, response)

    def _json(self, status, body):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    print(f"SSA Agent API example listening on http://{HOST}:{PORT}/v1")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
