"""Reveal signed forecasts from a fixed, protected sealed-branch snapshot.

Git dates are trusted-writer records, not independent timestamps. Never execute
code or workflows from the sealed branch. Keep its objects for public audits.
"""
import base64
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ssa import signed_forecasts as wire


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, text=True).strip()


def document(root, ref, path):
    return json.loads(git(root, 'show', f'{ref}:{path}'))


def ancestor(root, older, newer):
    return subprocess.run(['git', 'merge-base', '--is-ancestor', older, newer],
                          cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def latest_valid(root, snapshot, path, due):
    for commit in git(root, 'log', '--format=%H', snapshot, '--', path).splitlines():
        value = document(root, commit, path)
        persisted = git(root, 'show', '-s', '--format=%cI', commit)
        if wire.utc(persisted) < due and wire.utc(value['received_at']) < due:
            return value, commit, persisted
    return None


def public_status(root, rounds, ref='refs/remotes/origin/sealed'):
    """Only filing metadata from accepted encrypted versions, never answers."""
    snapshot = git(root, 'rev-parse', ref)
    deadlines = {row['round_id']: wire.utc(row['lock_at']) for row in rounds}
    out = {}
    for path in git(root, 'ls-tree', '-r', '--name-only', snapshot, 'sealed/').splitlines():
        parts = path.split('/')
        if len(parts) != 3 or not path.endswith('.json') or parts[1] not in deadlines:
            continue
        current = document(root, snapshot, path)
        if current.get('version') != wire.VERSION:
            continue
        selected = latest_valid(root, snapshot, path, deadlines[parts[1]])
        if not selected:
            continue
        value = selected[0]
        if path != f"sealed/{value['round_id']}/{value['entrant']}.json":
            raise wire.IntakeError('source_path_mismatch', 422)
        out.setdefault(parts[1], {})[value['entrant']] = {
            'filed': value['received_at'], 'sealed': True}
    return out


def verify_reveal(root, body, proof, base, sealed_tip, now):
    snapshot, commit, path = proof['snapshot'], proof['source_commit'], proof['source_path']
    if len(snapshot) != 40 or len(commit) != 40 or not ancestor(root, snapshot, sealed_tip):
        raise wire.IntakeError('untrusted_sealed_snapshot', 422)
    if path != f"sealed/{body['round_id']}/{body['entrant']}.json":
        raise wire.IntakeError('source_path_mismatch', 422)
    rounds = document(root, base, 'questions/season0.json')['rounds']
    rnd = next(r for r in rounds if r['round_id'] == body['round_id'])
    due = wire.utc(rnd['lock_at'])
    if now < due:
        raise wire.IntakeError('reveal_before_deadline', 422)
    selected = latest_valid(root, snapshot, path, due)
    if not selected or selected[1] != commit:
        raise wire.IntakeError('not_final_valid_version', 422)
    # Ensure snapshot wasn't intentionally selected before a later valid filing.
    tip_selected = latest_valid(root, sealed_tip, path, due)
    if not tip_selected or tip_selected[1] != commit:
        raise wire.IntakeError('stale_sealed_snapshot', 422)
    envelope, _, persisted = selected
    if envelope['ciphertext_sha256'] != proof['ciphertext_sha256']:
        raise wire.IntakeError('ciphertext_mismatch', 422)
    # Revealed plaintext is independently bound by the participant signature.
    raw, meta = wire.decode(proof['body_b64']), proof['headers']
    if wire.parse_body(raw) != body or meta['entrant'] != body['entrant']:
        raise wire.IntakeError('revealed_content_mismatch', 422)
    if (meta['request-id'] != envelope['request_id'] or
            wire.digest(wire.decode(meta['signature'])) != envelope['fingerprint']):
        raise wire.IntakeError('receipt_mismatch', 422)
    registry = envelope['registry_commit']
    if not ancestor(root, registry, base) or not ancestor(root, envelope['round_commit'], base):
        raise wire.IntakeError('unapproved_registry', 422)
    reg = document(root, registry, f"entrants/{body['entrant']}.json")
    wire.verify(meta, raw, reg, envelope['audience'], wire.utc(envelope['received_at']))
    schema = document(root, base, 'schema/forecast.schema.json')
    wire.validate_answer(body, meta, rnd, schema, wire.utc(persisted))
    return True


def reveal(root, snapshot, identities, now=None, base='HEAD',
           sealed_tip='refs/remotes/origin/sealed'):
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    snapshot = git(root, 'rev-parse', snapshot)
    sealed_tip = git(root, 'rev-parse', sealed_tip)
    rounds = {r['round_id']: r for r in document(root, base, 'questions/season0.json')['rounds']}
    count = 0
    for path in git(root, 'ls-tree', '-r', '--name-only', snapshot, 'sealed/').splitlines():
        parts = path.split('/')
        if len(parts) != 3 or not path.endswith('.json') or parts[1] not in rounds:
            continue
        current_envelope = document(root, snapshot, path)
        # Arena-collected receipts use their existing platform-signature protocol.
        if current_envelope.get('version') != wire.VERSION:
            continue
        due = wire.utc(rounds[parts[1]]['lock_at'])
        if now < due:
            continue
        selected = latest_valid(root, snapshot, path, due)
        if not selected:
            continue
        envelope, source_commit, _ = selected
        raw, meta = wire.open_envelope(envelope, identities)
        body = wire.parse_body(raw)
        proof = {'snapshot': snapshot, 'source_commit': source_commit, 'source_path': path,
                 'ciphertext_sha256': envelope['ciphertext_sha256'],
                 'headers': meta, 'body_b64': base64.b64encode(raw).decode()}
        verify_reveal(root, body, proof, base, sealed_tip, now)
        target = root / 'forecasts' / body['round_id'] / (body['entrant'] + '.json')
        evidence = root / 'reveal-receipts' / body['round_id'] / (body['entrant'] + '.json')
        if target.exists():
            if json.loads(target.read_text()) != body or not evidence.exists():
                raise wire.IntakeError('existing_forecast_conflict', 422)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_bytes(wire.canonical(proof) + b'\n')
        target.write_bytes(wire.canonical(body) + b'\n')
        count += 1
    return count
