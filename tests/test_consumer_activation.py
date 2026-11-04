"""Website admission, production collection and encrypted runner persistence."""
import copy
import json
import os
import subprocess
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pyrage

from ssa import consumer, consumer_round, inventory, season, task_registry
from tools import collect_consumer_rounds, publish_consumer, sync_consumer_archive
from tests.test_consumer_pipeline import current, moment


class ActivationTests(unittest.TestCase):
    def test_every_source_is_registered_and_permission_sources_have_no_live_question(self):
        tasks = {row['id']: row for row in task_registry.load()}
        rows = json.loads((consumer.ROOT / 'questions/season0.json').read_text())['rounds']
        definitions = [r for r in rows if 'consumer' in r]
        self.assertEqual(len(definitions), 3)
        self.assertEqual(len([key for key in tasks if key.startswith('consumer_')]), 7)
        self.assertEqual(tasks['consumer_netflix']['status'], 'permission-needed')
        self.assertEqual(tasks['consumer_acsi']['status'], 'permission-needed')
        self.assertEqual(tasks['consumer_michigan']['status'], 'permission-needed')
        self.assertTrue(all(consumer_round.source_for(r) in
                            {'consumer_sce_extensions', 'consumer_michigan_inflation'} for r in definitions))
        self.assertEqual(season.validate_document({'rounds': definitions}), [])

    def test_automated_collection_deduplicates_workbook_and_does_not_fetch_permission_sources(self):
        doc = json.loads((consumer.ROOT / 'questions/season0.json').read_text())
        fake_index = {'sources': {'sce': {'last_attempt': {'status': 'collected'}}}}
        with mock.patch.object(collect_consumer_rounds.consumer_data, 'collect_source') as request, \
                mock.patch.object(consumer, 'index', return_value=fake_index):
            failures = collect_consumer_rounds.collect(doc, now=moment('2026-11-04T00:00:00Z'))
        self.assertEqual(failures, [])
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[0], 'sce')

    def test_missing_or_bad_collection_is_reported_per_source(self):
        doc = json.loads((consumer.ROOT / 'questions/season0.json').read_text())
        with mock.patch.object(collect_consumer_rounds.consumer_data, 'collect_source'), \
                mock.patch.object(consumer, 'index', return_value={'sources': {}}):
            failures = collect_consumer_rounds.collect(doc, now=moment('2026-11-04T00:00:00Z'))
        self.assertEqual(failures, [('sce', 'missing')])

    def test_publish_preserves_unrelated_live_results(self):
        doc = json.loads((consumer.ROOT / 'questions/season0.json').read_text())
        old = {'rounds': [{'round_id': 'existing-round', 'scores': {'old-entrant': {'skill': 0.3}}}],
               'sources': {'old-source': {'sha256': 'keep'}}, 'leaderboard': {'entries': ['keep']}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'data.json'
            consumer.write_json(path, old)
            with mock.patch.object(consumer_round, 'history', return_value=[]):
                publish_consumer.publish(path, doc, now=moment('2026-11-08T00:00:00Z'))
            new = json.loads(path.read_text())
            self.assertIn(old['rounds'][0], new['rounds'])
            self.assertEqual(old['leaderboard'], new['leaderboard'])
            self.assertEqual(old['sources'], new['sources'])
            self.assertEqual(len(new['rounds']), 4)

    def test_private_evidence_survives_a_fresh_runner_and_is_ciphertext_only_in_git(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            remote = base / 'remote.git'
            repo = base / 'repo'
            repo.mkdir()
            def git(cwd, *args):
                return subprocess.run(['git', *args], cwd=cwd, capture_output=True, check=True)
            git(base, 'init', '--bare', str(remote))
            git(repo, 'init')
            git(repo, '-c', 'user.name=test', '-c', 'user.email=test@invalid',
                'commit', '--allow-empty', '-m', 'initialize')
            git(repo, 'branch', 'sealed')
            git(repo, 'remote', 'add', 'origin', str(remote))
            git(repo, 'push', 'origin', 'sealed')
            archive = base / 'private-first-run'
            body = current([('October', 4.7)])
            consumer.ingest('michigan_current', body,
                            'https://www.sca.isr.umich.edu/files/tbcpx1px5.csv', archive,
                            captured_at='2026-10-23T14:01:00Z')
            consumer.write_json(archive / 'locks/consumer-example.json', {'history': [{'value': 4.7}]})
            identity = pyrage.x25519.Identity.generate()
            with mock.patch.dict(os.environ, {'SSA_AGE_RECIPIENT': str(identity.to_public()),
                    'SSA_AGE_IDENTITIES': json.dumps([str(identity)])}):
                self.assertEqual(sync_consumer_archive.sync('pull', archive, root=repo), 0)
                self.assertEqual(sync_consumer_archive.sync('push', archive, root=repo), 1)
                fresh = base / 'private-fresh-run'
                self.assertGreater(sync_consumer_archive.sync('pull', fresh, root=repo), 1)
                self.assertEqual(sync_consumer_archive.pack(fresh), sync_consumer_archive.pack(archive))
                self.assertEqual(sync_consumer_archive.sync('push', fresh, root=repo), 0)
                # A platform seal landing between fetch and push is retried on
                # top of it rather than failing the whole refresh.
                other = base / 'other'
                git(base, 'clone', '-q', '-b', 'sealed', str(remote), str(other))
                real_remote_object = sync_consumer_archive.remote_object
                calls = []
                def seal_lands_after_fetch(*args, **kwargs):
                    result = real_remote_object(*args, **kwargs)
                    if not calls:
                        (other / 'seal.txt').write_text('unrelated seal')
                        git(other, 'add', 'seal.txt')
                        git(other, '-c', 'user.name=test', '-c', 'user.email=test@invalid',
                            'commit', '-m', 'seal')
                        git(other, 'push', '-q', 'origin', 'sealed')
                    calls.append(1)
                    return result
                consumer.write_json(fresh / 'locks/consumer-second.json', {'history': [{'value': 4.8}]})
                stale = base / 'private-stale-run'
                sync_consumer_archive.sync('pull', stale, root=repo)
                with mock.patch.object(sync_consumer_archive, 'remote_object', seal_lands_after_fetch):
                    self.assertEqual(sync_consumer_archive.sync('push', fresh, root=repo), 1)
                self.assertEqual(len(calls), 2)
                git(repo, 'fetch', '-q', 'origin', 'sealed:refs/remotes/origin/sealed')
                self.assertIn(b'seal.txt', git(repo, 'ls-tree', '--name-only', 'origin/sealed').stdout)
                # A run that restored an older consumer object never overwrites a newer one.
                consumer.write_json(stale / 'locks/consumer-third.json', {'history': [{'value': 4.9}]})
                with self.assertRaisesRegex(ValueError, 'changed on the remote'):
                    sync_consumer_archive.sync('push', stale, root=repo)
                public = git(repo, 'show', 'origin/sealed:consumer/archive.json').stdout
                self.assertNotIn(b'EXPECTED CHANGE IN PRICES', public)
                self.assertNotIn(b'raw_file', public)
                envelope = json.loads(public)
                bad = copy.deepcopy(envelope)
                bad['sha256'] = 'wrong'
                with self.assertRaisesRegex(ValueError, 'integrity'):
                    sync_consumer_archive.restore(base / 'bad-restore', bad)
                traversal = sync_consumer_archive.seal._encrypt(__import__('gzip').compress(
                    consumer.encoded({'../../escape': 'YQ=='}), mtime=0))
                plain = sync_consumer_archive.seal._decrypt(traversal)
                with self.assertRaisesRegex(ValueError, 'path'):
                    sync_consumer_archive.restore(base / 'bad-path', {
                        'format': sync_consumer_archive.DOMAIN, 'sha256': consumer.sha256(plain),
                        'ciphertext': traversal})

    @unittest.skipUnless(shutil.which('node'), 'Node is needed for the website render check')
    def test_registered_and_live_tasks_render_on_the_actual_website(self):
        subprocess.run(['node', 'tests/site/consumer_tasks.js'], cwd=consumer.ROOT, check=True,
                       capture_output=True, text=True)


if __name__ == '__main__':
    unittest.main()
