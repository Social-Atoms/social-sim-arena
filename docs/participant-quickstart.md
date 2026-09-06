# Participant quickstart

One registration, two ways to hand over a forecast. This page is the whole
onboarding path: read it, and you can complete a valid non-scored submission
without sending anyone a credential or a file by hand.

Confirm the route with the maintainers before a scored entry. Registration
acceptance is separate from technical validity — a payload can be perfect and
still not be admitted.

## The weekly rhythm

The Arena runs in weekly batches. **Everything in a batch shares one deadline:
Monday 12:00Z.** The batch is published a week ahead, so you have a full week
to work on it.

Each round still locks at `release − 48h`, but that is our clock, not yours —
a round locks 0 to 7 days *after* the deadline you were held to. You only ever
need to track one moment a week.

Filing early is allowed and costs you nothing: the deadline is the same for
everyone, and scores are computed against a baseline frozen at that same
deadline, so an entrant who files on Tuesday is not compared against a
reference that read Sunday's news. Waiting is not an edge and is not scored as
one.

The rule, the calendar, and the dated cutover are in
[`docs/submission-window.md`](submission-window.md).

## Step 1 — register

```bash
git clone https://github.com/Social-Atoms/social-sim-arena
cd social-sim-arena
pip install -r requirements.txt
```

Choose a stable entrant id matching `^[a-z0-9][a-z0-9_.-]{1,47}$`, add
`entrants/<entrant_id>.json` matching
[`schema/entrant.schema.json`](../schema/entrant.schema.json), and check it:

```bash
python tools/validate_submission.py entrants/<entrant_id>.json
```

The id is what every forecast is filed under and it does not change. Setting
`"status": "revoked"` later stops both routes at once.

## Step 2 — pick a route

| | Route A — we call you | Route B — you upload a bundle |
|---|---|---|
| you run | one HTTPS endpoint that answers a JSON question with a JSON forecast | anything; you produce a JSON file |
| we call it | once per round, 72–48h before the batch deadline | never |
| you watch | uptime | one deadline a week |
| contract | [`docs/agent-api.md`](agent-api.md) | [`docs/bundle-submission.md`](bundle-submission.md) |

Route A suits a service that is already running. Route B suits a research group
that would rather run its own pipeline on its own schedule and hand over a file.
Both end at the same place: one
`forecasts/<round_id>/<entrant_id>.json` record per answer, scored identically.

## Route B — the weekly bundle

A bundle **is** a batch: one deadline, many rounds, mixed shapes. Rehearse the
whole path first — nothing below enters the season:

```bash
python examples/bundle/entrant.py \
    --bundle examples/bundle/sandbox-batch.json \
    --entrant demo_bundle_entrant \
    --anchor examples/bundle/sandbox-anchors.json \
    --out /tmp/response.json

python tools/validate_bundle.py examples/bundle/sandbox-batch.json /tmp/response.json

python tools/accept_bundle.py /tmp/response.json \
    --bundle examples/bundle/sandbox-batch.json \
    --sandbox --write --out /tmp/sandbox-forecasts
```

That is the complete loop: a question bundle in, a response bundle out, a local
validation with the same code the arena runs, and the scored-form forecast
records it normalises into. `examples/bundle/entrant.py` is standard library
only; replace its `answer_for` function with your model and the rest of the
file is already a working submission client.

Then look at a real week:

```bash
python tools/make_bundle.py --list
python tools/make_bundle.py --batch batch-2026-09-14 --out /tmp/real.json
```

In Season 0 the answers go in by pull request: `tools/accept_bundle.py
RESPONSE.json --bundle BUNDLE.json --write` turns your response into one
`forecasts/<round_id>/<entrant_id>.json` per answer; commit them and open a
pull request against `main`. CI validates the files and merges them by itself
when they pass and the pull request's author is your registered GitHub
account. Lateness is judged at the moment your pull request reached GitHub,
not at the merge. (The website upload endpoint exists but is not switched on
this season.)

Everything else — the two schemas, the three answer shapes, the receipt, the
per-round rejection reasons, re-uploads — is in
[`docs/bundle-submission.md`](bundle-submission.md).

## Route A — the Agent API

Use this when the Arena should call your service and file forecasts on your
behalf. Start with the dependency-free fixture:

```bash
python examples/agent-api/server.py
```

In a second terminal, run the contract test against it:

```bash
python tools/probe_agent_api.py --url http://127.0.0.1:8787/forecast
```

Then point the same command at your own endpoint:

```bash
python tools/probe_agent_api.py --url https://api.example.com/forecast
```

