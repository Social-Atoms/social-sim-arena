"""Route A contract test: does this endpoint actually answer an arena round?

  python examples/agent-api/server.py &
  python tools/probe_agent_api.py --url http://127.0.0.1:8787/forecast

  python tools/probe_agent_api.py --url https://api.example.com/forecast

Standard library plus, optionally, `cryptography`: with it the probe signs its
requests with the published test key (`ssa-test`, see site/keys.json) the way
the arena signs live ones; without it the requests go unsigned and the probe
says so. It sends non-scored fixtures, files nothing, and never touches
`forecasts/`.

**Signing, not a bearer token.** The arena authenticates itself to your
endpoint by signing every request with its Ed25519 key; you verify with the
public key. Nothing secret is exchanged. A maintainer probing with the live
key names its variable with `--signing-key-env`; the key itself is never an
argument, because arguments end up in `ps`, shell history and pasted logs.

**Why probe more than one shape.** A real batch mixes scalar, profile and
ranking rounds. An endpoint tested against a single scalar fixture passes, and
then returns a number for a sixteen-cell profile round on the week it first
meets one -- which the arena records as a failed call, not as a forecast, and
the participant discovers after the deadline. Each shape is a separate check
with its own verdict.

**Why the signature check sends a bad signature.** Verifying is optional: an
endpoint that answers an unsigned or badly signed request is not insecure for
the arena (only the arena files forecasts), it is merely open to anyone who
finds the URL and wants it to compute. The probe reports which of the two your
endpoint is, so you know rather than assume.

**Why retries are only for transient failures.** A 4xx is an answer: the
request was wrong, and sending it again is a way to be told so four more times.
`ssa/harness.py` draws the same line, and the arena's live runner (15s connect,
600s read, retried by the scheduled refresh until the batch deadline) is what
this probe is a rehearsal for.

Exit status is 0 only when every selected check passed.
"""
import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCHEMA_VERSION = "ssa-agent-api-v2"
# Mirrors ssa/signing.py. This file imports nothing from ssa/ on purpose: a
# participant runs it from a bare checkout.
TEST_KEY_ID = "ssa-test"
TEST_PRIVATE_KEY = "HLHPLfr2J+BaNVHYXBHNs5CJOSbmgouzCUp2cxcwdy4="
HEADER_KEY_ID, HEADER_TIMESTAMP, HEADER_SIGNATURE = (
    "X-SSA-Key-Id", "X-SSA-Timestamp", "X-SSA-Signature")


def make_signer(private_b64, key_id):
    """A function body -> headers, or None when `cryptography` is missing."""
    try:
        import base64
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError:
        return None
    key = ed25519.Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_b64))

    def sign(body, timestamp=None, corrupt=False):
        ts = str(int(timestamp if timestamp is not None else time.time()))
        sig = key.sign(ts.encode() + b"." + body)
        if corrupt:
            sig = bytes([sig[0] ^ 0xFF]) + sig[1:]
        return {HEADER_KEY_ID: key_id, HEADER_TIMESTAMP: ts,
                HEADER_SIGNATURE: base64.b64encode(sig).decode()}
    return sign

# What the arena's own runner uses (`ssa.harness.TIMEOUT`). Quoted here so a
# participant sizing their endpoint reads one number, not two.
ARENA_TIMEOUT = (15, 600)

LOOPBACK = ("127.0.0.1", "::1", "localhost")

# Non-scored fixtures, one per round shape the season contains. The round ids
# are deliberately not season round ids: a probe must not be answerable by
# looking up a real question, and a reply to one of these must never be
# mistaken for a forecast.
FIXTURES = {
    "scalar": {
        "round_id": "ssa-contract-test",
        "board_id": "topline",
        "target_type": "continuous_normal",
        "question": "Non-scored contract test: return a normal forecast "
                    "centered on 50.",
        "unit": "points",
        "lock_at": "2099-01-01T00:00:00Z",
        "context": {"persistence": 50},
    },
    "profile": {
        "round_id": "ssa-contract-test-profile",
        "board_id": "profile",
        "target_type": "profile_energy",
        "question": "Non-scored contract test: return one distribution for "
                    "each of the four declared cells.",
        "unit": "points, per cell",
        "lock_at": "2099-01-01T00:00:00Z",
        "context": {"persistence": 50},
        "cells": ["probe_cell_a", "probe_cell_b", "probe_cell_c",
                  "probe_cell_d"],
    },
    "ranking": {
        "round_id": "ssa-contract-test-ranking",
        "board_id": "ranking",
        "target_type": "ranking_list",
        "question": "Non-scored contract test: return the four declared items "
                    "in your predicted order.",
        "unit": "ordered list of 4 items",
        "lock_at": "2099-01-01T00:00:00Z",
        "context": {},
        "ranking": {"length": 4,
                    "items": ["probe_a", "probe_b", "probe_c", "probe_d"]},
    },
}


