# Agent API contract

Status: versioned intake contract for `ssa-agent-api-v2`.

The API route is deliberately small. A participant exposes one HTTPS URL.
The arena POSTs each question to it as one JSON object and reads one JSON
object back; the arena's existing request runner, validation, and filing
window do the rest. There is no arena SDK or long-running process for
participants to install.

## Registration

A registration is a pull request against `main` adding
`entrants/<entrant_id>.json`. `submit.html` builds the file after the endpoint
passes the browser test and opens GitHub with it prefilled. CI validates it
(`tools/validate_submission.py`) and, when it passes, merges it by itself
(`.github/workflows/auto-merge.yml`); nobody has to be online. A `route` block
is what turns a rehearsed endpoint into one the season calls:

```json
{
  "entrant_id": "acme-forecast",
  "name": "Acme Forecaster",
  "organization": "Acme Research",
  "type": "participant",
  "contact": "team@acme.example",
  "github": "acme-bot",
  "route": {"kind": "agent_api", "url": "https://api.acme.example/forecast"}
}
```

`entrant_id` is the "Model / Team Name" on the form: lower-case, permanent,
the name of the file and of the row on the board. `contact` is optional and
public; leave it out to be reached through the `github` account.

`github` is the account that owns the entrant: only it, or a maintainer, may
later change this file or file forecasts under this id (checked against the
base branch's copy, so the owner cannot be rewritten by its own pull request).
A registration with no `route` is one of the arena's own entries (the
baselines and the models it runs itself); Season 0 admits outside entrants
through an endpoint only.

**There is no credential in the registration, and none anywhere else.** The
arena authenticates itself to the endpoint by signing every request
(`ssa/signing.py`, below); the participant verifies with the published public
key and hands the arena nothing. The schema refuses any field that could name
a credential: a registration that could name one could name
`ANTHROPIC_API_KEY`.

Three further rules follow from a registration being a public file:

- **HTTPS only**, checked in the schema when the registration is opened as a
  pull request and again in `ssa/participants.py` every time a request is about
  to be sent.
- **No standby.** Our own models fall back to OpenRouter when a route is
  terminally down. A participant's endpoint is the only place their forecast
  can come from; falling back would send their round to a vendor on our account
  and file the reply under their name. A participant whose endpoint is down has
  no forecast that round.
- **No `SSA_BASE_` override.** That escape hatch exists for a self-hosted
  gateway of ours. Applied to someone else's registration, an environment
  variable would silently redirect their round to a host their public record
  does not name.

Without the arena's own signing key (`SSA_SIGNING_KEY`, one Actions secret) no
participant is called at all: an unsigned request would be refused by a
verifying endpoint, and the participant could not tell our misconfiguration
from an attack.

## Endpoint and authentication

The arena calls:

```text
POST {url}
Content-Type: application/json
X-SSA-Key-Id: ssa-live
X-SSA-Timestamp: 1789030800
X-SSA-Signature: base64( Ed25519_sign( arena_private_key, "1789030800." + raw_body ) )
```

**Verifying the signature is the participant's choice and needs nothing
secret.** The public keys are published in
[`site/keys.json`](../site/keys.json) under a key id. To verify: take the raw
request body *as received* (re-serialising the JSON changes the bytes and the
signature will never match), check the Ed25519 signature over
`X-SSA-Timestamp + "." + body` with the key named by `X-SSA-Key-Id`, reject a
timestamp more than 300 seconds from your clock, and answer a repeated
`request_id` with the same forecast you gave before (the contract's
idempotency, which also makes a replayed request cost nothing).
`examples/agent-api/server.py` does this in fifteen lines with the
`cryptography` package; any language with Ed25519 can. An endpoint that does
not verify is not a risk to the arena, which alone files forecasts; it is
merely open to anyone who finds the URL and wants it to compute.

The key `ssa-test` is published *with* its private key so the browser test on
`submit.html` and `tools/probe_agent_api.py` can send signed requests; accept
both key ids. Rotation: a new key id is added to `keys.json`, both are valid
for a week or two, then the old one is removed.

The request body is the question envelope itself, one JSON object conforming
to
[`schema/agent-api-request.schema.json`](../schema/agent-api-request.schema.json).
There is no wrapper: what the contract page shows is byte-for-byte what
travels.

The response body is one JSON object conforming to
[`schema/agent-api-response.schema.json`](../schema/agent-api-response.schema.json).
It contains `schema_version` and one typed `forecast`, whose shape follows the
round's `target_type`: `{"mean", "sd"}` for `continuous_normal`,
`{"profile": {cell: {"mean", "sd"}}}` with every named cell for
`profile_energy`, `{"ranking": [...]}` for `ranking_list`. `reasoning_trace`
and `crosstabs` are optional.

