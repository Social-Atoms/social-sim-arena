"""Merge a participant's pull request into main once CI has validated it.

Run by `.github/workflows/auto-merge.yml` after `validate-submissions` succeeds
on a pull request. It decides, with no person online, whether that pull
request is one the arena merges by itself:

  - the base branch is `main` and the pull request is not a draft;
  - every changed file is a registration (`entrants/<id>.json`), none
    deleted, and every `<id>` is owned
    by the pull request's author (`tools/validate_submission.py --author`);
  - the files, taken from the pull request's head commit, validate again with
    the receipt time set to the moment GitHub started the validation run,
    so a slow queue cannot turn an on-time submission into a late one.

Anything else -- a change to code, a file under someone else's entrant id, a
validation failure -- is left for a maintainer, with a comment saying why.

What this never does: check out or execute the pull request's code. It runs
from `main`'s own copy of the tools and reads only the JSON files it merges,
so a pull request cannot change the rules that judge it. It merges as
`github-actions[bot]`, which `main-guard.yml` allows, and the merge commit
carries `Received-At: <time>` so `tools/audit_landing.py` judges lateness by
that moment rather than by the merge.
"""
import argparse
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRANT = r"[a-z0-9][a-z0-9_.-]{1,47}"
REGISTRATION = re.compile(rf"^entrants/({ENTRANT})\.json$")
FORECAST = re.compile(rf"^forecasts/[^/]+/({ENTRANT})\.json$")


def gh(*args, input_text=None):
    proc = subprocess.run(["gh", *args], cwd=ROOT, capture_output=True,
                          text=True, input=input_text)
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {proc.stderr.strip()}")
    return proc.stdout


def pull_requests_for(sha):
    out = gh("api", f"repos/{os.environ['GITHUB_REPOSITORY']}/commits/{sha}/pulls",
             "--jq", ".[] | {number, draft, base: .base.ref, head: .head.sha, "
                     "author: .user.login, title}")
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def changed_files(number):
    out = gh("api", "--paginate",
             f"repos/{os.environ['GITHUB_REPOSITORY']}/pulls/{number}/files",
             "--jq", ".[] | {filename, status}")
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def classify(files):
    """(ok, reason). ok means every file is a submission the bot may merge."""
    if not files:
        return False, "no files changed"
    for f in files:
        name, status = f["filename"], f["status"]
        if status in ("removed", "renamed"):
            return False, f"{name}: {status}; only a maintainer removes or renames"
        if FORECAST.match(name):
            # Season 0 admits outside entrants through an endpoint only; a
            # forecast file arriving by pull request is not a route anyone
            # was offered, so a person looks at it rather than the bot.
            return False, (f"{name}: forecast files are not merged by the bot "
                           "this season; left for a maintainer")
        if not REGISTRATION.match(name):
            return False, f"{name}: not a registration file; left for a maintainer"
    return True, ""


def fetch_head_files(number, files):
    """Check out only the changed JSON files from the pull request head."""
    subprocess.run(["git", "fetch", "-q", "origin", f"pull/{number}/head:refs/pr/{number}"],
                   cwd=ROOT, check=True)
    names = [f["filename"] for f in files]
    subprocess.run(["git", "checkout", "-q", f"refs/pr/{number}", "--", *names],
                   cwd=ROOT, check=True)
    return names


def validate(names, received, author):
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "validate_submission.py"),
         "--now", received, "--author", author, "--base", "origin/main", *names],
        cwd=ROOT, capture_output=True, text=True)
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


def comment(number, text):
    gh("pr", "comment", str(number), "--body", text)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sha", required=True, help="head sha the validation ran on")
    ap.add_argument("--received", required=True,
                    help="ISO-8601 time the validation run was created")
    ap.add_argument("--run-url", default="", help="link to the validation run")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    prs = [p for p in pull_requests_for(args.sha) if p["head"] == args.sha]
    if not prs:
        print(f"no open pull request has head {args.sha}; nothing to do")
        return 0
    pr = prs[0]
    number, author = pr["number"], pr["author"]
    print(f"pull request #{number} by @{author}: {pr['title']}")

    if pr["base"] != "main":
        print(f"base is {pr['base']}, not main; a person merges this")
        return 0
    if pr["draft"]:
        print("draft; not merged")
        return 0

    files = changed_files(number)
    ok, why = classify(files)
    if not ok:
        print(f"not a submission-only pull request: {why}")
        if not args.dry_run:
            comment(number, f"Not merged automatically: {why}. A maintainer "
                            "will look at it.")
        return 0

    names = fetch_head_files(number, files)
    valid, report = validate(names, args.received, author)
    print(report)
    if not valid:
        if not args.dry_run:
            comment(number, "Validation failed at receipt time "
                            f"{args.received}, so this was not merged:\n\n```\n"
                            f"{report[-3000:]}\n```")
        return 1

    if args.dry_run:
        print(f"dry run: would merge #{number}")
        return 0
    body = (f"Received-At: {args.received}\n"
            f"Auto-merged after validation of {args.sha[:12]}"
            + (f" ({args.run_url})" if args.run_url else ""))
    # --squash, not --merge: the ruleset on main requires a linear history, so a
    # merge commit is refused. One commit per pull request, authored by its opener.
    gh("pr", "merge", str(number), "--squash",
       "--subject", f"Merge #{number} from @{author}: {pr['title']}",
       "--body", body)
    print(f"merged #{number}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
