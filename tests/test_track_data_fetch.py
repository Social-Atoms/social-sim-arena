"""The data-only collector enforces rights and isolates source failures."""
import sys
from pathlib import Path
from unittest import mock
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import fetch_track_data as fetch


def test_plan_never_calls_a_source():
    with mock.patch.object(fetch.subprocess, 'run') as run:
        rows = fetch.collect()
    run.assert_not_called()
    assert all(r['status'] == 'planned' for r in rows)


def test_blocked_rights_cannot_be_overridden_by_execute():
    with mock.patch.object(fetch.inventory, 'rights_table', return_value={}), \
         mock.patch.object(fetch.subprocess, 'run') as run:
        rows = fetch.collect(True)
    run.assert_not_called()
    assert all(r['status'] == 'blocked-rights' for r in rows)


def test_a_failed_train_capture_still_attempts_bus_and_later_sources():
    calls = []
    def runner(command, **kwargs):
        calls.append(command)
        failure = command[-1] == 'train'
        return SimpleNamespace(returncode=1 if failure else 0)
    rows = fetch.collect(True, runner)
    by_name = {r['collector']:r for r in rows}
    assert by_name['lta_train']['status'] == 'failed'
    assert by_name['lta_bus']['status'] == 'captured'
    assert by_name['ec_consumer']['status'] == 'captured'
    assert len(calls) == len(fetch.COLLECTORS)
    assert not any('ssa.refresh' in arg or 'harness' in arg or 'participants' in arg
                   for command in calls for arg in command)


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            fn()
            print('ok', name)
