"""Registration pull requests are checked, answered and merged with nobody online.

tools/auto_merge.py --pr is driven by .github/workflows/registration-intake.yml
(pull_request_target). These tests run its decision path against stubbed
GitHub calls and real validation of real files, and check the workflow never
runs contributor code.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import auto_merge  # noqa: E402

GOOD_KEY = "hAruP3GrxDbLZGFhwde0cZ0ZcIgr28B+PvSIdYs+mWE="


class Stub:
    """Records every GitHub call; answers the reads from a fixture."""

    def __init__(self, pr, files, comments=()):
        self.pr, self.files, self.comments = pr, files, list(comments)
        self.calls, self.merged = [], None

    def gh(self, *args, input_text=None):
        self.calls.append(args)
        if args[:1] == ("api",) and args[-2:-1] == ("--jq",) and "/comments" in args[-3]:
            return "\n".join(json.dumps(c) for c in self.comments)
        if args[:2] == ("pr", "merge"):
            self.merged = args
        return ""


def run(stub, entrant_doc, filename, validate_real=True):
    """Drive main() with GitHub stubbed and the file written where validation reads it."""
    saved = (auto_merge.gh, auto_merge.pull_request, auto_merge.changed_files,
             auto_merge.fetch_head_files)
    path = os.path.join(ROOT, filename)
    existed = os.path.exists(path)
    try:
        auto_merge.gh = stub.gh
        auto_merge.pull_request = lambda n: stub.pr
        auto_merge.changed_files = lambda n: stub.files
        auto_merge.fetch_head_files = lambda n, files, sha=None: [f["filename"] for f in files]
        if entrant_doc is not None and not existed:
            with open(path, "w") as fh:
                json.dump(entrant_doc, fh)
        os.environ.setdefault("GITHUB_REPOSITORY", "arena/test")
        return auto_merge.main(["--pr", str(stub.pr["number"]),
                                "--received", "2026-11-20T00:00:00Z"])
    finally:
        (auto_merge.gh, auto_merge.pull_request, auto_merge.changed_files,
         auto_merge.fetch_head_files) = saved
        if entrant_doc is not None and not existed and os.path.exists(path):
            os.remove(path)


def pr(author="newcomer", **kw):
    base = {"number": 9, "draft": False, "state": "open", "base": "main",
            "head": "abc123def4567890", "author": author, "title": "Register"}
    base.update(kw)
    return base


def doc(eid, github):
    return {"entrant_id": eid, "name": "Test", "type": "participant",
            "github": github, "keys": [{"id": "k1", "alg": "ed25519",
                                        "public": GOOD_KEY, "revoked": False}]}


def replies(stub):
    return [a for a in stub.calls if a[:2] == ("pr", "comment")]


def labels(stub):
    return [a for a in stub.calls if a[:2] == ("pr", "edit")]


def test_a_valid_registration_by_its_author_is_answered_labelled_and_merged():
    s = Stub(pr(), [{"filename": "entrants/zz-intake-ok.json", "status": "added"}])
    assert run(s, doc("zz-intake-ok", "newcomer"), "entrants/zz-intake-ok.json") == 0
    assert s.merged, "a valid registration must merge"
    assert "--match-head-commit" in s.merged and "abc123def4567890" in s.merged
    assert "--squash" in s.merged
    body = replies(s)[0][-1]
    assert auto_merge.MARKER in body and "merged into `main` automatically" in body
    assert "registration-accepted" in labels(s)[0]


def test_an_id_need_not_match_the_account_only_github_must():
    # #282: id "briar", account "Bramble-Zhang". The rule binds `github`, not the id.
    s = Stub(pr("Bramble-Zhang"), [{"filename": "entrants/zz-briar.json", "status": "added"}])
    assert run(s, doc("zz-briar", "Bramble-Zhang"), "entrants/zz-briar.json") == 0
    assert s.merged


def test_a_registration_naming_someone_else_is_answered_with_the_fix_and_not_merged():
    s = Stub(pr(), [{"filename": "entrants/zz-intake-bad.json", "status": "added"}])
    try:
        rc = run(s, doc("zz-intake-bad", "someone-else"), "entrants/zz-intake-bad.json")
    except SystemExit as e:          # validate() shells out, so main returns 1
        rc = e.code
    assert rc == 1 and s.merged is None
    body = replies(s)[0][-1]
    assert "did not pass" in body and '"newcomer"' in body
    assert "registration-needs-fix" in labels(s)[0]


def test_the_empty_github_field_from_the_form_fails_until_the_suggestion_is_committed():
    s = Stub(pr(), [{"filename": "entrants/zz-intake-empty.json", "status": "added"}])
    rc = run(s, doc("zz-intake-empty", ""), "entrants/zz-intake-empty.json")
    assert rc == 1 and s.merged is None


def test_anything_but_a_registration_waits_for_a_maintainer():
    for files in ([{"filename": "site/index.html", "status": "modified"}],
                  [{"filename": "entrants/a.json", "status": "added"},
                   {"filename": ".github/workflows/registration-intake.yml", "status": "modified"}],
                  [{"filename": "entrants/a.json", "status": "removed"}],
                  [{"filename": "forecasts/r/a.json", "status": "added"}]):
        s = Stub(pr(), files)
        assert run(s, None, "entrants/a.json") == 0
        assert s.merged is None, files
        assert "needs-maintainer" in labels(s)[0]
        assert "not one the bot merges" in replies(s)[0][-1]


def test_closed_draft_and_non_main_pull_requests_are_left_alone():
    for p in (pr(state="closed"), pr(draft=True), pr(base="dev")):
        s = Stub(p, [{"filename": "entrants/zz-x.json", "status": "added"}])
        assert run(s, None, "entrants/zz-x.json") == 0
        assert s.calls == [], p


def test_the_reply_is_edited_in_place_not_repeated():
    old = {"id": 77, "login": "github-actions[bot]", "body": auto_merge.MARKER + "\nold"}
    s = Stub(pr(), [{"filename": "site/x.html", "status": "added"}], comments=[old])
    run(s, None, "entrants/a.json")
    assert not replies(s), "an existing reply must be edited, not duplicated"
    assert any(a[:3] == ("api", "-X", "PATCH") and "issues/comments/77" in a[3]
               for a in s.calls)


def test_the_workflow_never_checks_out_or_runs_the_pull_request():
    with open(os.path.join(ROOT, ".github", "workflows", "registration-intake.yml")) as fh:
        wf = fh.read()
    assert "pull_request_target:" in wf and "branches: [main]" in wf
    assert "ref: main" in wf and "persist-credentials: false" in wf
    for bad in ("head.sha", "head.ref", "head_ref", "pull_request.head",
                "${{ github.event.pull_request.title", "${{ github.event.pull_request.body"):
        assert bad not in wf, bad
    assert "tools/auto_merge.py --pr" in wf


if __name__ == "__main__":
    tests = [fn for k, fn in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
