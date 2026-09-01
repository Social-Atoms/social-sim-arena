"""Workflow trigger and durable-archive contracts.

Run: PYTHONPATH=. python tests/test_workflows.py
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def text(path):
    with open(os.path.join(ROOT, path)) as f:
        return f.read()


def command_with(needle, body):
    return next(line.strip() for line in body.splitlines() if needle in line)


def test_questions_workflow_triggers_on_its_own_contract_changes():
    body = text(".github/workflows/questions.yml")
    # Once in pull_request.paths and once in push.paths. Without both, editing
    # the workflow can silently skip the very check being repaired.
    assert body.count('- ".github/workflows/questions.yml"') == 2
    assert body.count('- ".github/workflows/refresh.yml"') == 2
    assert "PYTHONPATH=. python tests/test_workflows.py" in body


def test_refresh_commits_every_irreplaceable_archive():
    body = text(".github/workflows/refresh.yml")
    staged = command_with("git add -A", body).split()
    reset = command_with("git checkout --", body).split()
    for path in ("trends", "wikitop", "sources", "search", "replies"):
        assert path in staged, (path, staged)
        assert path in reset, (path, reset)


def test_the_agent_template_opens_its_pull_request_from_a_fork():
    """Checking out the arena makes `origin` the arena, where an external
    contributor cannot push. Their fork is the only branch they can write, and
    a pull request from it has to name which fork the branch is on."""
    body = text("templates/agent-cron.yml")
    assert "repository: Social-Atoms/social-sim-arena" not in body
    assert "${{ github.repository_owner }}:$BRANCH" in body


def test_the_agent_template_names_the_weekly_deadline():
    """The template is offered from site/docs.html, which says Monday 12:00Z.
    It predates the batch calendar and was the last file still teaching the
    per-round lock it replaced."""
    body = text("templates/agent-cron.yml")
    assert "Monday 12:00Z" in body
    for retired in ("before lock counts", "each round's lock"):
        assert retired not in body, retired


def test_residential_courier_stages_all_three_source_archives():
    body = text("tools/local_source_archive.py")
    line = command_with('git("add", "-A"', body)
    for path in ('"civiqs"', '"trends"', '"wikitop"'):
        assert path in line, (path, line)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print("ok", test.__name__)
    print(f"{len(tests)} passed")