## Browser test and CORS

The onboarding page tests an endpoint from the visitor's own browser, and a
browser sends no cross-origin POST until the endpoint answers a preflight
`OPTIONS`. Two headers are enough:

```
Access-Control-Allow-Origin: *
Access-Control-Allow-Headers: Content-Type, X-SSA-Key-Id, X-SSA-Timestamp, X-SSA-Signature
```

`examples/agent-api/server.py` does exactly that in a few lines. This is for
the page only: the arena's calls are server-to-server and never preflight, so
an endpoint without these headers works in the season and simply cannot be
tested from the page. When the browser is blocked the page says so and leaves
registration open, and `tools/probe_agent_api.py` is the check instead.

## Call and deadline policy

- The first call is due between 72 and 48 hours before the round's effective
  participant deadline (the weekly batch deadline after the dated cutover;
  `lock_at` for older rounds).
- The current runner uses a 15-second connection timeout and a 600-second read
  timeout.
- A valid forecast filed in that window is final and is never called again.
- A missing or invalid forecast is retried by the existing six-hourly refresh,
  rather than a second retry service, until 30 minutes before that deadline.
- HTTP/authentication errors, timeouts, malformed JSON, and schema failures are
  recorded as failed attempts. They never create a forecast.
- Every request is idempotent by entrant, round, and input hash.
- The server's receipt time controls the deadline. Client timestamps are ignored.

The moment an endpoint's forecasts are due is the **batch deadline**, not the
round's `lock_at` — see [`docs/submission-window.md`](submission-window.md).
The call window above is the arena's buying schedule. It ends before the same
participant-visible deadline applied to uploads and pull requests.

## Contract test

```bash
python examples/agent-api/server.py
python tools/probe_agent_api.py --url http://127.0.0.1:8787/forecast
```

`tools/probe_agent_api.py` is the runnable contract test: standard library
plus, optionally, `cryptography` to sign; non-scored fixtures; files nothing.
It signs with the published `ssa-test` key and checks the transport, **all
three round shapes**, idempotency of a repeated `request_id`, and whether a
*bad* signature is refused with 401 or 403 (reported either way: verifying is
optional). Probing one scalar fixture is how an endpoint passes today and fails
on the first profile round of the season, after the deadline, which is why
each shape is a separate verdict.

A maintainer probing with the live key names its variable with
`--signing-key-env`; the key itself is never an argument, because arguments end
up in `ps` output, shell history and pasted logs. Transient failures are
retried; a 4xx is an answer and is never retried.

`--entrant <id>` reads the route from `entrants/<id>.json` so the
maintainer-side probe aims at the endpoint
the season will actually call rather than at whatever was typed on the command
line. `--url` becomes optional when it does. It also refuses to probe a
registration whose `entrants/<id>.json` carries `"status": "revoked"`. Revocation stops both
routes at once: the bundle intake refuses an upload before issuing a receipt,
and the probe refuses to make the call. A revocation the arena does not honour
is a revocation in name only.

The browser's **Test connection** button sends the fixed non-scored fixture in
`examples/agent-api/request.json`, signed with the `ssa-test` key through
WebCrypto. It checks HTTPS, a JSON object in reply, and a valid continuous
forecast. Browser testing may additionally require CORS; the CLI probe does
not.

Then the cron's own path, against a real open round of each shape, signed
with the test key, filing nothing:

```bash
python tools/rehearse_endpoint.py --url https://your-host/forecast
```

If that prints `3 of 3 shapes filed and validated`, the refresh will file
for the endpoint; a failure line is the refresh's own error message.

## Forecasts and future fields

The response supports scalar distributions, outcome probabilities, profile
distributions, and ordered rankings. The arena normalizes the accepted result
into the existing public forecast contract; identity, round, and filing
metadata always come from the arena, never from the endpoint.

`crosstabs` may contain only dimensions and groups declared in the request's
`optional_crosstabs`. Undeclared cells are rejected. Omitting an optional
crosstab never invalidates the primary forecast.

`reasoning_trace` is archived even though it is not scored. Raw traces remain
private by default because they may include retrieved text or sensitive data.
The public forecast may carry only a content hash or private artifact reference.

## Secret handling

There is one secret in Route A and it is ours: the arena's Ed25519 private
key, held only as the Actions secret `SSA_SIGNING_KEY`, generated with
`tools/make_signing_key.py`. Participants hold none. Rotation is a new key id
in `site/keys.json`.

## Starter example

The dependency-free example under `examples/agent-api/` implements the exact
test fixture and includes sample request, response, and curl commands. It is a
contract example, not a production server.
