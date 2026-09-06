"""The participant registry (kept, not wired for Season 0; see the note atop
ssa/registry.py): register once with an invitation code, get one token, and the cron
side writes the public file and files the uploads. Runs against a temporary
directory; the GitHub-backed store has the same contract and is not exercised
here."""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import bundle_api, participants, registry, registry_api  # noqa: E402

FIELDS = {"entrant_id": "acme-forecast", "name": "Acme Forecast",
          "type": "firm", "method": "A model with a newsfeed.",
          "contact": "ops@acme.example",
          "url": "https://api.acme.example/forecast"}


class Sandbox:
    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="ssa-registry-")
        self.entrants = os.path.join(self.dir, "entrants")
        os.makedirs(self.entrants)
        self.saved = {k: os.environ.get(k) for k in
                      ("SUBMISSION_STORAGE_DIR", "SSA_PROMO_CODES",
                       "SSA_UPLOAD_KEY_ACME_FORECAST", "SSA_SIGNING_KEY")}
        os.environ["SUBMISSION_STORAGE_DIR"] = self.dir
        os.environ.pop("SSA_PROMO_CODES", None)
        os.environ.pop("SSA_UPLOAD_KEY_ACME_FORECAST", None)
        os.environ["SSA_SIGNING_KEY"] = "HLHPLfr2J+BaNVHYXBHNs5CJOSbmgouzCUp2cxcwdy4="
        self.store = registry.Store()
        self.store.put("invitations.json", {"codes": ["SEASON0"]}, None)
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.dir, ignore_errors=True)


def test_registration_needs_a_invitation_code_and_returns_one_token():
    with Sandbox() as sb:
        try:
            registry.register(sb.store, FIELDS, "WRONG", sb.entrants)
            assert False, "a wrong invitation code registered"
        except registry.RegistryError as err:
            assert err.status == 403 and err.code == "bad_invitation"
        record, token = registry.register(sb.store, FIELDS, "season0", sb.entrants)
        assert token.startswith("ssa_") and len(token) > 30
        assert record["entrant_id"] == "acme-forecast"
        assert record["url"] == FIELDS["url"]
        secret, _ = sb.store.get("secrets/acme-forecast.json")
        assert token not in json.dumps(secret), "the token is stored in the clear"
        assert "endpoint_key" not in secret, "the registry holds no participant key"
        assert registry.authenticate(sb.store, "acme-forecast", token)
        assert not registry.authenticate(sb.store, "acme-forecast", token[:-1])
        try:
            registry.register(sb.store, FIELDS, "SEASON0", sb.entrants)
            assert False, "the same id registered twice"
        except registry.RegistryError as err:
            assert err.status == 409
        print("ok test_registration_needs_a_invitation_code_and_returns_one_token")


def test_a_hand_written_entrant_id_cannot_be_claimed():
    with Sandbox() as sb:
        with open(os.path.join(sb.entrants, "acme-forecast.json"), "w") as fh:
            json.dump({"entrant_id": "acme-forecast", "name": "x",
                       "type": "firm", "method": "x"}, fh)
        try:
            registry.register(sb.store, FIELDS, "SEASON0", sb.entrants)
            assert False
        except registry.RegistryError as err:
            assert err.code == "taken"
        print("ok test_a_hand_written_entrant_id_cannot_be_claimed")


def test_the_token_edits_the_registration_and_only_that_one():
    with Sandbox() as sb:
        _, token = registry.register(sb.store, FIELDS, "SEASON0", sb.entrants)
        other = dict(FIELDS, entrant_id="beta-labs", name="Beta")
        _, other_token = registry.register(sb.store, other, "SEASON0", sb.entrants)
        record = registry.update(sb.store, "acme-forecast", token,
                                 {"url": "https://api.acme.example/v2"})
        assert record["url"] == "https://api.acme.example/v2"
        try:
            registry.update(sb.store, "acme-forecast", other_token, {"name": "X"})
            assert False, "another entrant's token edited this one"
        except registry.RegistryError as err:
            assert err.status == 401
        try:
            registry.update(sb.store, "acme-forecast", token,
                            {"entrant_id": "renamed", "name": "Still Acme"})
        except registry.RegistryError:
            assert False, "an ignored entrant_id field must not refuse the edit"
        record, _ = registry.load(sb.store, "acme-forecast")
        assert record["entrant_id"] == "acme-forecast"
        assert record["name"] == "Still Acme"
        new = registry.rotate_token(sb.store, "acme-forecast", token)
        assert not registry.authenticate(sb.store, "acme-forecast", token)
        assert registry.authenticate(sb.store, "acme-forecast", new)
        print("ok test_the_token_edits_the_registration_and_only_that_one")


def test_the_cron_side_writes_the_public_file():
    with Sandbox() as sb:
        registry.register(sb.store, FIELDS, "SEASON0", sb.entrants)
        wrote = registry.materialize(sb.store, sb.entrants)
        assert wrote == ["acme-forecast"]
        with open(os.path.join(sb.entrants, "acme-forecast.json")) as fh:
            doc = json.load(fh)
        assert doc["route"] == {"kind": "agent_api", "url": FIELDS["url"]}
        assert registry.materialize(sb.store, sb.entrants) == [], "rewrote an unchanged file"
        # the schema the pull-request path enforces accepts what the cron wrote
        import jsonschema
        with open(os.path.join(ROOT, "schema", "entrant.schema.json")) as fh:
            jsonschema.validate(doc, json.load(fh))
        ok, why = participants.callable_now("acme-forecast", sb.entrants)
        assert ok, why
        rt = participants.route("acme-forecast", sb.entrants)
        assert rt["api"] == "agent" and rt["base"] == FIELDS["url"] and rt["env"] == ""
        print("ok test_the_cron_side_writes_the_public_file")


