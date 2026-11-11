"""Closure phases, exact lock boundary, explicit final approval and immutable snapshots."""
import copy
import json
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ssa import refresh, season0


def row(rid, lock, scored=True):
    result={'round_id':rid,'lock_at':lock,'release_at':'2026-12-20T00:00:00Z',
            'status':'resolved' if scored else 'awaiting_resolution'}
    if scored:result['scores']={'apodex-futureflow':{'crps':1,'skill':.5},'persistence':{'crps':2,'skill':0}}
    return result


def test_inclusive_boundary_and_late_resolution():
    end=season0.load()['until']
    rows=[row('at',end),row('after','2026-11-15T14:00:01Z'),row('pending',end,False)]
    b=refresh.build_main_board(rows,now='2026-11-16T00:00:00Z')
    assert b['round_ids']==['at'] and b['pending_count']==1 and b['total_rounds']==2
    assert b['phase']=='preliminary'
    assert season0.metadata(rows,end)['phase']=='preliminary'
    assert b['entries'][0]['mean_skill']==.5
    rows[2]['scores']=rows[0]['scores']
    b=refresh.build_main_board(rows,now='2026-12-20T00:00:00Z')
    assert b['round_ids']==['at','pending'] and b['phase']=='preliminary'


def test_unscoreable_seats_and_missing_certification_block_final():
    c=season0.load(); rows=[row('midterm-2026-house-seats',c['until'],False)]
    rows[0]['scoreable']=False
    assert season0.metadata(rows,'2026-12-20T00:00:00Z',c)['pending_count']==1
    c['final_approval']={'approved_by':'organizer','approved_at':'2026-12-19T00:00:00Z','certifications':{}}
    rows[0]['scores']={'a':{'crps':1,'skill':0}}
    assert season0.metadata(rows,'2026-12-20T00:00:00Z',c)['phase']=='preliminary'
    c['final_approval']['certifications']={rid:'https://example.org/certified' for rid in season0.CERTIFIED_ROUNDS}
    assert season0.metadata(rows,'2026-12-20T00:00:00Z',c)['phase']=='final'
    assert season0.metadata(rows,'2026-11-14T00:00:00Z',c)['phase']=='ongoing'


def test_snapshot_preserves_scores_and_excludes_forecast_receipts():
    r=row('at',season0.load()['until']);r['forecasts']={'xumi':{'sealed':True}}
    data={'rounds':[r],'entrants':[{'entrant_id':id,'type':typ} for id,typ in [('apodex-futureflow','participant'),('persistence','baseline')]],
          'season0_main':refresh.build_main_board([r],now='2026-11-16T00:00:00Z')}
    original=copy.deepcopy(data)
    with tempfile.TemporaryDirectory() as tmp:
        p=season0.save_snapshot(data,tmp);raw=p.read_bytes();snapshot=json.loads(raw)
        assert snapshot['standings']['round_ids']==['at']
        assert 'forecasts' not in snapshot['input']['rounds'][0]
        data['rounds'][0]['scores']['apodex-futureflow']['crps']=99
        season0.save_snapshot(data,tmp)
        assert p.read_bytes()==raw
    assert original['rounds'][0]['forecasts']==data['rounds'][0]['forecasts']




def test_final_snapshot_freezes_roster_and_both_metrics():
    r=row('at',season0.load()['until'])
    data={'rounds':[r], 'entrants':[{'entrant_id':'apodex-futureflow','type':'participant'},
                                  {'entrant_id':'persistence','type':'baseline'}],
          'season0_main':refresh.build_main_board([r],now='2026-11-16T00:00:00Z')}
    data['season0_main'].update(phase='final',final_approved=True)
    with tempfile.TemporaryDirectory() as tmp:
        season0.publish_artifacts(data,tmp)
        frozen=copy.deepcopy(data['season0_main']['frozen_standings'])
        data['retired']={'apodex-futureflow':{'reason':'later retirement'}}
        data['rounds'][0]['scores']['apodex-futureflow']={'crps':10,'skill':-4}
        season0.publish_artifacts(data,tmp)
        assert data['season0_main']['frozen_standings']==frozen
        assert frozen['entries'][0]['mean_skill']==.5
        assert frozen['entries'][0]['pairwise']==100


def test_disposed_scored_round_does_not_enter_the_s0_denominator():
    from unittest import mock
    config=season0.load()
    config['dispositions']={'disposed':{'status':'cancelled','reason':'invalid target',
                                      'evidence':'https://example.org/decision','approved_by':'organizer'}}
    rows=[row('keep',config['until']),row('disposed',config['until'])]
    with mock.patch.object(season0,'load',return_value=config):
        board=refresh.build_main_board(rows)
    assert board['round_ids']==['keep']
    assert board['scored_rounds']==1 and len(board['dispositions'])==1
    assert len(rows)==2 and 'scores' in rows[1]


def test_existing_final_survives_transient_missing_scores():
    r=row('at',season0.load()['until'])
    data={'rounds':[r],'entrants':[{'entrant_id':'apodex-futureflow','type':'participant'},
                                  {'entrant_id':'persistence','type':'baseline'}],
          'season0_main':refresh.build_main_board([r],now='2026-12-01T00:00:00Z')}
    data['season0_main'].update(phase='final',final_approved=True)
    with tempfile.TemporaryDirectory() as tmp:
        season0.publish_artifacts(data,tmp)
        frozen=copy.deepcopy(data['season0_main']['frozen_standings'])
        data['rounds'][0].pop('scores')
        data['season0_main']=refresh.build_main_board(data['rounds'],now='2026-12-02T00:00:00Z')
        assert data['season0_main']['phase']=='preliminary'
        season0.publish_artifacts(data,tmp)
        assert data['season0_main']['phase']=='final'
        assert data['season0_main']['frozen_standings']==frozen
        assert not (Path(tmp)/'preliminary.json').exists()


if __name__=='__main__':
    for name,fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):fn();print('ok',name)
