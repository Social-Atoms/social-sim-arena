import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest import mock

from cryptography.fernet import Fernet

from ssa import seal, signing
from ssa import refresh, replies
from ssa.adapters import search as search_adapter
from tools import audit_landing


@contextmanager
def configured():
    private, public = signing.generate()
    with tempfile.TemporaryDirectory() as td:
        keys = os.path.join(td, "keys.json")
        with open(keys, "w") as fh:
            json.dump({"keys": [{"key_id": "seal-live", "public_key": public}]}, fh)
        env = {
            seal.ENABLE_ENV: "1",
            seal.AFTER_ENV: "2026-10-01T00:00:00Z",
            seal.KEY_ENV: Fernet.generate_key().decode(),
            signing.LIVE_KEY_ENV: private,
            signing.LIVE_KEY_ID_ENV: "seal-live",
        }
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(signing, "KEYS_FILE", keys):
            yield td, public


class SealTests(unittest.TestCase):
    def forecast(self):
        return {"round_id": "round-1", "entrant": "model-1",
                "topline": {"mean": 4.2, "sd": 1.1},
                "notes": "filed=2026-10-02T00:00Z"}

    def test_round_trip_and_no_plaintext_leak(self):
        with configured() as (td, public):
            receipt = seal.seal(self.forecast(), received_at=datetime(
                2026, 10, 2, tzinfo=timezone.utc))
            raw = json.dumps(receipt)
            self.assertNotIn('"mean": 4.2', raw)
            self.assertNotIn("commitment_salt", raw)
            got, salt = seal.open_receipt(receipt, {"seal-live": public})
            self.assertEqual(self.forecast(), got)
            seal.verify_disclosure(receipt, got, {"commitment_salt": salt})

    def test_tampered_forecast_and_receipt_fail(self):
        with configured() as (_td, public):
            receipt = seal.seal(self.forecast())
            got, salt = seal.open_receipt(receipt, {"seal-live": public})
            got["topline"]["mean"] = 99
            with self.assertRaises(seal.SealError):
                seal.verify_disclosure(receipt, got, {"commitment_salt": salt})
            receipt["received_at"] = "2020-01-01T00:00:00Z"
            with self.assertRaises(seal.SealError):
                seal.verify_receipt(receipt, {"seal-live": public})

    def test_missing_key_fails_closed(self):
        with mock.patch.dict(os.environ, {
                seal.ENABLE_ENV: "1",
                seal.AFTER_ENV: "2026-10-01T00:00:00Z",
                seal.KEY_ENV: "",
        }, clear=False):
            with self.assertRaises(seal.SealError):
                seal.require_ready()

    def test_public_test_key_cannot_sign_receipts_under_alias(self):
        with tempfile.TemporaryDirectory() as td:
            keys = os.path.join(td, "keys.json")
            with open(keys, "w") as fh:
                json.dump({"keys": [{"key_id": "looks-live",
                                      "public_key": signing.TEST_PUBLIC_KEY}]}, fh)
            env = {seal.ENABLE_ENV: "1", seal.AFTER_ENV: "2026-10-01T00:00:00Z",
                   seal.KEY_ENV: Fernet.generate_key().decode(),
                   signing.LIVE_KEY_ENV: signing.TEST_PRIVATE_KEY,
                   signing.LIVE_KEY_ID_ENV: "looks-live"}
            with mock.patch.dict(os.environ, env, clear=False), \
                    mock.patch.object(signing, "KEYS_FILE", keys):
                with self.assertRaises(seal.SealError):
                    seal.require_ready()

    def test_private_evidence_is_encrypted_and_replayable(self):
        with configured():
            source = {"reply": '{"mean": 4.2}', "logged_at": "now"}
            stored = seal.encrypt_record(source, "provider-reply")
            self.assertNotIn("mean", json.dumps(stored))
            self.assertEqual(source, seal.decrypt_record(stored, "provider-reply"))

    def test_deadline_boundary_is_fail_closed_and_reveal_is_idempotent(self):
        with configured() as (td, public):
            forecasts = os.path.join(td, "forecasts")
            sealed = os.path.join(td, "sealed")
            disclosures = os.path.join(td, "reveals")
            round_def = {"round_id": "round-1", "lock_at": "2026-10-03T00:00:00Z"}
            before = datetime(2026, 10, 2, 23, 59, 59, tzinfo=timezone.utc)
            deadline = datetime(2026, 10, 3, tzinfo=timezone.utc)
            with mock.patch.object(refresh, "FORECASTS", forecasts), \
                    mock.patch.object(seal, "SEALED", sealed):
                receipt_path = refresh.file_arena_forecast(
                    round_def, self.forecast(), before, deadline=deadline)
                self.assertTrue(os.path.exists(receipt_path))
                other = dict(self.forecast(), entrant="model-2")
                with self.assertRaises(seal.SealError):
                    refresh.file_arena_forecast(
                        round_def, other, deadline, deadline=deadline)
                target = os.path.join(forecasts, "round-1", "model-1.json")
                seal.reveal_to_files(receipt_path, target,
                                     public_keys={"seal-live": public},
                                     disclosure_root=disclosures)
                seal.reveal_to_files(receipt_path, target,
                                     public_keys={"seal-live": public},
                                     disclosure_root=disclosures)
                with open(target) as fh:
                    self.assertEqual(self.forecast(), json.load(fh))

    def test_reply_and_search_evidence_stays_opaque_until_reveal(self):
        with configured() as (td, _public):
            reply_root = os.path.join(td, "replies")
            search_root = os.path.join(td, "search-rounds")
            with mock.patch.dict(os.environ, {"SSA_REPLIES_DIR": reply_root}), \
                    mock.patch.object(search_adapter, "ROUNDS", search_root):
                replies.log("round-1", "model-1", "abc123", {
                    "reply": '{"mean": 4.2}', "usage": {}})
                search_adapter.record_round("round-1", "model-1", ["private query"],
                                            [{"query": "private query", "results": []}])
                rpath = replies.path("round-1", "model-1", "abc123")
                spath = search_adapter.round_path("round-1", "model-1")
                with open(rpath) as fh:
                    self.assertNotIn("mean", fh.read())
                with open(spath) as fh:
                    self.assertNotIn("private query", fh.read())
                self.assertEqual('{"mean": 4.2}', replies.lookup(
                    "round-1", "model-1", "abc123")["reply"])
                replies.reveal_round("round-1")
                search_adapter.reveal_round("round-1")
                with open(rpath) as fh:
                    self.assertIn("mean", fh.read())
                with open(spath) as fh:
                    self.assertIn("private query", fh.read())

    def test_landing_audit_accepts_only_a_parent_bound_reveal(self):
        with configured() as (td, _public):
            repo = os.path.join(td, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"],
                           cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "test"], cwd=repo,
                           check=True)
            for name in ("site", "questions", "sealed/round-1"):
                os.makedirs(os.path.join(repo, name), exist_ok=True)
            private = os.environ[signing.LIVE_KEY_ENV]
            public = signing.public_key_of(private)
            with open(os.path.join(repo, "site/keys.json"), "w") as fh:
                json.dump({"keys": [{"key_id": "seal-live",
                                      "public_key": public}]}, fh)
            with open(os.path.join(repo, "questions/season0.json"), "w") as fh:
                json.dump({"rounds": [{"round_id": "round-1",
                                        "lock_at": "2026-08-02T00:00:00Z"}]}, fh)
            receipt = seal.seal(self.forecast(), received_at=datetime(
                2026, 8, 1, 23, 59, 59, tzinfo=timezone.utc))
            with open(os.path.join(repo, "sealed/round-1/model-1.json"), "w") as fh:
                json.dump(receipt, fh)
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "seal"], cwd=repo, check=True)
            before = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                             cwd=repo, text=True).strip()
            forecast, salt = seal.open_receipt(receipt, {"seal-live": public})
            os.makedirs(os.path.join(repo, "forecasts/round-1"))
            os.makedirs(os.path.join(repo, "reveal-receipts/round-1"))
            with open(os.path.join(repo, "forecasts/round-1/model-1.json"), "w") as fh:
                json.dump(forecast, fh)
            with open(os.path.join(repo, "reveal-receipts/round-1/model-1.json"), "w") as fh:
                json.dump({"commitment_salt": salt}, fh)
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "reveal"], cwd=repo, check=True)
            after = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                            cwd=repo, text=True).strip()
            self.assertEqual("2026-08-01T23:59:59Z",
                             audit_landing.reveal_received_at(
                                 "forecasts/round-1/model-1.json",
                                 before, after, root=repo))
            # A receipt used for authority cannot be changed in the reveal
            # commit itself, even by a whitespace-only rewrite.
            subprocess.run(["git", "checkout", "-qb", "changed-receipt", before],
                           cwd=repo, check=True)
            os.makedirs(os.path.join(repo, "forecasts/round-1"))
            os.makedirs(os.path.join(repo, "reveal-receipts/round-1"))
            with open(os.path.join(repo, "forecasts/round-1/model-1.json"), "w") as fh:
                json.dump(forecast, fh)
            with open(os.path.join(
                    repo, "reveal-receipts/round-1/model-1.json"), "w") as fh:
                json.dump({"commitment_salt": salt}, fh)
            with open(os.path.join(repo, "sealed/round-1/model-1.json"), "a") as fh:
                fh.write("\n")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "reveal and touch receipt"], cwd=repo,
                           check=True)
            changed = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                              cwd=repo, text=True).strip()
            with self.assertRaises(seal.SealError):
                audit_landing.reveal_received_at(
                    "forecasts/round-1/model-1.json", before, changed, root=repo)

    def test_a_parent_seal_cannot_fall_back_to_an_unsigned_trailer(self):
        with configured() as (td, _public):
            repo = os.path.join(td, "repo")
            os.makedirs(repo)
            for args in (["git", "init", "-q"],
                         ["git", "config", "user.email", "test@example.com"],
                         ["git", "config", "user.name", "test"]):
                subprocess.run(args, cwd=repo, check=True)
            for name in ("site", "questions", "sealed/round-1"):
                os.makedirs(os.path.join(repo, name), exist_ok=True)
            public = signing.public_key_of(os.environ[signing.LIVE_KEY_ENV])
            with open(os.path.join(repo, "site/keys.json"), "w") as fh:
                json.dump({"keys": [{"key_id": "seal-live",
                                      "public_key": public}]}, fh)
            with open(os.path.join(repo, "questions/season0.json"), "w") as fh:
                json.dump({"rounds": [{"round_id": "round-1",
                                        "lock_at": "2026-08-02T00:00:00Z"}]}, fh)
            receipt = seal.seal(self.forecast(), received_at=datetime(
                2026, 8, 1, 23, 59, 59, tzinfo=timezone.utc))
            with open(os.path.join(repo, "sealed/round-1/model-1.json"), "w") as fh:
                json.dump(receipt, fh)
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "seal"], cwd=repo, check=True)
            before = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                             cwd=repo, text=True).strip()
            os.makedirs(os.path.join(repo, "forecasts/round-1"))
            with open(os.path.join(repo, "forecasts/round-1/model-1.json"), "w") as fh:
                json.dump(self.forecast(), fh)
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm",
                            "fake reveal\n\nReceived-At: 2026-08-01T23:59:59Z"],
                           cwd=repo, check=True)
            after = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                            cwd=repo, text=True).strip()
            with self.assertRaises(seal.SealError):
                audit_landing.reveal_received_at(
                    "forecasts/round-1/model-1.json", before, after, root=repo)

    def test_crowd_pools_sealed_members_and_refresh_reveals_only_after_close(self):
        with configured() as (td, _public):
            forecasts = os.path.join(td, "forecasts")
            sealed = os.path.join(td, "sealed")
            disclosures = os.path.join(td, "reveals")
            reply_root = os.path.join(td, "replies")
            search_rounds = os.path.join(td, "search-rounds")
            os.makedirs(os.path.join(forecasts, "round-1"))
            round_def = {"round_id": "round-1", "status": "open",
                         "lock_at": "2026-10-09T14:00:00Z"}
            deadline = refresh.batches.effective_deadline(round_def["lock_at"])
            before = deadline - refresh.timedelta(hours=1)
            with mock.patch.object(refresh, "FORECASTS", forecasts), \
                    mock.patch.object(seal, "SEALED", sealed), \
                    mock.patch.object(seal, "DISCLOSURES", disclosures), \
                    mock.patch.dict(os.environ, {"SSA_REPLIES_DIR": reply_root}), \
                    mock.patch.object(search_adapter, "ROUNDS", search_rounds):
                for entrant, mean in (("model-1", 4.0), ("model-2", 6.0)):
                    body = dict(self.forecast(), entrant=entrant,
                                topline={"mean": mean, "sd": 1.0})
                    refresh.file_arena_forecast(
                        round_def, body, before, deadline=deadline)
                with mock.patch.object(refresh, "datetime") as clock:
                    clock.now.return_value = before
                    self.assertEqual(1, refresh.file_crowd_forecasts(
                        [round_def], before))
                self.assertFalse(os.path.exists(os.path.join(
                    forecasts, "round-1", "crowd.json")))
                self.assertTrue(os.path.exists(os.path.join(
                    sealed, "round-1", "crowd.json")))
                self.assertEqual(0, refresh.reveal_locked_forecasts(
                    [round_def], before))
                self.assertEqual(3, refresh.reveal_locked_forecasts(
                    [round_def], deadline))
                self.assertTrue(os.path.exists(os.path.join(
                    forecasts, "round-1", "crowd.json")))

    def test_real_landing_audit_accepts_reveal_and_rejects_modified_answer(self):
        """Exercise the workflow CLI, including the real schema validator."""
        with configured() as (td, _public):
            repo = os.path.join(td, "repo")
            os.makedirs(repo)
            source_root = os.path.dirname(os.path.dirname(__file__))
            for directory in ("tools", "ssa", "schema", "site", "questions"):
                os.makedirs(os.path.join(repo, directory), exist_ok=True)
            for name in ("audit_landing.py", "validate_submission.py"):
                shutil.copy2(os.path.join(source_root, "tools", name),
                             os.path.join(repo, "tools", name))
            for name in ("__init__.py", "batches.py", "seal.py", "signing.py"):
                shutil.copy2(os.path.join(source_root, "ssa", name),
                             os.path.join(repo, "ssa", name))
            shutil.copy2(os.path.join(source_root, "schema/forecast.schema.json"),
                         os.path.join(repo, "schema/forecast.schema.json"))
            public = signing.public_key_of(os.environ[signing.LIVE_KEY_ENV])
            with open(os.path.join(repo, "site/keys.json"), "w") as fh:
                json.dump({"keys": [{"key_id": "seal-live",
                                      "public_key": public}]}, fh)
            with open(os.path.join(repo, "questions/season0.json"), "w") as fh:
                json.dump({"rounds": [{"round_id": "round-1",
                                        "lock_at": "2026-08-02T00:00:00Z"}]}, fh)
            for args in (["git", "init", "-q"],
                         ["git", "config", "user.email", "test@example.com"],
                         ["git", "config", "user.name", "test"]):
                subprocess.run(args, cwd=repo, check=True)
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
            base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo,
                                           text=True).strip()

            receipt = seal.seal(self.forecast(), received_at=datetime(
                2026, 8, 1, 23, 59, 59, tzinfo=timezone.utc))
            os.makedirs(os.path.join(repo, "sealed/round-1"))
            with open(os.path.join(repo, "sealed/round-1/model-1.json"), "w") as fh:
                json.dump(receipt, fh)
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "seal"], cwd=repo, check=True)
            sealed_commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            audit = [sys.executable, "tools/audit_landing.py"]
            self.assertEqual(0, subprocess.run(
                audit + [base, sealed_commit], cwd=repo).returncode)

            forecast, salt = seal.open_receipt(receipt, {"seal-live": public})
            os.makedirs(os.path.join(repo, "forecasts/round-1"))
            os.makedirs(os.path.join(repo, "reveal-receipts/round-1"))
            with open(os.path.join(repo, "forecasts/round-1/model-1.json"), "w") as fh:
                json.dump(forecast, fh)
            with open(os.path.join(
                    repo, "reveal-receipts/round-1/model-1.json"), "w") as fh:
                json.dump({"commitment_salt": salt}, fh)
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "reveal"], cwd=repo, check=True)
            reveal_commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            self.assertEqual(0, subprocess.run(
                audit + [sealed_commit, reveal_commit], cwd=repo).returncode)

            forecast["topline"]["mean"] = 99
            with open(os.path.join(repo, "forecasts/round-1/model-1.json"), "w") as fh:
                json.dump(forecast, fh)
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "tamper"], cwd=repo, check=True)
            tampered = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            self.assertNotEqual(0, subprocess.run(
                audit + [reveal_commit, tampered], cwd=repo).returncode)

    def test_landing_audit_rejects_an_early_reveal(self):
        class BeforeDeadline(datetime):
            @classmethod
            def now(cls, tz=None):
                value = cls(2027, 1, 1, 12, tzinfo=timezone.utc)
                return value if tz is None else value.astimezone(tz)

        with configured() as (td, _public):
            repo = os.path.join(td, "repo")
            os.makedirs(repo)
            for args in (["git", "init", "-q"],
                         ["git", "config", "user.email", "test@example.com"],
                         ["git", "config", "user.name", "test"]):
                subprocess.run(args, cwd=repo, check=True)
            for name in ("site", "questions", "sealed/round-1"):
                os.makedirs(os.path.join(repo, name), exist_ok=True)
            public = signing.public_key_of(os.environ[signing.LIVE_KEY_ENV])
            with open(os.path.join(repo, "site/keys.json"), "w") as fh:
                json.dump({"keys": [{"key_id": "seal-live",
                                      "public_key": public}]}, fh)
            with open(os.path.join(repo, "questions/season0.json"), "w") as fh:
                json.dump({"rounds": [{"round_id": "round-1",
                                        "lock_at": "2027-01-02T14:00:00Z"}]}, fh)
            receipt = seal.seal(self.forecast(), received_at=datetime(
                2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc))
            with open(os.path.join(repo, "sealed/round-1/model-1.json"), "w") as fh:
                json.dump(receipt, fh)
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "seal"], cwd=repo, check=True)
            before = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                             cwd=repo, text=True).strip()
            forecast, salt = seal.open_receipt(receipt, {"seal-live": public})
            os.makedirs(os.path.join(repo, "forecasts/round-1"))
            os.makedirs(os.path.join(repo, "reveal-receipts/round-1"))
            with open(os.path.join(repo, "forecasts/round-1/model-1.json"), "w") as fh:
                json.dump(forecast, fh)
            with open(os.path.join(
                    repo, "reveal-receipts/round-1/model-1.json"), "w") as fh:
                json.dump({"commitment_salt": salt}, fh)
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "early reveal"], cwd=repo,
                           check=True)
            after = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                            cwd=repo, text=True).strip()
            with mock.patch.object(audit_landing.datetime, "datetime", BeforeDeadline):
                with self.assertRaisesRegex(seal.SealError, "before its deadline"):
                    audit_landing.reveal_received_at(
                        "forecasts/round-1/model-1.json", before, after, root=repo)


if __name__ == "__main__":
    unittest.main()
