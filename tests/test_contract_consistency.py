"""Operator, workflow, harness and site state one executable contract.

Run: PYTHONPATH=. python tests/test_contract_consistency.py
"""
import os


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(path):
    with open(os.path.join(ROOT, path)) as f:
        return f.read()


def test_mock_contract_matches_fail_closed_automation():
    harness = _read("ssa/harness.py")
    workflow = _read(".github/workflows/refresh.yml")
    site = _read("site/docs.html")
    assert 'ALLOW_MOCK = os.environ.get("SSA_ALLOW_MOCK") == "1"' in harness
    assert "profile/ranking paths never mock" in harness
    assert "automation never enables" in workflow.lower()
    assert "SSA_ALLOW_MOCK=1" in site and "forbidden in automation" in site
    assert "file clearly-labeled MOCK forecasts" not in workflow
    assert "three samples, median" not in site


def test_cadence_and_resolution_docs_match_the_workflow():
    workflow = _read(".github/workflows/refresh.yml")
    readme = _read("README.md")
    site = _read("site/docs.html")
    assert 'cron: "17 */6 * * *"' in workflow
    assert "python -m ssa.resolve --write" in workflow
    assert "every\nsix hours" in readme
    assert "Every refresh runs <code>python -m ssa.resolve --write</code>" in site
    assert "manual for now, cron later" not in site
    assert "daily cron" not in site


def test_deadline_is_exposed_and_used_by_every_site_path():
    api = _read("ssa/questionnaire_api.py")
    for path in ("site/index.html", "site/leaderboard.html", "site/submit.html"):
        page = _read(path)
        assert "deadline||" in page or "deadline ||" in page, path
        assert "Date.parse(" in page, path
    assert '"deadline": _iso(_round_deadline(round_data))' in api
    site = _read("site/docs.html")
    assert "it is not a participant due date" in site
    assert "filed before the lock" not in site


def test_search_corpus_and_stamp_docs_use_the_participant_deadline():
    search = _read("ssa/adapters/search.py")
    conditions = _read("docs/conditions.md")
    stamps = _read("ssa/stamps.py")
    assert "filing window before the participant\ndeadline" in search
    assert "before the common\nparticipant deadline" in conditions
    assert "when submissions closed" in stamps
    assert "common\nmodel-filing window opened" in conditions
    assert "search happens at lock time" not in (search + conditions)


def test_landing_audit_contract_is_shared_by_both_workflows():
    command = "python tools/audit_landing.py HEAD^ HEAD"
    assert command in _read(".github/workflows/lock-audit.yml")
    assert command in _read(".github/workflows/refresh.yml")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