def test_an_upload_is_authenticated_by_the_registration_token():
    with Sandbox() as sb:
        _, token = registry.register(sb.store, FIELDS, "SEASON0", sb.entrants)
        # before the cron has written entrants/<id>.json, the registry is the record
        entrant = bundle_api._authenticate("acme-forecast", token, sb.entrants)
        assert entrant and entrant["entrant_id"] == "acme-forecast"
        assert bundle_api._authenticate("acme-forecast", "ssa_wrong", sb.entrants) is None
        assert bundle_api._authenticate("nobody", token, sb.entrants) is None
        # a token installed by hand still wins for its entrant
        registry.materialize(sb.store, sb.entrants)
        os.environ["SSA_UPLOAD_KEY_ACME_FORECAST"] = "hand-installed"
        assert bundle_api._authenticate("acme-forecast", "hand-installed", sb.entrants)
        assert bundle_api._authenticate("acme-forecast", token, sb.entrants) is None
        print("ok test_an_upload_is_authenticated_by_the_registration_token")


def test_stored_uploads_are_filed_against_their_receipt_time():
    from ssa import bundle as bundle_lib
    with Sandbox() as sb:
        registry.register(sb.store, FIELDS, "SEASON0", sb.entrants)
        registry.materialize(sb.store, sb.entrants)
        bundles = os.path.join(sb.dir, "bundles-src")
        os.makedirs(bundles)
        deadline = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)
        rounds = [{"round_id": "yougov-2026-w38-approval", "series": "yougov_approval",
                   "tracker": "economist_yougov", "unit": "% approve",
                   "question": "Approval.", "resolve": "topline",
                   "target_type": "continuous_normal",
                   "lock_at": "2026-09-14T14:00:00Z", "release_at": "2026-09-16T14:00:00Z"}]
        questions = bundle_lib.build_bundle(rounds, "batch-2026-09-14", now=deadline - timedelta(days=7))
        with open(os.path.join(bundles, "batch-2026-09-14.json"), "w") as fh:
            json.dump(questions, fh)
        response = {"schema_version": "1.0.0", "batch_id": "batch-2026-09-14",
                    "entrant_id": "acme-forecast",
                    "answers": [{"round_id": "yougov-2026-w38-approval",
                                 "topline": {"mean": 41.0, "sd": 2.0}}]}
        for received, name in ((deadline - timedelta(hours=2), "on-time"),
                               (deadline + timedelta(hours=2), "late")):
            receipt = {"received_at": received.strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "entrant_id": "acme-forecast", "response_sha256": name}
            sb.store.put(f"bundles/batch-2026-09-14/acme-forecast/{name}.json",
                         {"response": response, "receipt": receipt}, None)
        out = os.path.join(sb.dir, "forecasts")
        rows = registry.file_uploads(sb.store, out_dir=out, bundles_dir=bundles)
        by = {os.path.basename(r["packet"]): r for r in rows}
        assert by["on-time.json"]["status"] == "filed" and by["on-time.json"]["accepted"] == 1, by
        assert by["late.json"]["accepted"] == 0, "a late upload was filed"
        assert os.path.isfile(os.path.join(out, "yougov-2026-w38-approval", "acme-forecast.json"))
        again = registry.file_uploads(sb.store, out_dir=out, bundles_dir=bundles)
        assert {r["packet"]: r.get("written") for r in again}[
            "bundles/batch-2026-09-14/acme-forecast/on-time.json"] == 0, "refiled unchanged"
        print("ok test_stored_uploads_are_filed_against_their_receipt_time")


def test_the_endpoint_registers_reads_and_edits_with_the_token():
    with Sandbox() as sb:
        status, body = registry_api.handle("POST", None, None,
                                           dict(FIELDS, invitation_code="SEASON0"), sb.entrants)
        assert status == 201, body
        token = body["token"]
        assert body["registration"]["entrant_id"] == "acme-forecast"
        assert "endpoint_key" not in json.dumps(body["registration"])
        status, body = registry_api.handle("GET", "acme-forecast", token, None, sb.entrants)
        assert status == 200 and body["registration"]["entrant_id"] == "acme-forecast"
        status, body = registry_api.handle("GET", "acme-forecast", "nope", None, sb.entrants)
        assert status == 401
        status, body = registry_api.handle("PATCH", "acme-forecast", token,
                                           {"name": "Acme v2"}, sb.entrants)
        assert status == 200 and body["registration"]["name"] == "Acme v2"
        status, body = registry_api.handle("PATCH", "acme-forecast", token,
                                           {"rotate_token": True}, sb.entrants)
        assert status == 200 and body["token"] != token
        status, body = registry_api.handle("POST", None, None, "not an object", sb.entrants)
        assert status == 422
        print("ok test_the_endpoint_registers_reads_and_edits_with_the_token")


def test_without_storage_the_endpoint_says_so_instead_of_crashing():
    saved = {k: os.environ.pop(k, None) for k in ("SUBMISSION_STORAGE_DIR", "INTAKE_REPO_TOKEN")}
    try:
        status, body = registry_api.handle("POST", None, None, dict(FIELDS, invitation_code="SEASON0"))
        assert status == 503 and body["error"]["code"] == "registry_unavailable"
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
    print("ok test_without_storage_the_endpoint_says_so_instead_of_crashing")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("all registry tests passed")
