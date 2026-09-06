"""Route A authentication is the arena signing its requests (ssa/signing.py),
and Season 0 registration is a pull request whose author owns the entrant
(tools/validate_submission.py --author, tools/auto_merge.py).

Run: PYTHONPATH=. python tests/test_signing.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import signing  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "tools"))
import auto_merge  # noqa: E402
import validate_submission as vs  # noqa: E402


# --- signing -----------------------------------------------------------------

def test_a_signature_verifies_over_the_exact_bytes_and_nothing_else():
    private, public = signing.generate()
    body = b'{"request_id":"acme:r1","round":{"round_id":"r1"}}'
    headers = signing.sign(private, body, "ssa-live")
    assert set(headers) == {signing.HEADER_KEY_ID, signing.HEADER_TIMESTAMP,
                            signing.HEADER_SIGNATURE}
    assert signing.verify(public, headers, body) == "ssa-live"
    # case-insensitive header lookup, as an HTTP server hands them over
    lower = {k.lower(): v for k, v in headers.items()}
    assert signing.verify(public, lower, body) == "ssa-live"
    for bad_body in (body + b" ", body.replace(b"r1", b"r2"), b""):
        try:
            signing.verify(public, headers, bad_body)
            raise AssertionError("a different body verified")
        except signing.SignatureError:
            pass
    # re-serialised JSON is a different byte string, which is the whole point
    reserialised = json.dumps(json.loads(body)).encode()
    assert reserialised != body
    try:
        signing.verify(public, headers, reserialised)
        raise AssertionError("re-serialised JSON verified")
    except signing.SignatureError:
        pass
    print("ok test_a_signature_verifies_over_the_exact_bytes_and_nothing_else")


def test_a_signature_from_another_key_or_a_tampered_header_is_refused():
    private, public = signing.generate()
    other_private, other_public = signing.generate()
    body = b"{}"
    headers = signing.sign(private, body, "ssa-live")
    for public_key in (other_public,):
        try:
            signing.verify(public_key, headers, body)
            raise AssertionError("another key's signature verified")
        except signing.SignatureError:
            pass
    tampered = dict(headers, **{signing.HEADER_TIMESTAMP: str(int(headers[signing.HEADER_TIMESTAMP]) + 1)})
    try:
        signing.verify(public, tampered, body)
        raise AssertionError("a changed timestamp verified")
    except signing.SignatureError:
        pass
    for missing in (signing.HEADER_KEY_ID, signing.HEADER_TIMESTAMP, signing.HEADER_SIGNATURE):
        partial = {k: v for k, v in headers.items() if k != missing}
        try:
            signing.verify(public, partial, body)
            raise AssertionError(f"verified without {missing}")
        except signing.SignatureError as err:
            assert "not signed" in str(err)
    print("ok test_a_signature_from_another_key_or_a_tampered_header_is_refused")


def test_a_stale_request_is_refused_and_a_fresh_one_within_the_window_is_not():
    private, public = signing.generate()
    body = b"{}"
    now = 1_800_000_000
    headers = signing.sign(private, body, "ssa-live", timestamp=now)
    assert signing.verify(public, headers, body, now=now + signing.MAX_SKEW_SECONDS)
    assert signing.verify(public, headers, body, now=now - signing.MAX_SKEW_SECONDS)
    for skew in (signing.MAX_SKEW_SECONDS + 1, -(signing.MAX_SKEW_SECONDS + 1)):
        try:
            signing.verify(public, headers, body, now=now + skew)
            raise AssertionError("a stale request verified")
        except signing.SignatureError as err:
            assert "timestamp" in str(err)
    print("ok test_a_stale_request_is_refused_and_a_fresh_one_within_the_window_is_not")


def test_the_published_keys_file_carries_the_test_key_and_a_slot_for_the_live_one():
    with open(os.path.join(ROOT, "site", "keys.json")) as fh:
        doc = json.load(fh)
    by_id = {k["key_id"]: k for k in doc["keys"]}
    assert doc["algorithm"] == "ed25519"
    assert by_id[signing.TEST_KEY_ID]["public_key"] == signing.TEST_PUBLIC_KEY
    assert by_id[signing.TEST_KEY_ID]["private_key"] == signing.TEST_PRIVATE_KEY
    assert signing.public_key_of(signing.TEST_PRIVATE_KEY) == signing.TEST_PUBLIC_KEY
    assert signing.DEFAULT_LIVE_KEY_ID in by_id, "the live key id must be listed, even before the key exists"
    assert "private_key" not in by_id[signing.DEFAULT_LIVE_KEY_ID], "the live private key must never be published"
    keys = signing.published_keys()
    assert keys[signing.TEST_KEY_ID] == signing.TEST_PUBLIC_KEY
    if by_id[signing.DEFAULT_LIVE_KEY_ID]["public_key"] is None:
        assert signing.DEFAULT_LIVE_KEY_ID not in keys, "an empty slot is not a key"
    body = b'{"request_id":"x"}'
    assert signing.verify_against_published(
        signing.sign(signing.TEST_PRIVATE_KEY, body, signing.TEST_KEY_ID), body) == signing.TEST_KEY_ID
    try:
        signing.verify_against_published(
            signing.sign(signing.TEST_PRIVATE_KEY, body, "ssa-nope"), body)
        raise AssertionError("an unknown key id verified")
    except signing.SignatureError as err:
        assert "unknown key id" in str(err)
    print("ok test_the_published_keys_file_carries_the_test_key_and_a_slot_for_the_live_one")


def test_the_live_signer_comes_from_the_environment_only():
    saved = {k: os.environ.pop(k, None) for k in (signing.LIVE_KEY_ENV, signing.LIVE_KEY_ID_ENV)}
    try:
        assert signing.live_signer() is None
        os.environ[signing.LIVE_KEY_ENV] = signing.TEST_PRIVATE_KEY
        assert signing.live_signer() == (signing.TEST_PRIVATE_KEY, signing.DEFAULT_LIVE_KEY_ID)
        os.environ[signing.LIVE_KEY_ID_ENV] = "ssa-live-2027"
        assert signing.live_signer()[1] == "ssa-live-2027"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    print("ok test_the_live_signer_comes_from_the_environment_only")


# --- ownership by pull request author ----------------------------------------

class Repo:
    """A throwaway git repository with a base branch holding one registration."""

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="ssa-own-")
        run = lambda *a: subprocess.run(["git", *a], cwd=self.dir, check=True,  # noqa: E731
                                        capture_output=True, text=True)
        run("init", "-q", "-b", "main")
        run("config", "user.email", "t@t"); run("config", "user.name", "t")
        os.makedirs(os.path.join(self.dir, "entrants"))
        self.write("entrants/acme.json", {"entrant_id": "acme", "name": "Acme",
                                          "type": "firm", "method": "m", "github": "AcmeBot"})
        self.write("entrants/legacy.json", {"entrant_id": "legacy", "name": "L",
                                            "type": "llm", "method": "m"})
        run("add", "-A"); run("commit", "-q", "-m", "base")
        self.saved_root = vs.ROOT
        vs.ROOT = self.dir
        return self

    def write(self, rel, doc):
        with open(os.path.join(self.dir, rel), "w") as fh:
            json.dump(doc, fh)

    def __exit__(self, *exc):
        vs.ROOT = self.saved_root
        shutil.rmtree(self.dir, ignore_errors=True)


def refused(fn, *args):
    try:
        fn(*args)
    except SystemExit as err:
        return str(err.code) if err.code not in (0, None) else None
    return None


def test_a_registration_is_edited_only_by_its_github_owner_or_a_maintainer():
    with Repo() as repo:
        new = {"entrant_id": "acme", "name": "Acme v2", "type": "firm", "method": "m", "github": "AcmeBot"}
        assert vs.check_entrant_owner("entrants/acme.json", new, "acmebot", "main") is None, "owner, case-insensitive"
        assert vs.check_entrant_owner("entrants/acme.json", new, "jajamoa", "main") is None, "maintainer"
        assert refused(vs.check_entrant_owner, "entrants/acme.json", new, "someone-else", "main")
        stolen = dict(new, github="someone-else")
        assert refused(vs.check_entrant_owner, "entrants/acme.json", stolen, "someone-else", "main"), \
            "changing the owner field in the same pull request must not work"
        legacy = {"entrant_id": "legacy", "name": "L2", "type": "llm", "method": "m"}
        assert refused(vs.check_entrant_owner, "entrants/legacy.json", legacy, "anyone", "main"), \
            "a registration with no owner on record is a maintainer's to edit"
        fresh = {"entrant_id": "beta", "name": "B", "type": "firm", "method": "m", "github": "BetaLabs"}
        assert vs.check_entrant_owner("entrants/beta.json", fresh, "betalabs", "main") is None
        assert refused(vs.check_entrant_owner, "entrants/beta.json", fresh, "notbeta", "main"), \
            "a new registration must name its own author"
        assert vs.check_entrant_owner("entrants/beta.json", dict(fresh, github=None), None, None) is None, \
            "without --author (a local run) ownership is not checked"
        del repo
    print("ok test_a_registration_is_edited_only_by_its_github_owner_or_a_maintainer")


def test_forecasts_are_filed_only_by_the_entrant_owner():
    with Repo() as repo:
        assert vs.check_forecast_owner("forecasts/r1/acme.json", "acme", "acmebot", "main") is None
        assert vs.check_forecast_owner("forecasts/r1/acme.json", "acme", "ZhenzeMo", "main") is None
        assert refused(vs.check_forecast_owner, "forecasts/r1/acme.json", "acme", "someone-else", "main")
        assert refused(vs.check_forecast_owner, "forecasts/r1/nobody.json", "nobody", "someone-else", "main"), \
            "no registration, no forecast"
        # registered in the same pull request: the working-tree file counts
        repo.write("entrants/gamma.json", {"entrant_id": "gamma", "name": "G", "type": "firm",
                                           "method": "m", "github": "GammaCo"})
        assert vs.check_forecast_owner("forecasts/r1/gamma.json", "gamma", "gammaco", "main") is None
        assert refused(vs.check_forecast_owner, "forecasts/r1/gamma.json", "gamma", "other", "main")
    print("ok test_forecasts_are_filed_only_by_the_entrant_owner")


def test_the_validator_cli_takes_receipt_time_author_and_base():
    files, now, author, base = vs.parse_args(
        ["--now", "2026-09-14T11:59:00Z", "--author", "AcmeBot", "--base",
         "origin/main", "forecasts/r/acme.json", "entrants/acme.json"])
    assert files == ["forecasts/r/acme.json", "entrants/acme.json"]
    assert now.isoformat() == "2026-09-14T11:59:00+00:00"
    assert (author, base) == ("AcmeBot", "origin/main")
    files, now, author, base = vs.parse_args(["entrants/acme.json"])
    assert files == ["entrants/acme.json"] and now is None and author is None
    assert refused(vs.parse_args, ["--bogus", "x", "entrants/acme.json"])
    print("ok test_the_validator_cli_takes_receipt_time_author_and_base")


# --- what the bot merges by itself -------------------------------------------

def test_the_bot_merges_only_registration_files():
    """Season 0 admits outside entrants through an endpoint only, so the bot
    merges registrations and nothing else; a forecast file by pull request
    waits for a person."""
    ok, why = auto_merge.classify([{"filename": "entrants/acme.json", "status": "added"}])
    assert ok, why
    for bad in ([{"filename": "tools/validate_submission.py", "status": "modified"}],
                [{"filename": "entrants/acme.json", "status": "added"},
                 {"filename": "forecasts/yougov-2026-w38-approval/acme.json", "status": "added"}],
                [{"filename": "entrants/acme.json", "status": "removed"}],
                [{"filename": "forecasts/r/acme.json", "status": "renamed"}],
                [{"filename": ".github/workflows/auto-merge.yml", "status": "modified"}],
                [{"filename": "entrants/Acme.json", "status": "added"}],
                [{"filename": "forecasts/r/deep/acme.json", "status": "added"}],
                []):
        ok, why = auto_merge.classify(bad)
        assert not ok, bad
    print("ok test_the_bot_merges_only_registration_files")


def test_the_auto_merge_workflow_runs_in_the_base_repository_and_never_checks_out_the_pull_request():
    with open(os.path.join(ROOT, ".github", "workflows", "auto-merge.yml")) as fh:
        wf = fh.read()
    assert "workflow_run:" in wf and "validate-submissions" in wf
    assert "ref: main" in wf, "the tools that judge a pull request come from main"
    assert "pull_request.head" not in wf and "head_ref" not in wf, \
        "the workflow must not check out the pull request's code"
    assert "contents: write" in wf and "pull-requests: write" in wf
    assert "tools/auto_merge.py" in wf
    with open(os.path.join(ROOT, ".github", "workflows", "validate.yml")) as fh:
        v = fh.read()
    assert "--author" in v and "--base" in v, "PR-time validation must check ownership"
    with open(os.path.join(ROOT, ".github", "workflows", "refresh.yml")) as fh:
        r = fh.read()
    assert "SSA_SIGNING_KEY: ${{ secrets.SSA_SIGNING_KEY }}" in r
    assert "SSA_ENTRANT_KEY" not in r and "INTAKE_REPO_TOKEN" not in r
    print("ok test_the_auto_merge_workflow_runs_in_the_base_repository_and_never_checks_out_the_pull_request")


def test_the_landing_audit_reads_the_receipt_the_bot_recorded():
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import audit_landing
    d = tempfile.mkdtemp(prefix="ssa-land-")
    try:
        run = lambda *a: subprocess.run(["git", *a], cwd=d, check=True, capture_output=True, text=True)  # noqa: E731
        run("init", "-q", "-b", "main"); run("config", "user.email", "t@t"); run("config", "user.name", "t")
        open(os.path.join(d, "f"), "w").write("x"); run("add", "f")
        run("commit", "-q", "-m", "Merge #7 from @acme: forecasts\n\nReceived-At: 2026-09-14T11:58:30Z\nAuto-merged after validation of abc")
        assert audit_landing.received_at("HEAD", root=d) == "2026-09-14T11:58:30Z"
        open(os.path.join(d, "f"), "w").write("y"); run("commit", "-q", "-am", "data refresh")
        assert audit_landing.received_at("HEAD", root=d) is None
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print("ok test_the_landing_audit_reads_the_receipt_the_bot_recorded")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("all signing and ownership tests passed")
