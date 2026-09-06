"""Validate every forecast/entrant file changed by one landed commit.

Both ``lock-audit.yml`` and the bot-authored refresh workflow call this exact
entry point. GitHub deliberately does not start a second workflow for a push
made with the repository ``GITHUB_TOKEN``; invoking the audit before that push
is therefore part of the refresh commit transaction, not optional duplication.

Usage:
    python tools/audit_landing.py HEAD^ HEAD
"""
import argparse
import os
import subprocess
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def changed_contract_files(before="HEAD^", after="HEAD", root=ROOT):
    """Changed, non-deleted forecast/entrant JSON paths for one git range."""
    proc = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=d", before, after,
         "--", "forecasts", "entrants"],
        cwd=root, capture_output=True, text=True, check=True)
    out = []
    for raw in proc.stdout.splitlines():
        path = raw.strip()
        if not path.endswith(".json"):
            continue
        if path.startswith("forecasts/") or (
                path.startswith("entrants/") and path.count("/") == 1):
            out.append(path)
    return out


def received_at(commit="HEAD", root=ROOT):
    """The `Received-At:` trailer tools/auto_merge.py writes into a merge
    commit: the moment the submission reached GitHub. Without it, lateness is
    judged at landing, as before."""
    proc = subprocess.run(["git", "log", "-1", "--format=%B", commit], cwd=root,
                          capture_output=True, text=True, check=True)
    for line in proc.stdout.splitlines():
        if line.startswith("Received-At:"):
            return line.split(":", 1)[1].strip()
    return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("before", nargs="?", default="HEAD^")
    ap.add_argument("after", nargs="?", default="HEAD")
    args = ap.parse_args(argv)
    files = changed_contract_files(args.before, args.after)
    if not files:
        print("nothing to audit")
        return 0
    print("\n".join(files))
    received = received_at(args.after)
    extra = ["--now", received] if received else []
    if received:
        print(f"judging lateness at receipt time {received} (Received-At trailer)")
    return subprocess.call(
        [sys.executable, os.path.join(ROOT, "tools", "validate_submission.py"),
         *extra, *files], cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
