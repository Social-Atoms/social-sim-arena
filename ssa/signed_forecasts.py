"""Signed forecast wire protocol and age v1 sealing. No provider calls.

The platform is a trusted receiver. Signature timestamps are not independent
receipt-time proofs. Exact request bytes and signature remain inside ciphertext.
"""
import base64
import hashlib
import json
import re
from datetime import datetime, timezone

import jsonschema
import pyrage
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

VERSION = 'ssa-signed-forecast-v1'
PATH = '/api/v1/forecasts'
MAX_BYTES = 65536
SAFE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$')
FIELDS = ('entrant', 'key-id', 'timestamp', 'request-id', 'signature')


class IntakeError(Exception):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code, self.status = code, status


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def utc(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        raise IntakeError('invalid_timestamp') from None


def stamp(now):
    return now.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def decode(value):
    try:
        return base64.b64decode(value, validate=True)
    except Exception:
        raise IntakeError('invalid_encoding') from None


def metadata(headers):
    lower = {k.lower(): v for k, v in headers.items()}
    result = {k: lower.get('x-ssa-' + k, '') for k in FIELDS}
    if any(not result[k] for k in FIELDS):
        raise IntakeError('missing_signature', 401)
    if any(not SAFE.fullmatch(result[k]) for k in ('entrant', 'key-id', 'request-id')):
        raise IntakeError('invalid_identifier')
    return result


def signing_bytes(meta, body, audience):
    return canonical({'protocol': VERSION, 'audience': audience, 'method': 'POST',
                      'path': PATH, 'entrant': meta['entrant'], 'key_id': meta['key-id'],
                      'request_id': meta['request-id'], 'signed_at': meta['timestamp'],
                      'body_sha256': digest(body)})


def verify(meta, body, registration, audience, now, fresh=True):
    if registration.get('entrant_id') != meta['entrant'] or registration.get('status', 'active') != 'active':
        raise IntakeError('entrant_inactive', 403)
    keys = [k for k in registration.get('keys', []) if k['id'] == meta['key-id']]
    if len(keys) != 1 or keys[0].get('revoked') or keys[0].get('alg') != 'ed25519':
        raise IntakeError('unknown_key', 401)
    signed = utc(meta['timestamp'])
    if fresh and abs((now - signed).total_seconds()) > 300:
        raise IntakeError('signature_expired', 401)
    try:
        Ed25519PublicKey.from_public_bytes(decode(keys[0]['public'])).verify(
            decode(meta['signature']), signing_bytes(meta, body, audience))
    except Exception:
        raise IntakeError('invalid_signature', 401) from None


def parse_body(raw):
    if not 0 < len(raw) <= MAX_BYTES:
        raise IntakeError('invalid_body_size', 413)
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                raise ValueError('duplicate field')
            obj[key] = value
        return obj
    try:
        body = json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(body, dict):
            raise ValueError()
        return body
    except (ValueError, UnicodeError, RecursionError):
        raise IntakeError('invalid_json') from None


def validate_answer(body, meta, round_def, schema, now, check_deadline=True):
    from tools import validate_submission as validator
    try:
        jsonschema.validate(body, schema)
    except jsonschema.ValidationError:
        raise IntakeError('invalid_forecast', 422) from None
    if body['entrant'] != meta['entrant']:
        raise IntakeError('entrant_mismatch', 403)
    if body['round_id'] != round_def.get('round_id'):
        raise IntakeError('unknown_round', 422)
    try:
        validator.validate_answer_contract(body, round_def)
    except validator.AnswerContractError:
        raise IntakeError('invalid_forecast', 422) from None
    due = validator.effective_deadline(utc(round_def['lock_at']))
    if check_deadline and now >= due:
        raise IntakeError('round_closed', 422)
    return due


def seal(raw, meta, registration_commit, round_commit, audience, recipient, key_id, received_at):
    body = parse_body(raw)
    outer = {'version': VERSION, 'round_id': body['round_id'], 'entrant': meta['entrant'],
             'request_id': meta['request-id'], 'fingerprint': digest(decode(meta['signature'])),
             'received_at': stamp(received_at), 'registry_commit': registration_commit,
             'round_commit': round_commit, 'audience': audience, 'encryption': 'age-v1-x25519',
             'encryption_key_id': key_id}
    inside = {'binding': outer, 'headers': meta, 'body_b64': base64.b64encode(raw).decode()}
    encrypted = pyrage.encrypt(canonical(inside), [pyrage.x25519.Recipient.from_str(recipient)])
    return {**outer, 'ciphertext': base64.b64encode(encrypted).decode(),
            'ciphertext_sha256': digest(encrypted)}


def open_envelope(envelope, identities):
    ciphertext = decode(envelope['ciphertext'])
    if digest(ciphertext) != envelope['ciphertext_sha256']:
        raise IntakeError('ciphertext_mismatch', 422)
    try:
        inside = json.loads(pyrage.decrypt(ciphertext,
            [pyrage.x25519.Identity.from_str(key) for key in identities]))
        outer = {k: v for k, v in envelope.items() if k not in ('ciphertext', 'ciphertext_sha256')}
        if inside['binding'] != outer or outer['version'] != VERSION:
            raise ValueError()
        raw = decode(inside['body_b64'])
        meta = inside['headers']
        body = parse_body(raw)
        if (body['entrant'] != outer['entrant'] or body['round_id'] != outer['round_id'] or
                meta['request-id'] != outer['request_id'] or meta['entrant'] != outer['entrant'] or
                digest(decode(meta['signature'])) != outer['fingerprint']):
            raise ValueError()
        return raw, meta
    except Exception:
        raise IntakeError('invalid_sealed_envelope', 422) from None
