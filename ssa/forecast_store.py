"""GitHub-only persistence. A file update atomically stores receipt + ciphertext.

History queries are pinned to a head, bounded and fail closed. App tokens never
appear in URLs or exceptions. No repository credentials are accepted from users.
"""
import base64
import json
import os
import time
from urllib.parse import quote

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from ssa.signed_forecasts import IntakeError, canonical, utc


def b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


class GitHubStore:
    def __init__(self, repo=None, branch=None):
        self.repo = repo or os.environ['SSA_INTAKE_REPO']
        self.branch = branch or os.environ.get('SSA_INTAKE_BRANCH', 'sealed')
        if self.branch in ('main', 'dev') or len(self.repo.split('/')) != 2:
            raise IntakeError('invalid_storage_configuration', 503)
        self.base = 'https://api.github.com/repos/' + self.repo
        self.session = requests.Session()
        self.session.headers.update({'Accept': 'application/vnd.github+json'})
        self.session.headers['Authorization'] = 'Bearer ' + self._token()

    def _token(self):
        now = int(time.time())
        header = b64url(canonical({'alg': 'RS256', 'typ': 'JWT'}))
        payload = b64url(canonical({'iat': now - 60, 'exp': now + 540,
                                  'iss': os.environ['SSA_GITHUB_APP_ID']}))
        message = (header + '.' + payload).encode()
        private = serialization.load_pem_private_key(os.environ['SSA_GITHUB_APP_PRIVATE_KEY'].encode(), None)
        jwt = message.decode() + '.' + b64url(private.sign(message, padding.PKCS1v15(), hashes.SHA256()))
        result = requests.post('https://api.github.com/app/installations/' +
                               os.environ['SSA_GITHUB_INSTALLATION_ID'] + '/access_tokens',
                               headers={'Authorization': 'Bearer ' + jwt,
                                        'Accept': 'application/vnd.github+json'},
                               json={'repositories': [self.repo.split('/')[1]],
                                     'permissions': {'contents': 'write'}}, timeout=20)
        if result.status_code != 201:
            raise IntakeError('storage_unavailable', 503)
        return result.json()['token']

    def request(self, method, path, **kwargs):
        try:
            response = self.session.request(method, self.base + path, timeout=20, **kwargs)
        except requests.RequestException:
            raise IntakeError('storage_unavailable', 503) from None
        if response.status_code in (409, 422):
            raise IntakeError('storage_conflict', 409)
        if response.status_code == 404:
            return None
        if not response.ok:
            raise IntakeError('storage_unavailable', 503)
        return response.json()

    def head(self, branch):
        doc = self.request('GET', '/git/ref/heads/' + quote(branch, safe=''))
        if not doc:
            raise IntakeError('storage_branch_missing', 503)
        return doc['object']['sha']

    def file(self, path, ref):
        result = self.request('GET', '/contents/' + quote(path, safe='/'), params={'ref': ref})
        if result is None:
            return None, None
        if result.get('encoding') != 'base64':
            raise IntakeError('invalid_storage_record', 503)
        return json.loads(base64.b64decode(result['content'])), result['sha']

    def history(self, path, head):
        entries = []
        # A per-entrant-round limit bounds serverless work and submission abuse.
        # At capacity reject new writes; never silently ignore old request IDs.
        for page in range(1, 4):
            rows = self.request('GET', '/commits', params={'path': path, 'sha': head,
                                                        'per_page': 100, 'page': page}) or []
            for row in rows:
                line = next((line for line in row['commit']['message'].splitlines()
                             if line.startswith('SSA-Receipt: ')), None)
                if line:
                    value = json.loads(line[len('SSA-Receipt: '):])
                else:
                    value, _ = self.file(path, row['sha'])
                if value is not None:
                    entries.append((value, row['sha'], row['commit']['committer']['date']))
            if len(rows) < 100:
                return entries
        raise IntakeError('submission_history_limit', 429)

    def put(self, path, value, old_sha):
        public = {k: v for k, v in value.items() if k != 'ciphertext'}
        payload = {'branch': self.branch, 'message': 'Sealed forecast receipt ' + value['request_id'] +
                   '\n\nSSA-Receipt: ' + canonical(public).decode(),
                   'content': base64.b64encode(canonical(value)).decode()}
        if old_sha:
            payload['sha'] = old_sha
        result = self.request('PUT', '/contents/' + quote(path, safe='/'), json=payload)
        if not result:
            raise IntakeError('storage_unavailable', 503)
        return result['commit']['sha'], result['commit']['committer']['date']


def receipt(store, value, commit, committed_at, due):
    return {'entrant': value['entrant'], 'round_id': value['round_id'],
            'request_id': value['request_id'], 'received_at': value['received_at'],
            'persisted_at': committed_at,
            'status': 'accepted' if utc(committed_at) < due and utc(value['received_at']) < due else 'late',
            'ciphertext_sha256': value['ciphertext_sha256'], 'commit': commit,
            'url': f'https://github.com/{store.repo}/commit/{commit}'}