It signs its requests with the published test key the way the Arena signs
live ones, and checks the transport, all three round shapes, idempotency, and
whether a *bad* signature is refused (reported either way; verifying is your
choice). No credential changes hands in either direction: the Arena signs,
you verify with the public key in
[`site/keys.json`](https://social-simulation-arena.com/keys.json).

Your production endpoint must use HTTPS and follow
[`docs/agent-api.md`](agent-api.md).

### Registering the endpoint

[`submit.html`](https://social-simulation-arena.com/submit.html) tests your
endpoint, then builds `entrants/<entrant_id>.json` and opens GitHub with it
prefilled; open it as a pull request against `main`. CI validates it and
merges it by itself. The file looks like this:

```json
{
  "entrant_id": "acme-forecast",
  "name": "Acme Forecast",
  "type": "firm",
  "method": "One or two sentences: what generates the forecasts.",
  "github": "acme-bot",
  "route": {
    "kind": "agent_api",
    "url": "https://api.acme.example/forecast"
  }
}
```

`github` is the account that opens the pull request. Only it, or a maintainer,
can change the file later or file forecasts under this id; changing the URL is
another pull request, merged the same way. The entrant id never changes.

**There is no key anywhere.** The Arena signs every request it sends you; you
verify with the public key. Nothing you hold is secret, so nothing can leak.

The maintainers can run the same probe against your registration rather than
against a URL somebody typed:

```bash
python tools/probe_agent_api.py --entrant <entrant_id>
```

`"status": "revoked"` stops the call at the point of dialling, not only in the
probe. A change merged before Friday 12:00 UTC applies to that week's calls.

## The operational fallback

Repository forecast files still work and remain the recovery path when either
route is unavailable:

1. Read the open rounds from [`questions/season0.json`](../questions/season0.json).
   A round's `target_type` fixes whether the answer is a scalar distribution, a
   complete profile, or a ranking.
2. Add `forecasts/<round_id>/<entrant_id>.json` matching
   [`schema/forecast.schema.json`](../schema/forecast.schema.json).
3. Before the batch deadline, validate and open a pull request:

```bash
python tools/validate_submission.py forecasts/<round_id>/<entrant_id>.json
```

The validator prints `OK` and the canonical SHA-256 for an accepted file, and
names the batch and its deadline when a round is late. Validation is repeated
when the pull request lands: opening one before the deadline is not enough if
it merges after it.

Distribution rounds require uncertainty: either `mean` plus a strictly positive
`sd`, or at least three ordered quantiles including `0.5`. Profile rounds
require every named cell. Ranking rounds require the exact length and, when
supplied, the fixed item basket in predicted order.

## Where your answer shows up

Nothing here is a black box, and none of it waits for the season to end.

- **[The weekly calendar](https://social-simulation-arena.com/leaderboard.html#batches)**
  — which batch is open, what is in it, and when the next one is handed over.
  A batch is published a week before its deadline.
- **[The rounds table](https://social-simulation-arena.com/leaderboard.html#rounds)**
  — every round, and once one resolves it expands to the outcome, the exact
  rule it was resolved under, and **every entrant's forecast sorted by error**,
  yours among them. If a resolution was ever corrected, the correction and the
  reason for it are shown there too.
- **[The boards](https://social-simulation-arena.com/leaderboard.html#leaderboard)**
  — four of them. Backtest and Season 0 are scored with CRPS; the population
  profile board with the energy score, and the ranking board with a distance on
  lists. Only `skill` is comparable across them, and it means the same thing on
  each: how much better than copying the last release.
- **[Source freshness](https://social-simulation-arena.com/leaderboard.html#freshness)**
  — how old every source behind those numbers is, against the interval it is
  judged by, with a link to the archived file each figure came from.

Your entrant appears on a board after its first scored round, and in a round's
own result table as soon as that round resolves.

## What is public

Accepted entrant identity and method metadata are public. Forecast files, their
canonical hashes, lock records, and post-resolution scores are public.

Contact details, endpoint URLs, credentials, and private review records are not
public. Credentials are never in Git, never in an issue, never in an email, and
never in a forecast's `notes`; they are deleted on revocation and can be rotated
without changing your entrant id. The fuller intake and retention design is in
[`docs/submission-design.md`](submission-design.md).

## Before asking for help

Include your route, entrant id, batch id, round id, validator output (without
secrets), and either the commit containing the submission or the
`response_sha256` from your receipt. Never paste credentials into an issue, a
pull request, a forecast's `notes`, or a chat transcript.
