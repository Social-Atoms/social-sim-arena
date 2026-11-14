"""Publish consumer tasks and reviewed questions without refreshing other sources.

Uses the production builder and scorers; never fabricates a forecast or outcome.
The existing payload's unrelated rounds, sources and evidence remain intact.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ssa import consumer, consumer_round, refresh, season, task_registry


def publish(path, document, now=None):
    now = now or refresh.now_utc()
    season.require_valid(document)
    data = json.loads(Path(path).read_text())
    definitions = [r for r in document['rounds'] if 'consumer' in r]
    ids = {r['round_id'] for r in definitions}
    resolved = {r['round_id']: r['resolution'] for r in data['rounds']
                if r['round_id'] in ids and r.get('resolution')}
    rows, _ = refresh.build_rounds({'rounds': definitions}, {}, resolved, now)
    by_id = {r['round_id']: r for r in data['rounds']}
    for row in rows:
        prior = by_id.get(row['round_id'], {})
        for field in ('forecasts', 'forecast_count', 'scores', 'resolution'):
            if field in prior:
                row[field] = prior[field]
        by_id[row['round_id']] = row
    data['rounds'] = list(by_id.values())
    data['tasks'] = task_registry.publish_or_raise()
    for definition in definitions:
        points = consumer_round.history(definition, before=now)
        data.setdefault('charts', {})[definition['series']] = [
            {'date': p['date'], 'value': p['value']} for p in points[-24:] if 'value' in p]
    original = Path(path).read_text()
    second_line = original.splitlines()[1] if '\n' in original else ''
    indent = len(second_line) - len(second_line.lstrip()) if second_line else None
    Path(path).write_text(json.dumps(data, indent=indent, ensure_ascii=False, allow_nan=False)
                         + '\n')
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'site/data.json')
    args = parser.parse_args()
    rows = publish(args.output, json.loads((ROOT / 'questions/season0.json').read_text()))
    print(f'Published {len(rows)} consumer questions and seven task registrations')


if __name__ == '__main__':
    main()
