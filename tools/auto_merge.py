"""Merge a participant's pull request into main once CI has validated it.

Run two ways, with the same rules:

  - by `.github/workflows/registration-intake.yml` (`--pr N`) as soon as a
    registration pull request is opened or updated. That workflow is
    `pull_request_target`, so it runs for a first-time contributor's fork
    without a maintainer approving the run;
  - by `.github/workflows/auto-merge.yml` (`--sha`) after `validate-submissions`
    succeeds on a pull request.

It decides, with no person online, whether that pull request is one the arena
merges by itself:

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


PR_FIELDS = ("{number, draft, state, base: .base.ref, head: .head.sha, "
             "author: .user.login, title}")


def pull_requests_for(sha):
    out = gh("api", f"repos/{os.environ['GITHUB_REPOSITORY']}/commits/{sha}/pulls",
             "--jq", ".[] | " + PR_FIELDS)
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def pull_request(number):
    out = gh("api", f"repos/{os.environ['GITHUB_REPOSITORY']}/pulls/{number}",
             "--jq", PR_FIELDS)
    return json.loads(out)


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


def fetch_head_files(number, files, sha=None):
    """Copy only the changed JSON files from the pull request head into main's tree.

    Data, never code: `classify` has already refused any path that is not
    `entrants/<id>.json`, and nothing from the pull request is executed. With
    `sha`, the files come from exactly that commit, the one the merge is pinned to.
    """
    subprocess.run(["git", "fetch", "-q", "origin", f"pull/{number}/head:refs/pr/{number}"],
                   cwd=ROOT, check=True)
    names = [f["filename"] for f in files]
    subprocess.run(["git", "checkout", "-q", sha or f"refs/pr/{number}", "--", *names],
                   cwd=ROOT, check=True)
    return names


def validate(names, received, author):
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "validate_submission.py"),
         "--now", received, "--author", author, "--base", "origin/main", *names],
        cwd=ROOT, capture_output=True, text=True)
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


# One reply per pull request, edited in place on every run, so a registrant
# who pushes three fixes sees one current answer rather than three old ones.
MARKER = "<!-- ssa-registration-intake -->"
LABELS = {
    "accepted": ("registration-accepted", "0e8a16",
                 "Registration passed the checks and was merged"),
    "fix": ("registration-needs-fix", "d93f0b",
            "Registration failed a check; the bot reply says what to change"),
    "maintainer": ("needs-maintainer", "fbca04",
                   "Not a registration the bot merges; a maintainer will look"),
}


def reply_body(state, author, detail=""):
    """The sticky reply for one outcome. Pure, so it is tested directly."""
    if state == "accepted":
        text = (f"Thanks @{author}. Your registration passed every check and is "
                "merged into `main` automatically; nobody needs to approve it.\n\n"
                "Next: send signed forecasts with the private key that matches "
                "the public key in your file. See `docs/participant-quickstart.md` "
                "and `docs/signed-submissions.md`. Your entrant appears on the "
                "board after its first scored question.")
    elif state == "fix":
        text = (f"Thanks @{author}. The registration did not pass yet, so it is "
                "not merged. Push a fix to this same pull request and the check "
                "runs again by itself.\n\nWhat the check said:\n\n```\n"
                f"{detail[-3000:]}\n```\n\nCommon fixes: `\"github\"` must be "
                f"`\"{author}\"` (the author of this pull request); the file name "
                "must be `<entrant_id>.json`; each key needs a unique `id`, "
                "`\"alg\": \"ed25519\"` and the 44-character base64 public key "
                "ending in `=`.")
    else:
        text = (f"Thanks @{author}. This pull request is not one the bot merges "
                f"by itself ({detail}). A maintainer will look at it.")
    return f"{MARKER}\n{text}"


def comment(number, text):
    """Create the sticky reply, or edit it if this pull request already has one."""
    repo = os.environ["GITHUB_REPOSITORY"]
    out = gh("api", "--paginate", f"repos/{repo}/issues/{number}/comments",
             "--jq", ".[] | {id, login: .user.login, body}")
    for line in out.splitlines():
        if not line.strip():
            continue
        c = json.loads(line)
        if c["login"] == "github-actions[bot]" and MARKER in (c["body"] or ""):
            gh("api", "-X", "PATCH", f"repos/{repo}/issues/comments/{c['id']}",
               "-f", f"body={text}")
            return
    gh("pr", "comment", str(number), "--body", text)


def label(number, state):
    """Leave exactly one of the intake labels on the pull request."""
    name, color, desc = LABELS[state]
    gh("label", "create", name, "--color", color, "--description", desc, "--force")
    others = [LABELS[k][0] for k in LABELS if k != state]
    gh("pr", "edit", str(number), "--add-label", name,
       *sum((["--remove-label", o] for o in others), []))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--sha", help="head sha the validation ran on")
    which.add_argument("--pr", type=int,
                       help="pull request number (registration-intake.yml)")
    ap.add_argument("--received", required=True,
                    help="ISO-8601 time the submission was received")
    ap.add_argument("--run-url", default="", help="link to the run")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if args.pr is not None:
        pr = pull_request(args.pr)
    else:
        prs = [p for p in pull_requests_for(args.sha) if p["head"] == args.sha]
        if not prs:
            print(f"no pull request has head {args.sha}; nothing to do")
            return 0
        pr = prs[0]
    number, author, sha = pr["number"], pr["author"], pr["head"]
    print(f"pull request #{number} by @{author}: {pr['title']}")

    if pr.get("state", "open") != "open":
        print(f"pull request is {pr['state']}; nothing to do")
        return 0
    if pr["base"] != "main":
        print(f"base is {pr['base']}, not main; a person merges this")
        return 0
    if pr["draft"]:
        print("draft; not merged")
        return 0

    def answer(state, detail=""):
        if args.dry_run:
            print(f"dry run: would reply [{state}] and label it")
            return
        comment(number, reply_body(state, author, detail))
        label(number, state)

    files = changed_files(number)
    ok, why = classify(files)
    if not ok:
        print(f"not a submission-only pull request: {why}")
        answer("maintainer", why)
        return 0

    names = fetch_head_files(number, files, sha)
    valid, report = validate(names, args.received, author)
    print(report)
    if not valid:
        answer("fix", report)
        return 1

    if args.dry_run:
        print(f"dry run: would merge #{number}")
        return 0
    answer("accepted")
    body = (f"Received-At: {args.received}\n"
            f"Auto-merged after validation of {sha[:12]}"
            + (f" ({args.run_url})" if args.run_url else ""))
    # --squash, not --merge: the ruleset on main requires a linear history, so a
    # merge commit is refused. One commit per pull request, authored by its opener.
    # --match-head-commit: if the author pushed again after this run read the
    # files, the merge is refused and the next run judges the new head.
    gh("pr", "merge", str(number), "--squash", "--match-head-commit", sha,
       "--subject", f"Merge #{number} from @{author}: {pr['title']}",
       "--body", body)
    print(f"merged #{number}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