class ProbeFailure(RuntimeError):
    """The endpoint answered, but not in a way the arena could file."""


def envelope(shape, request_id):
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id,
        "round": FIXTURES[shape],
        "optional_crosstabs": [],
    }


def call(url, prompt, signer=None, timeout=30.0, retries=2, corrupt=False):
    """POST one request envelope to the exact URL, signed when a signer is
    given. Retries transient failures only.

    Returns the decoded reply object. Raises `ProbeFailure` with the reason a
    participant has to fix.
    """
    body = json.dumps(prompt).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if signer:
        headers.update(signer(body, corrupt=corrupt))

    last = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, data=body, headers=headers,
                                         method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
            break
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", "replace")[:200]
            if 400 <= err.code < 500:
                # A 4xx is an answer. Sending it again is a way to be told so
                # four more times.
                raise ProbeFailure(f"HTTP {err.code}: {detail}") from err
            last = ProbeFailure(f"HTTP {err.code}: {detail}")
        except (urllib.error.URLError, socket.timeout, TimeoutError) as err:
            reason = getattr(err, "reason", err)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                last = ProbeFailure(
                    f"no reply within {timeout:g}s. The arena allows "
                    f"{ARENA_TIMEOUT[1]}s to read a reply, but a round that "
                    "needs most of that has no room left for a retry before "
                    "the batch deadline.")
            else:
                last = ProbeFailure(f"could not reach {url}: {reason}")
        if attempt < retries:
            time.sleep(0.5 * (attempt + 1))
    else:
        raise last

    try:
        reply = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as err:
        raise ProbeFailure(
            "reply body is not JSON. It must be the object in "
            f"schema/agent-api-response.schema.json ({err})") from err
    if not isinstance(reply, dict):
        raise ProbeFailure("reply body is JSON but not an object")
    return reply


def check_content(content, shape):
    """The decoded reply, against the versioned response contract and the round.

    The schema check is the versioned contract; the per-shape check is the one
    the schema cannot make, because the schema does not know which round was
    asked. An endpoint that returns a valid profile for a scalar round passes
    the first and fails the second, which is exactly the distinction that
    matters.
    """
    schema_path = os.path.join(ROOT, "schema", "agent-api-response.schema.json")
    try:
        import jsonschema
        with open(schema_path, encoding="utf-8") as fh:
            jsonschema.validate(content, json.load(fh))
    except ImportError:
        if not isinstance(content, dict) or "forecast" not in content:
            raise ProbeFailure("reply has no `forecast`")
    except Exception as err:
        raise ProbeFailure(
            f"reply does not match schema/agent-api-response.schema.json: "
            f"{err}") from err

    forecast = content["forecast"]
    if shape == "scalar":
        if "mean" not in forecast or "sd" not in forecast:
            raise ProbeFailure(
                "a scalar round takes a mean and a strictly positive sd; a "
                "point guess is not a distribution and is not scored as one")
    elif shape == "profile":
        wanted = set(FIXTURES["profile"]["cells"])
        got = set((forecast.get("profile") or {}))
        if got != wanted:
            raise ProbeFailure(
                f"a profile round takes every declared cell and only those. "
                f"missing: {sorted(wanted - got) or 'none'}; "
                f"unasked: {sorted(got - wanted) or 'none'}")
    elif shape == "ranking":
        wanted = FIXTURES["ranking"]["ranking"]["items"]
        got = forecast.get("ranking") or []
        if sorted(got) != sorted(wanted):
            raise ProbeFailure(
                f"a fixed-basket ranking round takes a permutation of "
                f"{wanted}, not a free choice; got {got}")
    return content


