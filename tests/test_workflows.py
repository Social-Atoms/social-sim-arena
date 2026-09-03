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
    # This workflow is the only one that runs the agent-template contracts
    # below, so a change to the template they guard has to start it.
    assert body.count('- "templates/**"') == 2


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


def test_the_candidate_job_can_see_a_batch_it_just_created():
    """A new batch is a new *file*, and `git diff` does not see untracked ones.

    The first version of this job asked `git diff --quiet` and would have
    reported "nothing new" every Thursday while the generator wrote a hundred
    candidates beside it -- a cron that silently does nothing, which is worse
    than no cron because nobody goes looking for it.
    """
    body = text(".github/workflows/candidates.yml")
    assert "git add -A questions/candidates/" in body, \
        "the job must stage before it asks what changed"
    # Every `git diff` that is a command rather than prose, wherever it sits in
    # the line. Two near misses while writing this check are the reason it is
    # spelled out: `command_with` returns the first matching line, which is the
    # comment explaining the trap, and the real invocation is `if git diff ...`,
    # so a `startswith("git diff")` reads neither.
    import re
    invocations = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for m in re.finditer(r"git diff[^;|&\n]*", stripped):
            invocations.append(m.group(0).strip())
    assert invocations, "the job no longer checks whether anything changed"
    for cmd in invocations:
        assert "--cached" in cmd, \
            f"`{cmd}` cannot see a newly created batch file"
    print("ok test_the_candidate_job_can_see_a_batch_it_just_created")


def test_the_candidate_job_never_promotes_anything():
    """Drafting is automated; promotion is not, and that is the whole design.

    A round is contamination-proof because a person froze it in a commit before
    the answer existed. A job that wrote `questions/season0.json` would keep the
    schedule and lose the property.
    """
    body = text(".github/workflows/candidates.yml")
    # Commands, not prose. The job's own comments explain that it refuses to
    # touch the season file, and a check that scans the whole text cannot tell
    # the explanation from the act -- the same mistake as reading `git diff`
    # out of a comment above.
    commands = [ln.strip() for ln in body.splitlines()
                if ln.strip() and not ln.strip().startswith("#")]
    touching = [c for c in commands if "season0.json" in c]
    assert not touching, \
        f"the candidate job touches the season file: {touching}"
    assert "--write" in body and "questions/candidates/" in body
    assert "gh pr create" in body, "candidates arrive as a pull request to read"
    # A scheduled workflow runs only from the default branch. Anyone reading
    # this file on `dev` and expecting Thursday to happen is about to lose a
    # week, so the file has to say so.
    assert "default branch" in body, \
        "the job must say that `schedule:` fires only from the default branch"
    assert "workflow_dispatch" in body, "and give a way to run it before then"
    assert "--base dev" in body, "never straight to main"
    # Offline and free: the generator holds no key and makes no request, so a
    # source being down cannot fail this job and this job cannot spend money.
    for bad in ("API_KEY", "secrets.", "OPENAI", "ANTHROPIC"):
        assert bad not in body, f"the candidate job references {bad}"
    print("ok test_the_candidate_job_never_promotes_anything")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print("ok", test.__name__)
    print(f"{len(tests)} passed")
