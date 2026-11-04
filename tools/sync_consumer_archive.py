"""Persist private consumer evidence as age ciphertext on the sealed branch.

Only a fixed inert object is read from that branch. Plaintext never enters git,
Actions logs or public artifacts. Uses the arena's existing age recipient and
identities; no new access or key pair is created.
"""
import argparse
import base64
import gzip
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ssa import consumer, seal

OBJECT = 'consumer/archive.json'
DOMAIN = 'ssa.consumer.private-archive.v1'
PREFIXES = {'raw', 'parsed', 'vintages', 'evidence', 'attempts', 'locks'}
TOP_FILES = {'index.json', 'resolutions.json'}


def safe_path(name):
    path = PurePosixPath(name)
    return (not path.is_absolute() and '..' not in path.parts
            and str(path) == name and '\\' not in name
            and (name in TOP_FILES or len(path.parts) >= 2 and path.parts[0] in PREFIXES))


def pack(root):
    files = {}
    for path in sorted(Path(root).rglob('*')):
        if not path.is_file():
            continue
        name = path.relative_to(root).as_posix()
        if safe_path(name):
            if path.is_symlink():
                raise ValueError('Private archive cannot contain symbolic links')
            files[name] = base64.b64encode(path.read_bytes()).decode('ascii')
    return gzip.compress(consumer.encoded(files), mtime=0)


def restore(root, body):
    if body.get('format') != DOMAIN:
        raise ValueError('Unexpected encrypted consumer archive format')
    plain = seal._decrypt(body['ciphertext'])
    if consumer.sha256(plain) != body['sha256']:
        raise ValueError('Consumer archive integrity mismatch')
    files = json.loads(gzip.decompress(plain))
    if not isinstance(files, dict) or any(not safe_path(name) for name in files):
        raise ValueError('Invalid private archive path')
    root = consumer.private_archive(root)
    decoded = {name: base64.b64decode(value, validate=True) for name, value in files.items()}
    for name, value in decoded.items():
        dest = consumer.archive_path(root, name)
        if name in TOP_FILES or name.startswith('locks/'):
            if dest.exists() and dest.read_bytes() != value:
                raise ValueError('Restore must run before local evidence is modified')
            consumer.immutable(dest, value)
        else:
            consumer.immutable(dest, value)
    for entry in consumer.index(root)['sources'].values():
        for vintage in entry.get('vintages', []):
            consumer.read_vintage(root, vintage)
    return len(files)


def git(root, *args, check=True, text=True):
    return subprocess.run(['git', *args], cwd=root, check=check,
                          capture_output=True, text=text)


def remote_object(root, remote, branch):
    ref = f'refs/remotes/{remote}/{branch}'
    git(root, 'fetch', '--no-tags', remote, f'+refs/heads/{branch}:{ref}')
    listing = git(root, 'ls-tree', '--name-only', ref, '--', OBJECT).stdout.strip()
    if not listing:
        return ref, None
    return ref, json.loads(git(root, 'show', f'{ref}:{OBJECT}').stdout)


BASE = '.restored-sha256'   # outside PREFIXES/TOP_FILES, so never packed
PUSH_ATTEMPTS = 3


def _base_marker(archive):
    return Path(archive) / BASE


def sync(action, archive, root=ROOT, remote='origin', branch='sealed'):
    ref, previous = remote_object(root, remote, branch)
    if action == 'pull':
        marker = _base_marker(archive)
        marker.parent.mkdir(parents=True, exist_ok=True)
        if previous is None:
            # The branch was successfully fetched and the fixed path is absent:
            # a first-run initialization, not a failed network read.
            marker.write_text('none')
            return 0
        count = restore(archive, previous)
        marker.write_text(previous['sha256'])
        return count
    plain = pack(archive)
    digest = consumer.sha256(plain)
    if previous and previous.get('sha256') == digest:
        return 0
    if previous is None and not consumer.index(archive)['sources']:
        return 0
    if not consumer.index(archive)['sources']:
        raise ValueError('Refusing to persist an empty archive')
    encrypted = {'format': DOMAIN, 'sha256': digest, 'ciphertext': seal._encrypt(plain)}
    if seal._decrypt(encrypted['ciphertext']) != plain:
        raise ValueError('Archive encryption keys do not match')
    marker = _base_marker(archive)
    base = marker.read_text().strip() if marker.exists() else None
    for attempt in range(PUSH_ATTEMPTS):
        if attempt:
            ref, previous = remote_object(root, remote, branch)
        current = previous['sha256'] if previous else 'none'
        if base is not None and current != base:
            # The consumer object changed since this run restored it: someone
            # else's evidence. Never overwrite it.
            raise ValueError('Consumer archive changed on the remote since restore')
        if previous and previous.get('sha256') == digest:
            return 0
        with tempfile.TemporaryDirectory(prefix='ssa-consumer-store-') as directory:
            git(root, 'worktree', 'add', '--detach', directory, ref)
            try:
                consumer.write_json(Path(directory) / OBJECT, encrypted)
                git(directory, 'add', '--', OBJECT)
                git(directory, '-c', 'user.name=SSA consumer archive', '-c',
                    'user.email=actions@github.com', 'commit', '-m', 'Persist encrypted consumer evidence')
                # Non-fast-forward is rejected, never forced. The sealed branch
                # is shared with platform seals, so a rejection usually means
                # an unrelated seal landed: refetch and retry on top of it.
                pushed = git(directory, 'push', remote, f'HEAD:refs/heads/{branch}',
                             check=False).returncode == 0
            finally:
                git(root, 'worktree', 'remove', '--force', directory, check=False)
        if pushed:
            return 1
    raise RuntimeError('Consumer archive push rejected after retries')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['pull', 'push'])
    parser.add_argument('--archive', type=Path, default=consumer.DEFAULT_ARCHIVE)
    args = parser.parse_args()
    try:
        count = sync(args.action, args.archive)
    except Exception as error:
        # HTTP/git/key exceptions can contain private paths or credentials.
        print(f'Consumer archive {args.action} failed: {type(error).__name__}', file=sys.stderr)
        return 1
    print(f'Consumer archive {args.action}: {count} object(s)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