def load_entrant(entrant_id):
    path = os.path.join(ROOT, "entrants", entrant_id + ".json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def probe(url, signer=None, timeout=30.0, retries=2, shapes=None):
    """[(name, ok, detail)] -- one row per check, in the order they ran."""
    rows = []
    parsed = urlparse(url)
    if parsed.scheme == "https":
        rows.append(("transport", True, "https"))
    elif parsed.hostname in LOOPBACK:
        rows.append(("transport", True,
                     "loopback http, acceptable for a local fixture only"))
    else:
        rows.append(("transport", False,
                     "production endpoints must be https; the arena refuses "
                     "to call anything else"))
        return rows

    for shape in (shapes or ("scalar", "profile", "ranking")):
        request_id = f"ssa-probe-{shape}"
        try:
            content = call(url, envelope(shape, request_id), signer, timeout,
                           retries)
            check_content(content, shape)
            rows.append((f"round/{shape}", True, "valid forecast"))
        except ProbeFailure as err:
            rows.append((f"round/{shape}", False, str(err)))
            continue
        if shape == "scalar":
            try:
                again = call(url, envelope(shape, request_id), signer,
                             timeout, retries)
                same = again.get("forecast") == content.get("forecast")
                rows.append(("idempotency", same,
                             "same request_id, same forecast" if same else
                             "the same request_id produced a different "
                             "forecast; the arena may retry a call after a "
                             "dropped connection, and two different answers "
                             "to one request means the filed one is a coin "
                             "toss"))
            except ProbeFailure as err:
                rows.append(("idempotency", False, str(err)))

    if signer is None:
        rows.append(("signature", True,
                     "requests were sent unsigned (install `cryptography` to "
                     "probe signature verification)"))
    else:
        try:
            call(url, envelope("scalar", "ssa-probe-badsig"), signer, timeout,
                 retries=0, corrupt=True)
            rows.append(("signature", True,
                         "endpoint does not verify signatures: allowed, but "
                         "anyone who finds this URL can make it compute"))
        except ProbeFailure as err:
            refused = "HTTP 401" in str(err) or "HTTP 403" in str(err)
            rows.append(("signature", refused,
                         "bad signature refused; endpoint verifies" if refused
                         else f"bad signature was not refused with 401/403: {err}"))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url",
                    help="The exact endpoint URL, e.g. https://host/forecast. "
                         "Optional when --entrant names a registration that "
                         "carries a route.")
    ap.add_argument("--signing-key-env",
                    help="NAME of the environment variable holding a signing "
                         "key (maintainers, with the live key). Default: the "
                         "published test key. Never pass the key itself.")
    ap.add_argument("--key-id", default=TEST_KEY_ID,
                    help="key id to send with --signing-key-env")
    ap.add_argument("--timeout", type=float, default=30.0,
                    help=f"seconds to wait for a reply (the arena allows "
                         f"{ARENA_TIMEOUT[1]})")
    ap.add_argument("--retries", type=int, default=2,
                    help="retries for transient failures only; a 4xx is never "
                         "retried")
    ap.add_argument("--shape", action="append", dest="shapes",
                    choices=["scalar", "profile", "ranking"],
                    help="probe only this shape (repeatable)")
    ap.add_argument("--entrant",
                    help="read the route from entrants/<id>.json, and refuse "
                         "to probe a revoked registration")
    args = ap.parse_args(argv)

    if args.entrant:
        entrant = load_entrant(args.entrant)
        if entrant is None:
            print(f"FAIL: no entrants/{args.entrant}.json in this checkout",
                  file=sys.stderr)
            return 1
        if entrant.get("status") == "revoked":
            print(f"FAIL: entrant '{args.entrant}' is revoked. A revoked "
                  "registration is not probed and not called: the point of "
                  "revocation is that the arena stops reaching the endpoint.",
                  file=sys.stderr)
            return 1
        # With a registered route this is the *maintainer-side* probe: same
        # checks, but aimed by the registration rather than by whatever the
        # operator typed. A probe that passes against a URL nobody registered
        # says nothing about the endpoint the season will actually call.
        spec = entrant.get("route") or {}
        if spec.get("url") and not args.url:
            args.url = spec["url"]
    if not args.url:
        print("FAIL: pass --url, or --entrant naming a registration that "
              "carries a route", file=sys.stderr)
        return 1

    if args.signing_key_env:
        private = os.environ.get(args.signing_key_env)
        if not private:
            print(f"FAIL: ${args.signing_key_env} is not set", file=sys.stderr)
            return 1
        signer = make_signer(private, args.key_id)
        if signer is None:
            print("FAIL: signing needs the `cryptography` package", file=sys.stderr)
            return 1
    else:
        signer = make_signer(TEST_PRIVATE_KEY, TEST_KEY_ID)

    rows = probe(args.url, signer, args.timeout, args.retries, args.shapes)
    for name, ok, detail in rows:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<16} {detail}")
    failures = [name for name, ok, _ in rows if not ok]
    if failures:
        print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print("\nall checks passed. This is the technical contract only; "
          "registration acceptance is separate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
