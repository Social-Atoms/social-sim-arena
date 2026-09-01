"""Landing validation covers both human pushes and refresh-bot commits.

Run: PYTHONPATH=. python tests/test_landing_audit.py
"""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import audit_landing


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(root, *args):
    subprocess.run(args, cwd=root, check=True, capture_output=True, text=True)


def test_changed_contract_files_uses_the_landed_commit_range():
    with tempfile.TemporaryDirectory() as root:
        _run(root, "git", "init", "-q")
        _run(root, "git", "config", "user.name", "test")
        _run(root, "git", "config", "user.email", "test@example.com")
        os.makedirs(os.path.join(root, "forecasts", "round"))
        os.makedirs(os.path.join(root, "entrants"))
        os.makedirs(os.path.join(root, "site"))
        for path, body in (
            ("forecasts/round/a.json", "{}"),
            ("entrants/a.json", "{}"),
            ("site/data.json", "{}"),
        ):
            with open(os.path.join(root, path), "w") as f:
                f.write(body)
        _run(root, "git", "add", ".")
        _run(root, "git", "commit", "-qm", "base")
        before = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        with open(os.path.join(root, "forecasts", "round", "a.json"), "w") as f:
            f.write('{"changed": true}')
        with open(os.path.join(root, "entrants", "a.json"), "w") as f:
            f.write('{"changed": true}')
        with open(os.path.join(root, "site", "data.json"), "w") as f:
            f.write('{"changed": true}')
        _run(root, "git", "add", ".")
        _run(root, "git", "commit", "-qm", "landing")
        assert audit_landing.changed_contract_files(before, "HEAD", root) == [
            "entrants/a.json", "forecasts/round/a.json"]


def test_refresh_audits_after_rebase_and_before_its_bot_push():
    refresh = open(os.path.join(ROOT, ".github", "workflows", "refresh.yml")).read()
    pull = refresh.index("git -c rebase.autoStash=true pull --rebase origin main")
    audit = refresh.index("python tools/audit_landing.py HEAD^ HEAD")
    push = refresh.index("git push", audit)
    assert pull < audit < push
    lock = open(os.path.join(ROOT, ".github", "workflows", "lock-audit.yml")).read()
    assert "python tools/audit_landing.py HEAD^ HEAD" in lock
    assert "GITHUB_TOKEN" in lock and "does not trigger" in lock


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
