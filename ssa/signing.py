"""Request signing for Route A: the arena signs what it sends, the entrant
verifies it came from us.

The arena holds one Ed25519 private key (`SSA_SIGNING_KEY`, an Actions
secret) and publishes the matching public key in `site/keys.json` under a key
id. Every request to a participant endpoint carries three headers:

    X-SSA-Key-Id:     which published key to verify with
    X-SSA-Timestamp:  unix seconds when the request was signed
    X-SSA-Signature:  base64(Ed25519(private, f"{timestamp}.{raw body bytes}"))

The signature covers the timestamp and the exact bytes on the wire, so a
captured request cannot be replayed after `MAX_SKEW_SECONDS`. The `request_id`
inside the body is covered too; a second copy inside the window is answered
with the same forecast (the contract's idempotency), so a replay costs the
participant nothing. Verification needs nothing secret: the public key, the raw body
as received (never re-serialised JSON), and a clock.

This replaces the earlier per-entrant Bearer key. That design had the arena
storing a secret for every participant; this one stores none. Participants who
do not care may skip verification: the arena's filing path is unchanged
either way, because only the arena writes forecasts.

A second keypair, `ssa-test`, is published *with* its private key so the
browser test on `submit.html` and `tools/probe_agent_api.py` can send signed
requests without the real key. A server should accept both key ids; a test
request is never filed, so the only thing the public test key can cost a
participant is one model call.
"""
from __future__ import annotations

import base64
import json
import os
import time

HEADER_KEY_ID = "X-SSA-Key-Id"
HEADER_TIMESTAMP = "X-SSA-Timestamp"
HEADER_SIGNATURE = "X-SSA-Signature"
MAX_SKEW_SECONDS = 300

LIVE_KEY_ENV = "SSA_SIGNING_KEY"
LIVE_KEY_ID_ENV = "SSA_SIGNING_KEY_ID"
DEFAULT_LIVE_KEY_ID = "ssa-live"

# Published on purpose. See the module docstring.
TEST_KEY_ID = "ssa-test"
TEST_PRIVATE_KEY = "HLHPLfr2J+BaNVHYXBHNs5CJOSbmgouzCUp2cxcwdy4="
TEST_PUBLIC_KEY = "zt0rAf60fDi4fOj1qhhAqzx7GJz7XmBV1AWs+ln2xOY="

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEYS_FILE = os.path.join(ROOT, "site", "keys.json")


class SignatureError(Exception):
    """Why a request was refused. The message is safe to return to a caller."""


def _ed25519():
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
        from cryptography.hazmat.primitives import serialization
    except ImportError as err:                       # pragma: no cover
        raise RuntimeError(
            "request signing needs the `cryptography` package "
            "(pip install -r requirements.txt)") from err
    return ed25519, serialization


def generate() -> tuple[str, str]:
    """(private key, public key), both base64 of the 32 raw bytes."""
    ed25519, serialization = _ed25519()
    key = ed25519.Ed25519PrivateKey.generate()
    seed = key.private_bytes(serialization.Encoding.Raw,
                             serialization.PrivateFormat.Raw,
                             serialization.NoEncryption())
    pub = key.public_key().public_bytes(serialization.Encoding.Raw,
                                        serialization.PublicFormat.Raw)
    return base64.b64encode(seed).decode(), base64.b64encode(pub).decode()


def public_key_of(private_b64: str) -> str:
    ed25519, serialization = _ed25519()
    key = ed25519.Ed25519PrivateKey.from_private_bytes(_b64(private_b64, 32))
    pub = key.public_key().public_bytes(serialization.Encoding.Raw,
                                        serialization.PublicFormat.Raw)
    return base64.b64encode(pub).decode()


def _b64(value: str, length: int) -> bytes:
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as err:
        raise SignatureError("key or signature is not valid base64") from err
    if len(raw) != length:
        raise SignatureError(f"expected {length} bytes, got {len(raw)}")
    return raw


def message(timestamp: str, body: bytes) -> bytes:
    return timestamp.encode("ascii") + b"." + body


def sign(private_b64: str, body: bytes, key_id: str,
         timestamp: int | None = None) -> dict[str, str]:
    """The three headers for one request body."""
    ed25519, _ = _ed25519()
    key = ed25519.Ed25519PrivateKey.from_private_bytes(_b64(private_b64, 32))
    ts = str(int(timestamp if timestamp is not None else time.time()))
    sig = key.sign(message(ts, body))
    return {HEADER_KEY_ID: key_id, HEADER_TIMESTAMP: ts,
            HEADER_SIGNATURE: base64.b64encode(sig).decode()}


def verify(public_b64: str, headers, body: bytes,
           now: float | None = None,
           max_skew: int = MAX_SKEW_SECONDS) -> str:
    """Return the key id when the request is genuine and fresh; raise
    SignatureError otherwise. `headers` is any case-insensitive mapping (a
    dict of the three headers works)."""
    ed25519, _ = _ed25519()
    get = _getter(headers)
    key_id, ts, sig = get(HEADER_KEY_ID), get(HEADER_TIMESTAMP), get(HEADER_SIGNATURE)
    if not (key_id and ts and sig):
        raise SignatureError("request is not signed (missing X-SSA-* headers)")
    try:
        ts_int = int(ts)
    except ValueError as err:
        raise SignatureError("X-SSA-Timestamp is not an integer") from err
    if abs((now if now is not None else time.time()) - ts_int) > max_skew:
        raise SignatureError(f"timestamp is more than {max_skew}s from now")
    pub = ed25519.Ed25519PublicKey.from_public_bytes(_b64(public_b64, 32))
    try:
        pub.verify(_b64(sig, 64), message(ts, body))
    except Exception as err:                         # InvalidSignature
        raise SignatureError("signature does not match") from err
    return key_id


def _getter(headers):
    lower = {str(k).lower(): v for k, v in headers.items()}
    return lambda name: lower.get(name.lower())


def published_keys(path: str | None = None) -> dict[str, str]:
    """{key_id: public key} from site/keys.json, the test key always present."""
    keys = {TEST_KEY_ID: TEST_PUBLIC_KEY}
    p = path or KEYS_FILE
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as fh:
            doc = json.load(fh)
        for row in doc.get("keys", []):
            if row.get("public_key") and row.get("key_id"):
                keys[row["key_id"]] = row["public_key"]
    return keys


def verify_against_published(headers, body: bytes, keys: dict[str, str] | None = None,
                             now: float | None = None) -> str:
    """Verify with whichever published key the request names."""
    keys = keys or published_keys()
    key_id = _getter(headers)(HEADER_KEY_ID)
    if not key_id:
        raise SignatureError("request is not signed (missing X-SSA-* headers)")
    if key_id not in keys:
        raise SignatureError(f"unknown key id {key_id!r}")
    return verify(keys[key_id], headers, body, now=now)


def live_signer() -> tuple[str, str] | None:
    """(private key, key id) from the environment, or None when unset."""
    private = os.environ.get(LIVE_KEY_ENV, "").strip()
    if not private:
        return None
    return private, os.environ.get(LIVE_KEY_ID_ENV, "").strip() or DEFAULT_LIVE_KEY_ID
