"""Signed POST orchestration. Configuration is explicit; no default live target."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from ssa import signed_forecasts as protocol
from ssa.forecast_store import GitHubStore, receipt

ROOT = Path(__file__).resolve().parents[1]


def accept(raw, headers, store=None, clock=None):
    clock = clock or (lambda: datetime.now(timezone.utc))
    if os.environ.get('SSA_SIGNED_INTAKE_ENABLED') != '1':
        raise protocol.IntakeError('intake_disabled', 503)
    audience = os.environ['SSA_INTAKE_AUDIENCE']
    meta = protocol.metadata(headers)
    body = protocol.parse_body(raw)
    store = store or GitHubStore()
    main = store.head(os.environ.get('SSA_INTAKE_REGISTRY_BRANCH', 'main'))
    reg, _ = store.file('entrants/' + meta['entrant'] + '.json', main)
    if reg is None:
        raise protocol.IntakeError('unknown_entrant', 401)
    # Expired signatures may retrieve an existing receipt, but never cause a new write.
    protocol.verify(meta, raw, reg, audience, clock(), fresh=False)
    schema = json.loads((ROOT / 'schema/forecast.schema.json').read_text())
    season, _ = store.file('questions/season0.json', main)
    round_def = next((r for r in season['rounds'] if r['round_id'] == body.get('round_id')), None)
    if round_def is None:
        raise protocol.IntakeError('unknown_round', 422)
    due = protocol.utc(round_def['lock_at'])
    # Validate before constructing paths. Historical retries still need valid content.
    protocol.validate_answer(body, meta, round_def, schema, clock(), check_deadline=False)
    path = f"sealed/{body['round_id']}/{meta['entrant']}.json"
    fingerprint = protocol.digest(protocol.decode(meta['signature']))
    for _ in range(3):
        head = store.head(store.branch)
        history = store.history(path, head)
        for value, commit, committed_at in history:
            if value.get('version') != protocol.VERSION:
                raise protocol.IntakeError('submission_route_conflict', 409)
            if value['request_id'] == meta['request-id']:
                if value['fingerprint'] != fingerprint:
                    raise protocol.IntakeError('request_id_conflict', 409)
                return receipt(store, value, commit, committed_at, due)
        protocol.verify(meta, raw, reg, audience, clock())
        protocol.validate_answer(body, meta, round_def, schema, clock())
        if len(history) >= 120:
            raise protocol.IntakeError('submission_history_limit', 429)
        if history and (clock() - protocol.utc(history[0][0]['received_at'])).total_seconds() < 2:
            raise protocol.IntakeError('submission_rate_limit', 429)
        # Refresh authorization on each retry; fail if the registry changed.
        if store.head(os.environ.get('SSA_INTAKE_REGISTRY_BRANCH', 'main')) != main:
            raise protocol.IntakeError('registry_changed_retry', 409)
        current, old_sha = store.file(path, head)
        envelope = protocol.seal(raw, meta, main, main, audience,
                                 os.environ['SSA_AGE_RECIPIENT'], os.environ['SSA_AGE_KEY_ID'], clock())
        try:
            commit, committed_at = store.put(path, envelope, old_sha)
        except protocol.IntakeError as error:
            if error.code == 'storage_conflict':
                continue
            raise
        return receipt(store, envelope, commit, committed_at, due)
    raise protocol.IntakeError('storage_conflict', 409)
