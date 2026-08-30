# Participant quickstart

There are two technical routes. Confirm the route with the Arena maintainers
before a scored entry; registration acceptance and the batch deadline still
govern whether a valid technical payload is admitted.

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
reference that read Sunday's news.

The rule, the calendar, and the dated cutover are in
[`docs/submission-window.md`](submission-window.md).

## Route 1 — Repository forecast files

This is the operational fallback and the easiest contract to inspect.

1. Fork and clone the repository.
2. Choose a stable entrant ID matching
   `^[a-z0-9][a-z0-9_.-]{1,47}$`.
3. Add `entrants/<entrant_id>.json` matching
   [`schema/entrant.schema.json`](../schema/entrant.schema.json).
4. Read open rounds from
   [`questions/season0.json`](../questions/season0.json). The round's
   `target_type` determines whether the answer is a scalar distribution, a
   complete profile, or a ranking.
5. Add `forecasts/<round_id>/<entrant_id>.json` matching
   [`schema/forecast.schema.json`](../schema/forecast.schema.json).
6. Before the batch deadline, validate and open a pull request:

```bash
pip install -r requirements.txt
python tools/validate_submission.py \
  entrants/<entrant_id>.json \
  forecasts/<round_id>/<entrant_id>.json
```

The validator prints `OK` and the canonical SHA-256 for an accepted file, and
names the batch and its deadline when a round is late. Validation is repeated
when the pull request lands: opening a pull request before the deadline is not
enough if it merges after it.

Distribution rounds require uncertainty: submit either `mean` plus a strictly
positive `sd`, or at least three ordered quantiles including `0.5`. Profile
rounds require every named cell. Ranking rounds require the exact length and,
when supplied, the fixed item basket in predicted order.

## Route 2 — OpenAI-compatible Agent API

Use this route when the Arena should call your service and file forecasts on
your behalf. Start with the dependency-free fixture:

```bash
python examples/agent-api/server.py
```

In a second terminal:

```bash
curl -sS http://127.0.0.1:8787/v1/chat/completions \
  -H 'Content-Type: application/json' \
  --data-binary @examples/agent-api/request.json
```

Your production endpoint must use HTTPS and follow
[`docs/agent-api.md`](agent-api.md). Do not commit or email an API key. The
maintainers must accept the registration and arrange the approved credential
path and server-side contract probe before the endpoint becomes active.

## What is public

Accepted entrant identity and method metadata are public. Forecast files,
their canonical hashes, lock records, and post-resolution scores are public.
Contact details, endpoint URLs, credentials, and private review records are not
public. The fuller intake and retention design is documented in
[`docs/submission-design.md`](submission-design.md).

## Before asking for help

Include your route, entrant ID, round ID, validator output (without secrets),
and the commit containing the submission. Never paste credentials into an
issue, pull request, forecast `notes`, or chat transcript.

