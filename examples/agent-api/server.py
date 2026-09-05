"""Dependency-free SSA Agent API contract example.

Run from the repository root:

    python examples/agent-api/server.py                    # no authentication
    SSA_EXAMPLE_API_KEY=secret python examples/agent-api/server.py
    python examples/agent-api/server.py --port 8788 --delay 2

This is a fixture server, not a predictive agent and not production
authentication. It answers all three round shapes because a real week mixes
them: an endpoint that only ever returns a scalar passes a contract test built
from one fixture and then fails on the first profile round it is asked, which
is the week a participant finds out.

`--delay` holds the response open, so `tools/probe_agent_api.py --timeout` can
be seen to time out against something real rather than against a mock. Timeout
behaviour is the failure mode that never shows up in a hand test: an endpoint
that answers in eleven minutes is indistinguishable from one that works, right
up until the arena's read timeout closes the round.
"""
import argparse
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
PORT = 8787
SCHEMA_VERSION = "ssa-agent-api-v2"

# The fixed answer for a scalar round. Constant on purpose: the contract test
# checks the shape of a reply, and a fixture that moved would make a failing
# contract test indistinguishable from a fixture that had drifted.
FIXTURE_MEAN = 50.0
FIXTURE_SD = 5.0


def forecast_for(round_spec):
    """One forecast in the shape the round asked for.

    `target_type` decides, and an unknown one raises rather than defaulting to
    a scalar. A default here would return a well-formed answer to a question
    that was not asked, which the arena would file and score.
    """
    target = round_spec.get("target_type")
    if target in (None, "continuous_normal"):
        return {"mean": FIXTURE_MEAN, "sd": FIXTURE_SD}
    if target == "profile_energy":
        cells = round_spec.get("cells") or []
        if len(cells) < 2:
            raise ValueError("profile round declared no cells")
        return {"profile": {cell: {"mean": FIXTURE_MEAN, "sd": FIXTURE_SD}
                            for cell in cells}}
    if target == "ranking_list":
        spec = round_spec.get("ranking") or {}
        items = spec.get("items")
        if not items:
            raise ValueError(
                "this fixture only answers fixed-basket ranking rounds; a "
                "free-choice ranking needs a real model, not a default")
        return {"ranking": list(items)}
    raise ValueError(f"unsupported target_type '{target}'")


class Handler(BaseHTTPRequestHandler):
    api_key = None
    delay = 0.0

    def do_POST(self):
        if self.path.rstrip("/") not in ("", "/forecast"):
            self.send_error(404)
            return
        if self.api_key:
            supplied = self.headers.get("Authorization", "")
            if supplied != f"Bearer {self.api_key}":
                # 401, not 200-with-an-error: an endpoint that answers an
                # unauthenticated caller is an endpoint anyone can file
                # forecasts through under your entrant id.
                self._json(401, {"error": "missing or wrong bearer token"})
                return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            prompt = json.loads(self.rfile.read(size))
            if prompt["schema_version"] != SCHEMA_VERSION:
                raise ValueError("unsupported schema_version")
            forecast = forecast_for(prompt["round"])
        except (KeyError, IndexError, TypeError, ValueError,
                json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})
            return

        if self.delay:
            time.sleep(self.delay)
        self._json(200, {
            "schema_version": SCHEMA_VERSION,
            "forecast": forecast,
            "reasoning_trace": "Non-scored starter-kit fixture.",
            "crosstabs": {},
        })

    def _json(self, status, body):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        return


def serve(host=HOST, port=PORT, api_key=None, delay=0.0):
    """A configured server, not started. Returned so a test can run it in
    process rather than shelling out to a port that may already be busy."""
    handler = type("ConfiguredHandler", (Handler,),
                   {"api_key": api_key, "delay": delay})
    return ThreadingHTTPServer((host, port), handler)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--delay", type=float, default=0.0,
                    help="seconds to stall before replying, for timeout tests")
    args = ap.parse_args()
    key = os.environ.get("SSA_EXAMPLE_API_KEY")
    server = serve(args.host, args.port, key, args.delay)
    print(f"SSA Agent API example listening on "
          f"http://{args.host}:{args.port}/forecast"
          + ("  (Bearer auth required)" if key else "  (no authentication)"))
    server.serve_forever()
