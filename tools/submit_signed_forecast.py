"""Optional client: private key stays local; retries reuse the exact request.
Generate a raw Ed25519 private key with --generate-key. The output file is 0600.
"""
import argparse
import base64
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
from ssa.signed_forecasts import PATH, signing_bytes, stamp


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--generate-key')
    p.add_argument('--key')
    p.add_argument('--key-id', default='k1')
    p.add_argument('--entrant')
    p.add_argument('--audience')
    p.add_argument('--url')
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
        if not all((args.key, args.entrant, args.audience, args.answer)):
            p.error('new requests need --key --entrant --audience --answer')
        raw = Path(args.answer).read_bytes()
        meta = {'entrant': args.entrant, 'key-id': args.key_id, 'request-id': str(uuid.uuid4()),
                'timestamp': stamp(datetime.now(timezone.utc))}
        key = Ed25519PrivateKey.from_private_bytes(Path(args.key).read_bytes())
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
