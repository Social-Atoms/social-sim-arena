# Registering an entrant

No OAuth, no account database, no typed-in username, and no bot with write access
to anything. A participant generates an Ed25519 key pair locally with
`ssh-keygen` and only ever publishes the public half --
see [generating-a-key.md](generating-a-key.md).

1. Fork this repository, once per account. The page's **1 · Fork this
   repository** button is the ordinary GitHub fork page, and it has to come
   first: GitHub cannot serve a prefilled new-file page to someone without write
   access, so step 2 without a fork answers 422 and shows a fork prompt instead.
2. Fill in the details and the public key, then click **2 · Open the
   prefilled file**. It opens `entrants/<id>.json` on this repository, and
   because you cannot write here the editor offers only "create a new branch and
   start a pull request" -- in your own fork it would preselect a direct commit,
   which opens nothing and reports nothing. Open the pull request against
   **`main`**.
3. The file arrives with `"github": ""`. Validation fails on purpose until the
   identity is bound. A `pull_request_target` workflow reads the pull request
   author from GitHub's own metadata and posts an inline suggestion filling that
   line in.
4. Check the account and the public key shown, then click **Commit suggestion**.
   That records the binding in Git history, by your own hand. No username is ever
   typed. A pull request opened from the wrong account should be closed and
   resubmitted from the right one.
5. The existing ownership and schema checks then pass, and a maintainer reviews
   and merges.

## Why the base branch is `main`

It was `qa-signed-intake-registry` for a while, and that was a mistake: **a fork
starts with the default branch only**, so a non-default base forced every
registrant to create that branch in their own fork by hand before they could open
the pull request. That is our plumbing leaking into someone else's first five
minutes.

Isolation is what the whole fork is for. An internal QA branch adds nothing to it
and is not something a registrant should have to know about. `qa-signed-intake-
registry` still exists, but only as the home of the signed-lifecycle rehearsal
report that `site/signed-lifecycle.html` reads.

## Where the safety actually comes from

`tools/validate_submission.py` refuses a new registration whose `github` field is
not the pull request's author, and refuses a change to an existing file by anyone
but its recorded owner. **That check is the security boundary, not the workflow.**

The binding workflow only removes the typing step and turns a confusing
validation failure into one click. If it never runs, nothing unsafe happens: the
`github` field stays empty, validation stays red, and the pull request cannot
merge.

That is why it can safely use `pull_request_target`. It never checks out
contributor code, never runs a contributor script, and never touches repository
contents. Its only write permission is `pull-requests: write`, which is to say
comments. It must live on the default branch to be discoverable.

Existing entrants are never rebound automatically.
