"""Season 0 closeout: lock window, settlement accounting and explicit final sign-off."""
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'registry/season0.json'
CERTIFIED_ROUNDS = ('midterm-2026-house-margin', 'midterm-2026-house-seats')


def load():
    return json.loads(CONFIG.read_text())


def in_window(row, since=None, until=None):
    config = load()
    if valid_disposition(config.get('dispositions', {}).get(row.get('round_id'), {})):
        return False
    return (since or config['since']) <= row.get('lock_at', '') <= (until or config['until'])


def valid_disposition(decision):
    return bool(decision.get('status') in ('cancelled', 'withdrawn') and decision.get('reason')
                and decision.get('evidence') and decision.get('approved_by'))


def metadata(rounds, now=None, config=None):
    config = config or load()
    now = now or datetime.now(timezone.utc)
    if isinstance(now, datetime):
        now = now.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    admitted = [r for r in rounds if config['since'] <= r.get('lock_at', '') <= config['until']
                and r.get('scored') is not False]
    pending, settled, disposed = [], [], []
    decisions = config.get('dispositions', {})
    for row in admitted:
        rid = row['round_id']
        decision = decisions.get(rid, {})
        if valid_disposition(decision):
            disposed.append({'round_id': rid, **decision})
        elif row.get('scores'):
            settled.append(rid)
        else:
            pending.append({'round_id': rid, 'status': row.get('status', 'unscored'),
                            'release_at': row.get('release_at'),
                            'reason': row.get('baseline_note') or row.get('resolution_note')})
    approval = config.get('final_approval') or {}
    certifications = approval.get('certifications', {})
    approved = bool(approval.get('approved_by') and approval.get('approved_at')
                    and config['until'] < approval['approved_at'] <= now
                    and all(isinstance(certifications.get(rid), str)
                            and certifications[rid].startswith('https://') for rid in CERTIFIED_ROUNDS))
    final_ready = now >= config['until'] and not pending and approved
    return {'since': config['since'], 'until': config['until'],
            'phase': 'ongoing' if now < config['until'] else 'final' if final_ready else 'preliminary',
            'total_rounds': len(admitted), 'settled_rounds': len(settled),
            'pending_rounds': pending, 'pending_count': len(pending),
            'dispositions': disposed, 'final_approved': final_ready,
            'founding_entrant_ids': list(config['founding_entrant_ids'])}


def save_snapshot(data, directory=None):
    """Write the first post-close preliminary/final public result, once only.

    Snapshot inputs contain public scores and registry state, never forecasts or
    private receipts. They reproduce the exact roster and both Overall metrics.
    """
    import hashlib
    import subprocess
    meta = data.get('season0_main', {})
    stage = meta.get('phase')
    if stage not in ('preliminary', 'final'):
        return None
    directory = Path(directory or ROOT / 'site/seasons/season0')
    path = directory / (stage + '.json')
    if path.exists():
        return path
    payload = {'season0_main': meta, 'entrants': data.get('entrants', []),
               'retired': data.get('retired', {}),
               'rounds': [{k: r[k] for k in ('round_id', 'lock_at', 'release_at', 'status',
                                            'scores', 'resolution') if k in r}
                          for r in data.get('rounds', [])]}
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    script = 'const fs=require("fs"),p=require(process.argv[1]),d=JSON.parse(fs.readFileSync(0,"utf8"));process.stdout.write(JSON.stringify(p.build(d,{scope:"s0",field:"mixed"})));'
    result = subprocess.run(['node', '-e', script, str(ROOT / 'site/pairwise.js')],
                            input=raw, capture_output=True, text=True, check=True)
    snapshot = {'stage': stage, 'captured_at': datetime.now(timezone.utc).isoformat(),
                'data_generated_at': data.get('generated_at'),
                'input_sha256': hashlib.sha256(raw.encode()).hexdigest(),
                'input': payload, 'standings': json.loads(result.stdout)}
    directory.mkdir(parents=True, exist_ok=True)
    import os
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', dir=directory, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(snapshot, stream, ensure_ascii=False, indent=1)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.link(temporary, path)  # atomic, and never replaces an existing snapshot
    except FileExistsError:
        pass
    finally:
        temporary.unlink()
    return path


def publish_artifacts(data, directory=None):
    directory = Path(directory or ROOT / 'site/seasons/season0')
    meta = data['season0_main']
    final = directory / 'final.json'
    # Once formally published, a transient loss of scores or later retirement
    # cannot reopen the season. Corrections require an explicit organizer action.
    if not final.exists():
        save_snapshot(data, directory)
    if final.exists():
        saved = json.loads(final.read_text())
        original = saved['input']['season0_main']
        if original['until'] != meta['until'] or original['since'] != meta['since']:
            raise ValueError('Final snapshot has a different season boundary')
        data['season0_main'] = meta = dict(original)
        meta['frozen_standings'] = saved['standings']
    meta['snapshots'] = {phase: f'seasons/season0/{phase}.json'
                         for phase in ('preliminary', 'final')
                         if (directory / (phase + '.json')).exists()}
