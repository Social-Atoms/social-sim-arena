"""Collect only admitted consumer sources needed by the reviewed season."""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ssa import batches, consumer, consumer_round, inventory
from tools import consumer_data


def collect(document, now=None, root=None):
    now = now or datetime.now(timezone.utc)
    root = root or consumer.DEFAULT_ARCHIVE
    requests = {}
    for row in document.get('rounds', []):
        if 'consumer' not in row:
            continue
        spec = consumer_round.spec_for(row)
        if inventory.rights_table().get(consumer_round.source_for(row)) not in inventory.GENERATING_RIGHTS:
            continue
        if now < batches.published_at(row['lock_at']) or now > consumer_round.timestamp(spec['capture_until']):
            continue
        source = spec['source']
        url, context = consumer_data.URLS[source], None
        if source == 'nyt':
            url = f"https://api.nytimes.com/svc/books/v3/lists/{spec['published_date']}/{spec['list_name']}.json"
        if source == 'steam':
            from datetime import timezone as utc
            context = {'country_code': spec['country'], 'start_date': int(
                datetime.fromisoformat(row['ranking']['week_start']).replace(tzinfo=utc.utc).timestamp())}
        requests[(source, url, json.dumps(context, sort_keys=True))] = context
        if source == 'michigan_current':
            # Finals are legitimate prior-month context, never invented preliminaries.
            requests[('michigan_finals', consumer_data.URLS['michigan_finals'], 'null')] = None
    failures = []
    for (source, url, _), context in requests.items():
        if source == 'mint':
            state = consumer.index(root)['sources'].get(source, {}).get('last_attempt', {})
            if state.get('status') != 'collected':
                failures.append((source, 'manual_import_required'))
            continue
        consumer_data.collect_source(source, root, timeout=30, fetch_sce=True, url=url, request_context=context)
        state = consumer.index(root)['sources'].get(source, {}).get('last_attempt', {})
        if state.get('status') != 'collected':
            failures.append((source, state.get('status', 'missing')))
    return failures


def main():
    from ssa import envfile
    envfile.load()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--season', type=Path, default=ROOT / 'questions/season0.json')
    args = parser.parse_args()
    failures = collect(json.loads(args.season.read_text()))
    for source, status in failures:
        print(f'Consumer source {source}: {status}')
    return bool(failures)


if __name__ == '__main__':
    raise SystemExit(main())
