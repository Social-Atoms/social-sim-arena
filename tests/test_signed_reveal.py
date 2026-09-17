"""Offline reveal integration against real Git history and age ciphertext."""

import base64
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pyrage
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ssa import forecast_reveal as reveal
from ssa import signed_forecasts as wire


UTC = timezone.utc


def commit(root, message, when):
    env = dict(os.environ, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "commit", "-m", message], cwd=root, env=env,
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root,
                                   text=True).strip()


class Fixture:
    def __init__(self, root):
        self.root = Path(root)
        subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(["git", "config", "user.name", "Reveal Test"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "reveal@example.test"], cwd=root, check=True)
        self.private = Ed25519PrivateKey.generate()
        public = self.private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.age = pyrage.x25519.Identity.generate()
        self.reg = {"entrant_id": "team-one", "name": "Team One", "type": "participant",
                    "keys": [{"id": "key-1", "alg": "ed25519",
                              "public": base64.b64encode(public).decode()}]}
        self.round = {"round_id": "round-one", "lock_at": "2030-01-01T01:00:00Z"}
        self.write("entrants/team-one.json", self.reg)
        self.write("questions/season0.json", {"rounds": [self.round]})
        source_schema = Path(__file__).resolve().parents[1] / "schema" / "forecast.schema.json"
        target = self.root / "schema" / "forecast.schema.json"
        target.parent.mkdir(parents=True)
        target.write_bytes(source_schema.read_bytes())
        self.main = commit(root, "approved registry and round", "2029-12-31T23:00:00Z")
        subprocess.run(["git", "switch", "--orphan", "sealed"], cwd=root, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.records = []

    def write(self, path, value):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(wire.canonical(value) + b"\n")

    def filing(self, value, request_id, received):
        raw = wire.canonical({"round_id": "round-one", "entrant": "team-one",
                              "topline": {"mean": value, "sd": 2}})
        meta = {"entrant": "team-one", "key-id": "key-1", "request-id": request_id,
                "timestamp": wire.stamp(received)}
        meta["signature"] = base64.b64encode(
            self.private.sign(wire.signing_bytes(meta, raw, "test"))).decode()
        wire.verify(meta, raw, self.reg, "test", received)
        envelope = wire.seal(raw, meta, self.main, self.main, "test",
                             str(self.age.to_public()), "age-1", received)
        self.write("sealed/round-one/team-one.json", envelope)
        sha = commit(self.root, "sealed " + request_id, wire.stamp(received))
        self.records.append((raw, meta, envelope, sha))
        return self.records[-1]

    def proof(self, index, snapshot=None):
        raw, meta, envelope, sha = self.records[index]
        return json.loads(raw), {"snapshot": snapshot or sha, "source_commit": sha,
            "source_path": "sealed/round-one/team-one.json",
            "ciphertext_sha256": envelope["ciphertext_sha256"], "headers": meta,
            "body_b64": base64.b64encode(raw).decode()}


def build_fixture(directory):
    fx = Fixture(directory)
    fx.filing(41, "request-a", datetime(2030, 1, 1, 0, 10, tzinfo=UTC))
    fx.filing(42, "request-b", datetime(2030, 1, 1, 0, 20, tzinfo=UTC))
    fx.filing(99, "request-c-late", datetime(2030, 1, 1, 1, 10, tzinfo=UTC))
    return fx


def test_reveal_uses_latest_valid_filing_and_is_idempotent():
    with tempfile.TemporaryDirectory() as directory:
        fx = build_fixture(directory)
        count = reveal.reveal(directory, fx.records[-1][3], [str(fx.age)],
                              now=datetime(2030, 1, 1, 2, tzinfo=UTC), base="main",
                              sealed_tip="sealed")
        assert count == 1
        status = reveal.public_status(directory, [fx.round], ref='sealed')
        assert status == {'round-one': {'team-one': {
            'filed': '2030-01-01T00:20:00Z', 'sealed': True}}}
        published = json.loads((Path(directory) / "forecasts/round-one/team-one.json").read_text())
        assert published["topline"]["mean"] == 42
        assert reveal.reveal(directory, fx.records[-1][3], [str(fx.age)],
                             now=datetime(2030, 1, 1, 2, tzinfo=UTC), base="main",
                             sealed_tip="sealed") == 0
        # Decryption preserves the exact signed bytes, not a reserialized approximation.
        raw, meta = wire.open_envelope(fx.records[1][2], [str(fx.age)])
        assert raw == fx.records[1][0]
        wire.verify(meta, raw, fx.reg, "test", datetime(2030, 1, 1, 0, 20, tzinfo=UTC))


def test_reveal_proof_rejects_early_tampered_and_stale_snapshots():
    with tempfile.TemporaryDirectory() as directory:
        fx = build_fixture(directory)
        body, proof = fx.proof(1, snapshot=fx.records[-1][3])
        try:
            reveal.verify_reveal(directory, body, proof, "main", fx.records[-1][3],
                                 datetime(2030, 1, 1, 0, 30, tzinfo=UTC))
        except wire.IntakeError as error:
            assert error.code == "reveal_before_deadline"
        else:
            raise AssertionError("forecast revealed before its deadline")

        tampered = dict(proof, body_b64=base64.b64encode(b"{}").decode())
        try:
            reveal.verify_reveal(directory, body, tampered, "main", fx.records[-1][3],
                                 datetime(2030, 1, 1, 2, tzinfo=UTC))
        except wire.IntakeError:
            pass
        else:
            raise AssertionError("tampered plaintext proof passed")

        old_body, old_proof = fx.proof(0)
        try:
            reveal.verify_reveal(directory, old_body, old_proof, "main", fx.records[-1][3],
                                 datetime(2030, 1, 1, 2, tzinfo=UTC))
        except wire.IntakeError as error:
            assert error.code == "stale_sealed_snapshot"
        else:
            raise AssertionError("an early sealed snapshot hid a later valid filing")


def test_reveal_cannot_publish_from_an_early_source_snapshot():
    with tempfile.TemporaryDirectory() as directory:
        fx = build_fixture(directory)
        try:
            reveal.reveal(directory, fx.records[0][3], [str(fx.age)],
                          now=datetime(2030, 1, 1, 2, tzinfo=UTC), base="main",
                          sealed_tip="sealed")
        except wire.IntakeError as error:
            assert error.code == "stale_sealed_snapshot"
        else:
            raise AssertionError("an early source snapshot was accepted as the final branch tip")
        assert not (Path(directory) / "forecasts").exists()
        assert not (Path(directory) / "reveal-receipts").exists()


if __name__ == "__main__":
    test_reveal_uses_latest_valid_filing_and_is_idempotent()
    test_reveal_proof_rejects_early_tampered_and_stale_snapshots()
    test_reveal_cannot_publish_from_an_early_source_snapshot()
    print("all signed reveal tests passed")
