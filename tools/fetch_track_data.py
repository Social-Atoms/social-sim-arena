"""Collect approved market/urban sources without model or participant calls.

Default is a read-only plan. --execute captures missing public archives;
consumer evidence is handled separately by sync_consumer_archive in the workflow.
Each collector is independent. Failed attempts are recorded and make the job red
only after successful captures have had a chance to be persisted.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ssa import inventory

COLLECTORS = (
    ('seattle', 'seattle_cad', ['-m', 'ssa.adapters.seattle_cad', 'capture']),
    ('nyc311', 'nyc311', ['-m', 'ssa.adapters.nyc311', 'capture']),
    ('bls_rent', 'bls_cpi', ['-m', 'ssa.adapters.bls_cpi', 'capture']),
    ('coe', 'sg_coe', ['-m', 'ssa.adapters.sg_coe', 'capture']),
    ('lta_train', 'sg_lta_pv', ['-m', 'ssa.adapters.sg_lta_pv', 'capture', 'train']),
    ('lta_bus', 'sg_lta_pv', ['-m', 'ssa.adapters.sg_lta_pv', 'capture', 'bus']),
    ('sce', 'sce', ['-c', 'from ssa.adapters import sce; b=sce.fetch_bytes(); r=sce.parse(b); sce.archive(b, newest=r[-1]["date"])']),
    ('ec_consumer', 'ecbcs', ['-c', 'from ssa.adapters import ecbcs; d=[]; ecbcs.history(diagnostics=d); raise SystemExit(bool(d))']),
)


def collect(execute=False, runner=None):
    runner = runner or subprocess.run
    rights = inventory.rights_table()
    results = []
    for name, source, command in COLLECTORS:
        row = {'collector': name, 'source': source}
        if rights.get(source) not in inventory.GENERATING_RIGHTS:
            row['status'] = 'blocked-rights'
        elif not execute:
            row['status'] = 'planned'
        else:
            try:
                # Keys are inherited environment variables, never arguments.
                # Raw collector output is deliberately not copied into the log.
                result = runner([sys.executable, *command], cwd=ROOT,
                                capture_output=True, text=True, timeout=480)
                row.update(status='captured' if result.returncode == 0 else 'failed',
                           exit_code=result.returncode)
            except (OSError, subprocess.TimeoutExpired) as error:
                row.update(status='failed', error_type=type(error).__name__)
        results.append(row)
        print(f"{name}: {row['status']}", flush=True)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    rows = collect(args.execute)
    if args.execute:
        payload = {'attempted_at': datetime.now(timezone.utc).isoformat(), 'collectors': rows}
        path = ROOT / 'sources/track-fetch-status.json'
        path.write_text(json.dumps(payload, indent=2) + '\n')
    return int(any(r['status'] == 'failed' for r in rows))


if __name__ == '__main__':
    raise SystemExit(main())
