"""Offline dedicated-branch synchronization tests; no network or providers."""
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

import pyrage

from ssa import seal, signing
from tools import sync_platform_seals as sync


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


class PlatformSealSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.remote = os.path.join(self.tmp.name, "remote.git")
        self.repo = os.path.join(self.tmp.name, "repo")
        subprocess.run(["git", "init", "--bare", "-q", self.remote], check=True)
        os.makedirs(self.repo)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "test")
        git(self.repo, "config", "user.email", "test@example.com")
        os.makedirs(os.path.join(self.repo, "site"))
        os.makedirs(os.path.join(self.repo, "questions"))
        self.private, self.public = signing.generate()
        with open(os.path.join(self.repo, "site", "keys.json"), "w") as fh:
            json.dump({"keys": [{"key_id": "live", "public_key": self.public}]}, fh)
        with open(os.path.join(self.repo, "questions", "season0.json"), "w") as fh:
            json.dump({"rounds": [{"round_id": "round-one",
                                    "lock_at": "2030-01-02T00:00:00Z"}]}, fh)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "main")
        git(self.repo, "remote", "add", "origin", self.remote)
        git(self.repo, "push", "-u", "origin", "main")

        # Seed the data branch with a signed-API envelope. Platform sync must
        # preserve it byte-for-byte and must never attempt to interpret it.
        git(self.repo, "checkout", "--orphan", "sealed")
        git(self.repo, "rm", "-qrf", ".")
        api_path = os.path.join(self.repo, "sealed", "round-one", "api-client.json")
        os.makedirs(os.path.dirname(api_path))
        self.api = {"version": "ssa-signed-forecast-v1",
                    "ciphertext": "opaque-api-envelope"}
        with open(api_path, "w") as fh:
            json.dump(self.api, fh)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "seed sealed branch")
        git(self.repo, "push", "-u", "origin", "sealed")
        git(self.repo, "checkout", "main")

        self.age = pyrage.x25519.Identity.generate()
        self.env = mock.patch.dict(os.environ, {
            seal.ENABLE_ENV: "1", seal.AFTER_ENV: "2029-01-01T00:00:00Z",
            seal.RECIPIENT_ENV: str(self.age.to_public()),
            seal.AGE_KEY_ID_ENV: "age-live",
            seal.IDENTITIES_ENV: json.dumps([str(self.age)]),
            signing.LIVE_KEY_ENV: self.private,
            signing.LIVE_KEY_ID_ENV: "live",
        }, clear=False)
        self.keys = mock.patch.object(
            signing, "KEYS_FILE", os.path.join(self.repo, "site", "keys.json"))
        self.env.start(); self.keys.start()
        self.addCleanup(self.env.stop); self.addCleanup(self.keys.stop)

    def receipt(self, received=None, entrant="model-one", mean=42):
        return seal.seal({"round_id": "round-one", "entrant": entrant,
                          "topline": {"mean": mean, "sd": 2}},
                         received_at=received or datetime(
                             2030, 1, 1, tzinfo=timezone.utc))

    def write_local(self, body=None):
        path = os.path.join(self.repo, "sealed", "round-one", "model-one.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(body or self.receipt(), fh)
        return path

    def branch_json(self, path):
        git(self.repo, "fetch", "origin", "sealed:refs/remotes/origin/sealed")
        return json.loads(git(self.repo, "show", "refs/remotes/origin/sealed:" + path))

    def test_push_and_pull_platform_receipt_preserve_api_envelope(self):
        expected = self.receipt()
        local = self.write_local(expected)
        self.assertEqual(1, sync.push(self.repo))
        self.assertEqual(expected, self.branch_json(
            "sealed/round-one/model-one.json"))
        self.assertEqual(self.api, self.branch_json(
            "sealed/round-one/api-client.json"))
        os.unlink(local)
        self.assertEqual(1, sync.pull(self.repo))
        with open(local) as fh:
            self.assertEqual(expected, json.load(fh))

    def test_refuses_tracked_main_receipt_and_late_receipt(self):
        local = self.write_local()
        git(self.repo, "add", "sealed/round-one/model-one.json")
        with self.assertRaisesRegex(sync.SyncError, "must not be tracked"):
            sync.push(self.repo)
        git(self.repo, "reset", "-q", "HEAD", "--", "sealed")
        self.write_local(self.receipt(datetime(2030, 1, 2, tzinfo=timezone.utc)))
        with self.assertRaisesRegex(sync.SyncError, "late platform"):
            sync.push(self.repo)

    def test_existing_platform_receipt_is_immutable(self):
        first = self.receipt()
        local = self.write_local(first)
        sync.push(self.repo)
        changed = dict(first)
        changed["ciphertext"] = changed["ciphertext"][:-4] + "AAAA"
        with open(local, "w") as fh:
            json.dump(changed, fh)
        with self.assertRaises(sync.SyncError):
            sync.push(self.repo)

    def test_crowd_can_advance_before_lock_but_changes_cannot_land_after(self):
        path = os.path.join(self.repo, "sealed", "round-one", "crowd.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(self.receipt(entrant="crowd", mean=40), fh)
        before = datetime(2030, 1, 1, 12, tzinfo=timezone.utc)
        self.assertEqual(1, sync.push(self.repo, now=before))
        with open(path, "w") as fh:
            json.dump(self.receipt(datetime(2030, 1, 1, 13, tzinfo=timezone.utc),
                                   entrant="crowd", mean=41), fh)
        self.assertEqual(1, sync.push(self.repo, now=before))
        with open(path, "w") as fh:
            json.dump(self.receipt(datetime(2030, 1, 1, 14, tzinfo=timezone.utc),
                                   entrant="crowd", mean=42), fh)
        after = datetime(2030, 1, 2, tzinfo=timezone.utc)
        with self.assertRaisesRegex(sync.SyncError, "after deadline"):
            sync.push(self.repo, now=after)

    def test_non_fast_forward_push_is_rebuilt_and_retried(self):
        expected = self.receipt()
        self.write_local(expected)
        real_git = sync._git
        pushes = [0]

        def one_rejection(root, *args, **kwargs):
            if args and args[0] == "push":
                pushes[0] += 1
                if pushes[0] == 1:
                    return subprocess.CompletedProcess(
                        ["git", *args], 1, "", "non-fast-forward")
            return real_git(root, *args, **kwargs)

        with mock.patch.object(sync, "_git", side_effect=one_rejection):
            self.assertEqual(1, sync.push(self.repo))
        self.assertEqual(2, pushes[0])
        self.assertEqual(expected, self.branch_json(
            "sealed/round-one/model-one.json"))


if __name__ == "__main__":
    unittest.main()
