"""Dependency-free SSA Agent API contract example.

Run from the repository root:

    python examples/agent-api/server.py                    # verifies signatures
    python examples/agent-api/server.py --no-verify        # accepts anything
    python examples/agent-api/server.py --port 8788 --delay 2

This is a fixture server, not a predictive agent. It does show the one piece
of security a participant writes: verifying that a request really came from
the arena (`verify_signature` below, ~15 lines, `cryptography` only). The
arena signs `X-SSA-Timestamp + "." + raw body` with its Ed25519 key; the
public keys are in `site/keys.json` and the test key is published so this
server can be probed without the live one. It answers all three round shapes because a real week mixes
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
import base64
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KEYS_FILE = os.path.join(ROOT, "site", "keys.json")
MAX_SKEW_SECONDS = 300
# The published test key, so this file works from a bare checkout too.
PUBLIC_KEYS = {"ssa-test": "zt0rAf60fDi4fOj1qhhAqzx7GJz7XmBV1AWs+ln2xOY="}


def load_public_keys(path=KEYS_FILE):
    """{key_id: base64 public key} from site/keys.json plus the test key."""
    keys = dict(PUBLIC_KEYS)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            for row in json.load(fh).get("keys", []):
                if row.get("key_id") and row.get("public_key"):
                    keys[row["key_id"]] = row["public_key"]
    return keys


def verify_signature(headers, raw_body, keys, now=None):
    """Raise ValueError unless the request is a fresh, genuine arena request.

    Verify over the raw bytes as received: re-serialising the JSON changes
    the bytes and the signature will never match. Reject a timestamp more than
    five minutes from now. A repeated request_id is not an attack but a
    retry, and the contract wants the same answer back: see `do_POST`.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    key_id = headers.get("X-SSA-Key-Id")
    ts = headers.get("X-SSA-Timestamp")
    sig = headers.get("X-SSA-Signature")
    if not (key_id and ts and sig):
        raise ValueError("request is not signed")
    if key_id not in keys:
        raise ValueError(f"unknown key id {key_id!r}")
    if abs((now or time.time()) - int(ts)) > MAX_SKEW_SECONDS:
        raise ValueError("timestamp too old or too far ahead")
    public = Ed25519PublicKey.from_public_bytes(base64.b64decode(keys[key_id]))
    try:
        public.verify(base64.b64decode(sig), ts.encode() + b"." + raw_body)
    except Exception as exc:
        raise ValueError("signature does not match") from exc

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
        length = spec.get("length") or 0
        items = spec.get("items")
        if items:
            return {"ranking": list(items)[:length] if length else list(items)}
        # Free choice: repeat the most recent week the round handed us, minus
        # anything the round says it excludes. That is the persistence null --
        # the thing every entrant is scored against -- so it is an honest
        # demonstration rather than a guess, and it shows where the two pieces
        # of the envelope a ranking round needs actually live:
        # `context.recent_weeks` and `ranking.exclusions`.
        weeks = (round_spec.get("context") or {}).get("recent_weeks") or []
        # A week is `{"date", "items", ...}`; `ranking` is accepted as well so
        # this keeps working if the envelope ever names the list that.
        newest = weeks[-1] if weeks else {}
        last = (newest.get("items") or newest.get("ranking")) if isinstance(newest, dict) else None
        if not last:
            raise ValueError(
                "free-choice ranking round carried no recent_weeks to repeat; "
                "a real entrant answers from its own model")
        excl = spec.get("exclusions") or {}
        titles = set(excl.get("titles") or ())
        prefixes = tuple(excl.get("prefixes") or ())
        keep, seen = [], set()
        for title in last:
            if title in titles or (prefixes and title.startswith(prefixes)):
                continue
            if title in seen:
                continue
            seen.add(title)
            keep.append(title)
        if length and len(keep) < length:
            raise ValueError(
                f"only {len(keep)} usable items in the last week, round wants {length}")
        return {"ranking": keep[:length] if length else keep}
    raise ValueError(f"unsupported target_type '{target}'")


class Handler(BaseHTTPRequestHandler):
    verify = True
    keys = PUBLIC_KEYS
    answered = {}        # request_id -> reply: a retry gets the same answer
    delay = 0.0

    def do_POST(self):
        if self.path.rstrip("/") not in ("", "/forecast"):
            self.send_error(404)
            return
        size = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(size)
        if self.verify:
            try:
                verify_signature(self.headers, raw, self.keys)
            except ValueError as exc:
                # 401, not 200-with-an-error, so the probe (and the arena's
                # runner) records a refusal rather than a malformed answer.
                self._json(401, {"error": str(exc)})
                return
        try:
            prompt = json.loads(raw)
            if prompt["schema_version"] != SCHEMA_VERSION:
                raise ValueError("unsupported schema_version")
            forecast = forecast_for(prompt["round"])
        except (KeyError, IndexError, TypeError, ValueError,
                json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})
            return

        # Idempotency: the arena retries with the same request_id, and the
        # contract wants the same forecast back, not a second computation.
        # This also blunts a replayed request: it costs nothing to answer.
        request_id = prompt.get("request_id")
        if request_id in self.answered:
            self._json(200, self.answered[request_id])
            return
        if self.delay:
            time.sleep(self.delay)
        reply = {
            "schema_version": SCHEMA_VERSION,
            "forecast": forecast,
            "reasoning_trace": "Non-scored starter-kit fixture.",
            "crosstabs": {},
        }
        if request_id is not None:
            self.answered[request_id] = reply
        self._json(200, reply)

    def do_OPTIONS(self):
        # The arena's own calls are server-to-server and never preflight. This
        # is here for one reason: the onboarding page tests an endpoint from
        # the visitor's browser, and a browser will not send that POST at all
        # unless the endpoint answers the preflight first. Two headers, and a
        # participant can test their endpoint from the page like everyone else.
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, X-SSA-Key-Id, X-SSA-Timestamp, X-SSA-Signature")
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _cors(self):
        # Public: the question is public, the forecast is public, and the
        # signature is what says a request came from the arena -- so nothing
        # here is protected by an origin check.
        self.send_header("Access-Control-Allow-Origin", "*")

    def _json(self, status, body):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        return


def serve(host=HOST, port=PORT, verify=True, delay=0.0, keys=None):
    """A configured server, not started. Returned so a test can run it in
    process rather than shelling out to a port that may already be busy."""
    handler = type("ConfiguredHandler", (Handler,),
                   {"verify": verify, "keys": keys or load_public_keys(),
                    "answered": {}, "delay": delay})
    return ThreadingHTTPServer((host, port), handler)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--delay", type=float, default=0.0,
                    help="seconds to stall before replying, for timeout tests")
    ap.add_argument("--no-verify", action="store_true",
                    help="answer unsigned requests too (not recommended)")
    args = ap.parse_args()
    server = serve(args.host, args.port, not args.no_verify, args.delay)
    print(f"SSA Agent API example listening on "
          f"http://{args.host}:{args.port}/forecast"
          + ("  (unsigned requests accepted)" if args.no_verify
             else "  (verifying arena signatures)"))
    server.serve_forever()
