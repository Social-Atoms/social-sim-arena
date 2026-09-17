"""Optional client: private key stays local; retries reuse the exact request.
Generate a raw Ed25519 private key with --generate-key. The output file is 0600.
"""
import argparse
import base64
import getpass
import json
import os
from pathlib import Path
import sys
import uuid
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import requests
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import UnsupportedAlgorithm
from ssa.signed_forecasts import AUDIENCE, ORIGIN, PATH, signing_bytes, stamp


def load_private_key(path):
    """Whatever the participant already has: the raw 32 bytes this tool writes,
    the OpenSSH key ssh-keygen writes, or PEM. A passphrase comes from
    SSA_KEY_PASSPHRASE, or is asked for once when the key turns out to need one
    (so an unattended run fails loudly instead of hanging on a prompt)."""
    data = Path(path).read_bytes()
    if len(data) == 32:
        return Ed25519PrivateKey.from_private_bytes(data)
    load = (serialization.load_ssh_private_key
            if b'OPENSSH PRIVATE KEY' in data[:80] else serialization.load_pem_private_key)
    secret = os.environ.get('SSA_KEY_PASSPHRASE')
    try:
        return unlock(load, data, secret.encode() if secret else None)
    except TypeError:
        if not sys.stdin.isatty():
            raise SystemExit(f'{path} is passphrase-protected; set SSA_KEY_PASSPHRASE')
        return unlock(load, data, getpass.getpass(f'Passphrase for {path}: ').encode())


def unlock(load, data, secret):
    try:
        return load(data, secret)
    except UnsupportedAlgorithm:
        # cryptography hands OpenSSH's bcrypt KDF to a module it does not require.
        raise SystemExit('a passphrase-protected OpenSSH key needs bcrypt: '
                         'pip install bcrypt, or generate the key with -N ""') from None


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--generate-key')
    p.add_argument('--key')
    p.add_argument('--key-id', default='k1')
    p.add_argument('--entrant')
    p.add_argument('--audience', default=AUDIENCE,
                   help='Only the isolated rehearsal fork needs to override this')
    p.add_argument('--url', default=ORIGIN)
    p.add_argument('--answer')
    p.add_argument('--request-file', help='Saved signed request for safe retries; contains plaintext, keep private')
    args = p.parse_args()
    if args.generate_key:
        key = Ed25519PrivateKey.generate()
        fd = os.open(args.generate_key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as out:
            out.write(key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                        serialization.NoEncryption()))
        print(base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,
                                                            serialization.PublicFormat.Raw)).decode())
        return
    if not args.url or not args.url.startswith('https://') or not args.request_file:
        p.error('--url HTTPS and --request-file are required')
    saved = Path(args.request_file)
    if saved.exists():
        record = json.loads(saved.read_text())
        if record['url'] != args.url:
            p.error('saved request belongs to a different URL')
    else:
        if not all((args.key, args.entrant, args.answer)):
            p.error('new requests need --key --entrant --answer')
        raw = Path(args.answer).read_bytes()
        meta = {'entrant': args.entrant, 'key-id': args.key_id, 'request-id': str(uuid.uuid4()),
                'timestamp': stamp(datetime.now(timezone.utc))}
        key = load_private_key(args.key)
        meta['signature'] = base64.b64encode(key.sign(signing_bytes(meta, raw, args.audience))).decode()
        record = {'url': args.url, 'meta': meta, 'body': base64.b64encode(raw).decode()}
        fd = os.open(saved, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as out:
            json.dump(record, out)
    headers = {'X-SSA-' + k: v for k, v in record['meta'].items()}
    headers['Content-Type'] = 'application/json'
    response = requests.post(args.url.rstrip('/') + PATH, headers=headers,
                             data=base64.b64decode(record['body']), timeout=60,
                             allow_redirects=False)
    print(response.text)
    raise SystemExit(0 if response.ok else 1)


if __name__ == '__main__':
    main()
