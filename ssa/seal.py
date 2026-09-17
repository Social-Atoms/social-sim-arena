"""age-encrypted storage and signed receipts for arena-collected forecasts."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import datetime, timezone

import pyrage

from . import signing


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEALED = os.path.join(ROOT, "sealed")
DISCLOSURES = os.path.join(ROOT, "reveal-receipts")
ENABLE_ENV = "SSA_SEAL_FORECASTS"
AFTER_ENV = "SSA_SEAL_AFTER"
RECIPIENT_ENV = "SSA_AGE_RECIPIENT"
AGE_KEY_ID_ENV = "SSA_AGE_KEY_ID"
IDENTITIES_ENV = "SSA_AGE_IDENTITIES"
DOMAIN = "social-sim-arena/forecast-seal/v1"
PRIVATE_RECORD_DOMAIN = "social-sim-arena/private-record/v1"


class SealError(ValueError):
    pass


def enabled() -> bool:
    return os.environ.get(ENABLE_ENV, "").strip().lower() in {
        "1", "true", "yes", "on"
    }


def eligible(deadline) -> bool:
    """Only rounds closing after the explicit rollout boundary are sealed."""
    if not enabled():
        return False
    raw = os.environ.get(AFTER_ENV, "").strip()
    if not raw:
        raise SealError(f"{ENABLE_ENV} is enabled but {AFTER_ENV} is missing")
    try:
        after = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as err:
        raise SealError(f"{AFTER_ENV} is not an ISO timestamp") from err
    if after.tzinfo is None:
        raise SealError(f"{AFTER_ENV} must include a timezone")
    return deadline.astimezone(timezone.utc) >= after.astimezone(timezone.utc)


def _recipient():
    value = os.environ.get(RECIPIENT_ENV, "").strip()
    if not value:
        raise SealError(f"{ENABLE_ENV} is enabled but {RECIPIENT_ENV} is missing")
    try:
        return pyrage.x25519.Recipient.from_str(value)
    except Exception as err:
        raise SealError(f"{RECIPIENT_ENV} is not an age X25519 recipient") from err


def _age_key_id():
    value = os.environ.get(AGE_KEY_ID_ENV, "").strip()
    if not value:
        raise SealError(f"{ENABLE_ENV} is enabled but {AGE_KEY_ID_ENV} is missing")
    return value


def _identities():
    raw = os.environ.get(IDENTITIES_ENV, "").strip()
    if not raw:
        raise SealError(f"{ENABLE_ENV} is enabled but {IDENTITIES_ENV} is missing")
    try:
        values = json.loads(raw)
        if not isinstance(values, list) or not values or not all(
                isinstance(value, str) and value for value in values):
            raise ValueError("identities must be a non-empty string list")
        return [pyrage.x25519.Identity.from_str(value) for value in values]
    except Exception as err:
        raise SealError(f"{IDENTITIES_ENV} is not a JSON list of age identities") from err


def _encrypt(plain: bytes) -> str:
    return base64.b64encode(pyrage.encrypt(plain, [_recipient()])).decode("ascii")


def _decrypt(encoded: str) -> bytes:
    try:
        ciphertext = base64.b64decode(encoded, validate=True)
        return pyrage.decrypt(ciphertext, _identities())
    except Exception as err:
        raise SealError("age ciphertext cannot be opened") from err


def require_ready() -> None:
    """Fail before any provider call when rollout is enabled incorrectly."""
    if not enabled():
        return
    # Parse the rollout bound now, not after a provider has been paid.
    eligible(datetime.max.replace(tzinfo=timezone.utc))
    _age_key_id()
    probe = _encrypt(b"ssa-age-readiness")
    if _decrypt(probe) != b"ssa-age-readiness":
        raise SealError("configured age identity does not match the recipient")
    signer = signing.live_signer()
    if signer is None:
        raise SealError(f"{ENABLE_ENV} is enabled but {signing.LIVE_KEY_ENV} is missing")
    public = signing.public_key_of(signer[0])
    if signer[1] == signing.TEST_KEY_ID or public == signing.TEST_PUBLIC_KEY:
        raise SealError("the public ssa-test key cannot sign forecast receipts")
    published = signing.published_keys()
    if published.get(signer[1]) != public:
        raise SealError("the live receipt signer does not match site/keys.json")


def require_rollout_consistent() -> None:
    """A flag cannot strand receipts that still need the reveal pass."""
    if enabled() or not os.path.isdir(SEALED):
        return
    for round_id in os.listdir(SEALED):
        directory = os.path.join(SEALED, round_id)
        if not os.path.isdir(directory):
            continue
        for name in os.listdir(directory):
            if name.endswith(".json") and not os.path.exists(os.path.join(
                    DISCLOSURES, round_id, name)):
                raise SealError(
                    "SSA_SEAL_FORECASTS cannot be disabled with pending receipts")


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def encrypt_record(record: dict, kind: str) -> dict:
    """Opaque storage for reply/search evidence that could reveal an answer."""
    require_ready()
    return {
        "version": 1,
        "domain": PRIVATE_RECORD_DOMAIN,
        "kind": kind,
        "cipher": "age-v1-x25519",
        "encryption_key_id": _age_key_id(),
        "ciphertext": _encrypt(canonical(record)),
    }


def decrypt_record(record: dict, kind: str) -> dict:
    if record.get("domain") != PRIVATE_RECORD_DOMAIN or record.get("kind") != kind:
        return record                         # pre-rollout plaintext evidence
    try:
        if record.get("cipher") != "age-v1-x25519":
            raise ValueError("unsupported cipher")
        got = json.loads(_decrypt(record["ciphertext"]))
    except Exception as err:
        raise SealError(f"encrypted {kind} record cannot be opened") from err
    if not isinstance(got, dict):
        raise SealError(f"encrypted {kind} record is not an object")
    return got


def path(round_id: str, entrant: str, root: str | None = None) -> str:
    return os.path.join(root or SEALED, round_id, entrant + ".json")


def disclosure_path(round_id: str, entrant: str,
                    root: str | None = None) -> str:
    return os.path.join(root or DISCLOSURES, round_id, entrant + ".json")


def _iso(now=None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _signed_bytes(receipt: dict) -> bytes:
    unsigned = {k: v for k, v in receipt.items() if k != "signature"}
    return DOMAIN.encode("ascii") + b"\n" + canonical(unsigned)


def seal(forecast: dict, *, received_at=None) -> dict:
    require_ready()
    signer = signing.live_signer()
    assert signer is not None
    salt = os.urandom(32)
    plain = canonical(forecast)
    payload = canonical({
        "forecast": forecast,
        "commitment_salt": base64.b64encode(salt).decode("ascii"),
    })
    receipt = {
        "version": 1,
        "domain": DOMAIN,
        "round_id": forecast.get("round_id"),
        "entrant": forecast.get("entrant"),
        "received_at": _iso(received_at),
        "commitment_sha256": hashlib.sha256(salt + plain).hexdigest(),
        "cipher": "age-v1-x25519",
        "encryption_key_id": _age_key_id(),
        "ciphertext": _encrypt(payload),
        "key_id": signer[1],
    }
    unix = int(datetime.fromisoformat(
        receipt["received_at"].replace("Z", "+00:00")).timestamp())
    headers = signing.sign(signer[0], _signed_bytes(receipt), signer[1], unix)
    receipt["signature"] = headers[signing.HEADER_SIGNATURE]
    return receipt


def verify_receipt(receipt: dict, public_keys: dict[str, str]) -> None:
    if receipt.get("version") != 1 or receipt.get("domain") != DOMAIN:
        raise SealError("unsupported forecast seal")
    key_id = receipt.get("key_id")
    if not key_id or key_id == signing.TEST_KEY_ID or key_id not in public_keys:
        raise SealError("forecast receipt is not signed by a published live key")
    try:
        when = datetime.fromisoformat(
            receipt["received_at"].replace("Z", "+00:00"))
        if when.tzinfo is None:
            raise ValueError("naive timestamp")
        unix = int(when.astimezone(timezone.utc).timestamp())
    except (KeyError, TypeError, ValueError) as err:
        raise SealError("forecast receipt has no valid received_at") from err
    headers = {
        signing.HEADER_KEY_ID: key_id,
        signing.HEADER_TIMESTAMP: str(unix),
        signing.HEADER_SIGNATURE: receipt.get("signature"),
    }
    try:
        signing.verify(public_keys[key_id], headers, _signed_bytes(receipt),
                       now=unix, max_skew=0)
    except signing.SignatureError as err:
        raise SealError("forecast receipt signature does not match") from err


def open_receipt(receipt: dict, public_keys: dict[str, str]) -> tuple[dict, str]:
    verify_receipt(receipt, public_keys)
    try:
        if receipt.get("cipher") != "age-v1-x25519":
            raise ValueError("unsupported cipher")
        payload = json.loads(_decrypt(receipt["ciphertext"]))
        forecast = payload["forecast"]
        salt_b64 = payload["commitment_salt"]
        salt = base64.b64decode(salt_b64, validate=True)
    except Exception as err:
        raise SealError("forecast ciphertext cannot be opened") from err
    verify_disclosure(receipt, forecast, {"commitment_salt": salt_b64})
    if forecast.get("round_id") != receipt.get("round_id") or \
            forecast.get("entrant") != receipt.get("entrant"):
        raise SealError("forecast identity does not match its receipt")
    return forecast, salt_b64


def verify_disclosure(receipt: dict, forecast: dict, disclosure: dict) -> None:
    try:
        salt = base64.b64decode(disclosure["commitment_salt"], validate=True)
    except Exception as err:
        raise SealError("reveal has no valid commitment salt") from err
    digest = hashlib.sha256(salt + canonical(forecast)).hexdigest()
    if digest != receipt.get("commitment_sha256"):
        raise SealError("revealed forecast does not match its commitment")


def write_json_atomic(dest: str, body: dict) -> None:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = f"{dest}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(body, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, dest)


def write_json_exclusive(dest: str, body: dict) -> None:
    """Create one immutable JSON artifact; never replace a concurrent writer."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(dest, flags, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(body, fh, indent=2, sort_keys=True, ensure_ascii=False)
            fh.write("\n")
    except Exception:
        try:
            os.unlink(dest)
        except OSError:
            pass
        raise


