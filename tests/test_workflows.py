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
    # Discovery, not a hand-written list. The list this replaced named 14 of
    # 51 suites and skipped `test_scoring.py`; two skipped suites sat red on
    # dev while this workflow reported success. Asserting the loop rather than
    # a step named after this file is also what keeps *this* test honest --
    # the old assertion passed as long as one line mentioned it, which was
    # true even while most of the suite went unrun.
    assert "for t in tests/test_*.py; do" in body, \
        "questions.yml no longer discovers test suites"
    listed = [ln for ln in body.splitlines()
              if "python tests/test_" in ln and "for t in" not in ln]
    assert not listed, f"suites are hand-listed again: {listed}"
    # This workflow is the only one that runs the agent-template contracts
    # below, so a change to the template they guard has to start it.
    assert body.count('- "templates/**"') == 2
    # The bundle endpoint's deployment is asserted in tests/test_bundle_api.py,
    # which never runs on a pull request that changes only those files unless
    # they are listed here.
    assert body.count('- "api/**"') == 2
    assert body.count('- "vercel.json"') == 2


def test_ci_runs_every_suite_that_exists():
    """The discovered set must be the committed set, minus what is gitignored.

    A loop is only as good as its glob. This walks the same directory CI
    walks and requires that nothing is excluded by accident -- the two
    provider suites are kept local deliberately (see `.gitignore`), and
    anything else missing means a suite exists that no one runs.
    """
    import subprocess
    on_disk = {f for f in os.listdir(os.path.join(ROOT, "tests"))
               if f.startswith("test_") and f.endswith(".py")}
    tracked = subprocess.run(
        ["git", "ls-files", "tests/test_*.py"], cwd=ROOT,
        capture_output=True, text=True).stdout.split()
    tracked = {os.path.basename(f) for f in tracked}
    local_only = on_disk - tracked
    assert local_only <= {"test_harness.py", "test_model_backtest.py"}, \
        f"untracked suites CI can never see: {sorted(local_only)}"
    assert len(tracked) >= 40, f"only {len(tracked)} suites tracked"
    print("ok test_ci_runs_every_suite_that_exists")


def test_every_workflow_file_parses_as_yaml_with_its_triggers():
    """A workflow that does not parse is a workflow GitHub silently drops:
    the run shows up as a failure with zero jobs and `workflow_dispatch`
    answers "Workflow does not have 'workflow_dispatch' trigger". One lost
    indent in a `run: |` block did exactly that on dev (run 34011704426)."""
    import glob
    import yaml
    for path in sorted(glob.glob(os.path.join(ROOT, ".github", "workflows", "*.yml"))):
        with open(path) as fh:
            doc = yaml.safe_load(fh)
        on = doc.get("on", doc.get(True))
        assert isinstance(on, (dict, list, str)) and on, f"{path}: no triggers"
        assert doc.get("jobs"), f"{path}: no jobs"
    print("ok test_every_workflow_file_parses_as_yaml_with_its_triggers")


def test_every_workflow_pins_a_python_the_code_supports():
    """CI's version, the documented floor, and what a contributor installs are
    three different numbers unless something checks. `.python-version` used to
    pin 3.12, which was installed nowhere and made every bare `python` in the
    repository fail, including the one a participant is told to run."""
    import glob
    import re
    floor = (3, 10)
    seen = 0
    for path in sorted(glob.glob(os.path.join(ROOT, ".github/workflows/*.yml"))):
        for m in re.finditer(r'python-version:\s*"?([0-9]+)\.([0-9]+)',
                             text(os.path.relpath(path, ROOT))):
            seen += 1
            got = (int(m.group(1)), int(m.group(2)))
            assert got >= floor, f"{os.path.basename(path)} pins {got}"
    assert seen, "no workflow pins a python version"
    # `.python-version` is NOT a developer preference and must not be deleted:
    # `@vercel/python` reads it to pick the runtime for `api/*.py`, and
    # `tests/test_questionnaire_api.py` pins it to 3.12 for that reason. It is
    # a deployment artifact that pyenv also happens to obey, which is why a
    # contributor whose pyenv lacks 3.12 sees every bare `python` in the
    # repository fail. That is a local install problem with a local fix, not a
    # reason to change what Vercel is told.
    pin = os.path.join(ROOT, ".python-version")
    assert os.path.exists(pin), "Vercel needs .python-version to pick a runtime"
    assert tuple(int(n) for n in text(".python-version").strip().split(".")[:2]) \
        >= floor, ".python-version fell below the supported floor"
    print("ok test_every_workflow_pins_a_python_the_code_supports")


def test_a_manual_agent_probe_publishes_and_refreshes_the_dev_result():
    probe = text(".github/workflows/source-probe.yml")
    preview = text(".github/workflows/preview.yml")
    agent_job = probe.split("agent-endpoint-probe:", 1)[1]
    assert "contents: write" in agent_job and "actions: write" in agent_job
    assert 'probe.record_public_result("site/agent-probes.json", result)' in probe
    assert 'git add site/agent-probes.json' in probe
    assert '"show"' in probe and 'f"HEAD:entrants/{entrant_id}.json"' in probe
    assert 'gh workflow run preview.yml --ref "${GITHUB_REF_NAME}"' in probe
    # A token-authored probe commit will not trigger push workflows, so the
    # dispatched run itself has to be allowed to move the stable dev alias.
    assert "github.event_name == 'workflow_dispatch'" in preview
    alias = preview.split("- name: stable alias for the dev branch", 1)[1]
    assert "workflow_dispatch" in alias.split("- name:", 1)[0]
    assert "PREVIEW_BRANCH" in preview
    assert "github.rest.pulls.list" in preview
    print("ok test_a_manual_agent_probe_publishes_and_refreshes_the_dev_result")


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
