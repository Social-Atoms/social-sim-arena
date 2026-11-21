"""Publish reviewed market/urban questions from held archives, without fetching.

Uses the production round builder. No provider, participant or network call is
made. Existing forecasts, scores and resolutions are retained.
"""
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ssa import domains, refresh, season, series, task_registry, urban
from ssa.adapters import bls_cpi, ecbcs, sce


def publish(path=ROOT / 'site/data.json'):
    document = json.loads((ROOT / 'questions/season0.json').read_text())
    season.require_valid(document)
    original = Path(path).read_text()
    data = json.loads(original)
    lines = original.splitlines()
    indent = len(lines[1]) - len(lines[1].lstrip()) if len(lines) > 1 else 1
    definitions = [r for r in document['rounds'] if 'consumer' in r or
                   domains.domain_of(r['series'], strict=False) == 'urban' or
                   r['series'] in ('ec_cci_eu', 'ec_cci_ea')]
    histories = dict(data.get('charts', {}))
    for sid in series.URBAN_SERIES:
        spec = series.SERIES[sid]
        rule = urban.get(spec['source'])
        if rule:
            histories[sid] = rule.history(dict(spec, sid=sid))
    histories['cpi_rent_sa_mom'] = bls_cpi.history()
    moving = sce.moving_history()
    for sid, spec in series.SERIES.items():
        if spec.get('source') == 'sce_moving':
            histories[sid] = sce.moving_series(moving, spec['sce_moving']['cell'])
    european = ecbcs.history(fetch=False)
    for geo in ('EU', 'EA'):
        histories['ec_cci_' + geo.lower()] = ecbcs.to_series(european, geo)
    prior = {r['round_id']: r for r in data['rounds']}
    resolved = {rid: row['resolution'] for rid,row in prior.items() if row.get('resolution')}
    rows, _ = refresh.build_rounds({'rounds':definitions}, histories, resolved, refresh.now_utc())
    for row in rows:
        for key in ('forecasts','forecast_count','scores','resolution'):
            if key in prior.get(row['round_id'], {}):
                row[key] = prior[row['round_id']][key]
        prior[row['round_id']] = row
    reviewed = {r['round_id'] for r in document['rounds']}
    data['rounds'] = [r for rid,r in prior.items() if rid in reviewed]
    data['tasks'] = task_registry.publish_or_raise()
    for sid, points in histories.items():
        if domains.domain_of(sid, strict=False) == 'urban' or sid in ('ec_cci_eu','ec_cci_ea'):
            data.setdefault('charts', {})[sid] = points[-104:]
    Path(path).write_text(json.dumps(data, indent=indent, ensure_ascii=False, allow_nan=False)+'\n')
    print(f'Published {len(rows)} reviewed market/urban question definitions from archived data')


if __name__ == '__main__':
    publish()