def seal_to_file(forecast: dict, *, received_at=None,
                 sealed_root: str | None = None, replace: bool = False) -> str:
    dest = path(forecast["round_id"], forecast["entrant"], sealed_root)
    body = seal(forecast, received_at=received_at)
    if replace:
        write_json_atomic(dest, body)
    else:
        try:
            write_json_exclusive(dest, body)
        except FileExistsError as err:
            raise SealError("a sealed forecast is immutable") from err
    return dest


def read(pathname: str) -> dict:
    with open(pathname, encoding="utf-8") as fh:
        return json.load(fh)


def reveal_to_files(receipt_path: str, forecast_path: str, *, public_keys,
                    disclosure_root: str | None = None) -> tuple[str, str]:
    receipt = read(receipt_path)
    forecast, salt = open_receipt(receipt, public_keys)
    disclosure = {
        "version": 1,
        "round_id": receipt["round_id"],
        "entrant": receipt["entrant"],
        "sealed_receipt": os.path.relpath(receipt_path, ROOT),
        "commitment_salt": salt,
    }
    dpath = disclosure_path(receipt["round_id"], receipt["entrant"],
                            disclosure_root)
    if os.path.exists(forecast_path):
        with open(forecast_path, encoding="utf-8") as fh:
            if json.load(fh) != forecast:
                raise SealError("refusing to replace an existing forecast")
    else:
        write_json_atomic(forecast_path, forecast)
    if os.path.exists(dpath):
        if read(dpath) != disclosure:
            raise SealError("refusing to replace an existing reveal receipt")
    else:
        write_json_atomic(dpath, disclosure)
    return forecast_path, dpath
