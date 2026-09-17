"""Offline protocol and persistence races; never calls a model or GitHub."""
import base64
import copy
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pyrage
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from ssa import signed_forecasts as wire
from ssa.forecast_api import accept

import ast
import pathlib
ROOT_TOOL = pathlib.Path(__file__).resolve().parents[1] / 'tools/submit_signed_forecast.py'
DOC = pathlib.Path(__file__).resolve().parents[1] / 'docs/signed-submissions.md'


class Store:
    repo = 'test/fork'
    branch = 'sealed'
    def __init__(self, reg, clock):
        self.reg, self.clock, self.records = reg, clock, []
        self.conflict = False
        self.late = False
    def head(self, branch):
        return 'a' * 40 if branch == 'main' else str(len(self.records))
    def file(self, path, ref):
        if path.startswith('entrants/'):
            return self.reg, 'reg'
        if path == 'questions/season0.json':
            return {'rounds': [{'round_id': 'round-one', 'lock_at': '2030-01-01T01:00:00Z'}]}, 'season'
        return (self.records[-1][0], str(len(self.records))) if self.records else (None, None)
    def history(self, path, head):
        return list(reversed(self.records))
    def put(self, path, envelope, old):
        if self.conflict:
            self.conflict = False
            raise wire.IntakeError('storage_conflict', 409)
        when = datetime(2030, 1, 1, 1, tzinfo=timezone.utc) if self.late else self.clock()
        sha = str(len(self.records) + 1).zfill(40)
        self.records.append((copy.deepcopy(envelope), sha, wire.stamp(when)))
        return sha, wire.stamp(when)


class SignedTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        self.private = Ed25519PrivateKey.generate()
        public = base64.b64encode(self.private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
        self.reg = {'entrant_id': 'team-one', 'keys': [{'id': 'k1', 'alg': 'ed25519', 'public': public}]}
        self.age = pyrage.x25519.Identity.generate()
        self.store = Store(self.reg, lambda: self.now)
        self.env = patch.dict(os.environ, {'SSA_SIGNED_INTAKE_ENABLED': '1',
            'SSA_INTAKE_AUDIENCE': 'test', 'SSA_AGE_RECIPIENT': str(self.age.to_public()),
            'SSA_AGE_KEY_ID': 'age1', 'SSA_INTAKE_REGISTRY_BRANCH': 'main'})
        self.env.start()
        self.addCleanup(self.env.stop)
    def request(self, value=42, rid='req-1'):
        raw = wire.canonical({'round_id': 'round-one', 'entrant': 'team-one',
                              'topline': {'mean': value, 'sd': 2}})
        meta = {'entrant': 'team-one', 'key-id': 'k1', 'request-id': rid, 'timestamp': wire.stamp(self.now)}
        meta['signature'] = base64.b64encode(self.private.sign(wire.signing_bytes(meta, raw, 'test'))).decode()
        return raw, {'X-SSA-' + k: v for k, v in meta.items()}
    def send(self, req):
        return accept(*req, store=self.store, clock=lambda: self.now)
    def test_roundtrip_no_plaintext_and_interoperable_age(self):
        req = self.request()
        result = self.send(req)
        self.assertEqual(result['status'], 'accepted')
        envelope = self.store.records[-1][0]
        self.assertNotIn('topline', json.dumps(envelope))
        raw, meta = wire.open_envelope(envelope, [str(self.age)])
        self.assertEqual(raw, req[0])
        wire.verify(meta, raw, self.reg, 'test', self.now)
        cipher = base64.b64decode(envelope['ciphertext'])
        self.assertTrue(cipher.startswith(b'age-encryption.org/v1\n'))
        pyrage.decrypt(cipher, [self.age])
    def test_a_b_retry_a_does_not_rollback(self):
        a = self.request(); ra = self.send(a)
        self.now += timedelta(seconds=3)
        self.send(self.request(43, 'req-2'))
        self.assertEqual(self.send(a), ra)
        self.assertEqual(len(self.store.records), 2)
        self.assertEqual(self.store.records[-1][0]['request_id'], 'req-2')
    def test_same_id_different_content_refused(self):
        self.send(self.request())
        with self.assertRaisesRegex(wire.IntakeError, 'request_id_conflict'):
            self.send(self.request(43))
    def test_changed_body_and_wrong_audience_refused(self):
        raw, headers = self.request()
        with self.assertRaisesRegex(wire.IntakeError, 'invalid_signature'):
            self.send((raw.replace(b'42', b'43'), headers))
        with patch.dict(os.environ, {'SSA_INTAKE_AUDIENCE': 'production'}):
            with self.assertRaisesRegex(wire.IntakeError, 'invalid_signature'):
                self.send((raw, headers))
    def test_invalid_answer_never_replaces_valid(self):
        self.send(self.request())
        self.now += timedelta(seconds=3)
        raw, headers = self.request(43, 'req-2')
        body = json.loads(raw); body['topline']['sd'] = 0
        raw = wire.canonical(body)
        meta = wire.metadata(headers)
        headers['X-SSA-signature'] = base64.b64encode(self.private.sign(wire.signing_bytes(meta, raw, 'test'))).decode()
        with self.assertRaisesRegex(wire.IntakeError, 'invalid_forecast'):
            self.send((raw, headers))
        self.assertEqual(len(self.store.records), 1)
    def test_conflict_retry_and_late_persistence(self):
        self.store.conflict = True
        self.assertEqual(self.send(self.request())['status'], 'accepted')
        self.now += timedelta(seconds=3)
        self.store.late = True
        self.assertEqual(self.send(self.request(44, 'req-2'))['status'], 'late')
    def test_receipt_retry_after_deadline(self):
        req = self.request(); expected = self.send(req)
        self.now += timedelta(hours=2)
        self.assertEqual(self.send(req), expected)
        with self.assertRaisesRegex(wire.IntakeError, 'round_closed'):
            self.send(self.request(44, 'new'))
    def test_revocation_blocks_new_and_retry(self):
        req = self.request(); self.send(req)
        self.reg['keys'][0]['revoked'] = True
        with self.assertRaisesRegex(wire.IntakeError, 'unknown_key'):
            self.send(req)
    def test_tampered_envelope_and_wrong_key(self):
        self.send(self.request()); env = copy.deepcopy(self.store.records[-1][0])
        env['received_at'] = '2020-01-01T00:00:00Z'
        with self.assertRaises(wire.IntakeError):
            wire.open_envelope(env, [str(self.age)])
        with self.assertRaises(wire.IntakeError):
            wire.open_envelope(self.store.records[-1][0], [str(pyrage.x25519.Identity.generate())])
    def test_timeout_after_write_retry_recovers_receipt(self):
        request = self.request()
        put = self.store.put
        def dropped_response(*args):
            put(*args)
            raise wire.IntakeError('storage_unavailable', 503)
        with patch.object(self.store, 'put', side_effect=dropped_response):
            with self.assertRaisesRegex(wire.IntakeError, 'storage_unavailable'):
                self.send(request)
        recovered = self.send(request)
        self.assertEqual(recovered['commit'], self.store.records[0][1])
        self.assertEqual(len(self.store.records), 1)

    def test_concurrent_winner_is_preserved_before_retry(self):
        competitor_raw, competitor_headers = self.request(41, 'competitor')
        competitor = wire.seal(competitor_raw, wire.metadata(competitor_headers),
            'a' * 40, 'a' * 40, 'test', str(self.age.to_public()), 'age1', self.now)
        put = self.store.put
        def raced(path, envelope, old):
            if not self.store.records:
                put(path, competitor, old)
                self.now += timedelta(seconds=3)
                raise wire.IntakeError('storage_conflict', 409)
            self.assertEqual(old, '1')
            return put(path, envelope, old)
        with patch.object(self.store, 'put', side_effect=raced):
            result = self.send(self.request(43, 'retrying-writer'))
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual([entry[0]['request_id'] for entry in self.store.records],
                         ['competitor', 'retrying-writer'])

    def test_platform_route_collision_is_explicit(self):
        self.store.records.append(({'version': 1}, 'b' * 40, wire.stamp(self.now)))
        with self.assertRaisesRegex(wire.IntakeError, 'submission_route_conflict'):
            self.send(self.request())

    def test_duplicate_json_fields_rejected(self):
        with self.assertRaisesRegex(wire.IntakeError, 'invalid_json'):
            wire.parse_body(b'{"entrant":"one","entrant":"two"}')


if __name__ == '__main__':
    unittest.main()


class ClientDefaults(unittest.TestCase):
    """Where to send a forecast and what its signature is scoped to are the
    arena's, not the participant's, and every participant was being told both by
    hand. They are constants now and the client fills them in."""

    def parser_defaults(self):
        import ast
        source = ROOT_TOOL.read_text()
        found = {}
        for node in ast.walk(ast.parse(source)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == 'add_argument' and node.args
                    and isinstance(node.args[0], ast.Constant)):
                default = next((k.value for k in node.keywords if k.arg == 'default'), None)
                found[node.args[0].value] = default
        return found

    def test_the_client_asks_only_for_what_the_participant_owns(self):
        defaults = self.parser_defaults()
        for flag, constant in (('--url', 'ORIGIN'), ('--audience', 'AUDIENCE')):
            node = defaults.get(flag)
            self.assertIsInstance(node, ast.Name, f'{flag} has no default')
            self.assertEqual(node.id, constant,
                             f'{flag} must default to the constant, not a literal')

    def test_the_constants_are_usable_and_the_audience_is_not_a_secret(self):
        self.assertTrue(wire.ORIGIN.startswith('https://'))
        self.assertTrue(wire.AUDIENCE)
        self.assertNotIn(' ', wire.AUDIENCE)
        # It goes into the signed bytes, which is why it separates environments
        # and why publishing it costs nothing.
        signed = wire.signing_bytes({'entrant': 'e', 'key-id': 'k', 'request-id': 'r',
                                     'timestamp': wire.stamp(datetime(2030, 1, 1, tzinfo=timezone.utc))},
                                    b'{}', wire.AUDIENCE)
        self.assertIn(wire.AUDIENCE.encode(), signed)

    def test_the_documented_command_is_the_one_that_works(self):
        doc = DOC.read_text()
        self.assertNotIn('YOUR-PLATFORM', doc)
        self.assertNotIn('YOUR-PUBLISHED-ENVIRONMENT-ID', doc)
        self.assertIn(wire.AUDIENCE, doc)
