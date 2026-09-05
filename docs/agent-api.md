# Agent API contract

Status: versioned intake contract for `ssa-agent-api-v2`.

The API route is deliberately small. A participant exposes one HTTPS URL.
The arena POSTs each question to it as one JSON object and reads one JSON
object back; the arena's existing request runner, validation, and filing
window do the rest. There is no arena SDK or long-running process for
participants to install.

## Registration

A `route` block in `entrants/<entrant_id>.json` is what turns a rehearsed
endpoint into one the season calls:

```json
"route": {
  "kind": "agent_api",
  "url": "https://api.acme.example/forecast",
  "auth": "bearer"
}
```

`auth` is optional and defaults to `"bearer"`.
A registration with no `route` is unchanged in meaning: that entrant hands its
forecasts over itself, which is what every registration written before this
field did.

**The credential is derived, never declared.** The arena reads the bearer token
from `SSA_ENTRANT_KEY_<ENTRANT_ID>`, computed from the entrant id. There is no
field naming it, and the schema refuses one: a registration that could name its
own variable could name `ANTHROPIC_API_KEY`, and the arena would put our
provider key in an `Authorization` header addressed to the `url` in the
same file. It would equally let one participant ask to be called with another's
credential.

Three further rules follow from a registration being a public file:

- **HTTPS only**, checked in the schema when the registration is opened as a
  pull request and again in `ssa/participants.py` every time a token is about
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

Until the key is installed the entrant is simply not on the roster: not called,
and not failing the run every six hours. Onboarding is not a fault, and a red
run every cycle through a week of it teaches everyone to ignore the colour.

## Endpoint and authentication

The arena calls:

```text
POST {url}
Content-Type: application/json
Authorization: Bearer {api_key}    # only when a key was supplied
```

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
only, non-scored fixtures, files nothing. It checks the transport, **all three
round shapes**, idempotency of a repeated `request_id`, and — when a key is
configured — that a *wrong* bearer token is refused with 401 or 403. Probing
one scalar fixture is how an endpoint passes today and fails on the first
profile round of the season, after the deadline, which is why each shape is a
separate verdict.

A key is read from an environment variable named with `--key-env`, never from
an argument: a key on the command line is in `ps` output, in shell history, and
in the log of whoever pastes the command into an issue. Transient failures are
retried; a 4xx is an answer and is never retried.

`--entrant <id>` reads the route from `entrants/<id>.json` — base URL and the
derived credential variable — so the maintainer-side probe aims at the endpoint
the season will actually call rather than at whatever was typed on the command
line. `--url` becomes optional when it does. It also refuses to probe a
registration whose `entrants/<id>.json` carries `"status": "revoked"`. Revocation stops both
routes at once: the bundle intake refuses an upload before issuing a receipt,
and the probe refuses to make the call. A revocation the arena does not honour
is a revocation in name only.

The browser's **Test connection** button sends the fixed non-scored fixture in
`examples/agent-api/request.json`. It checks HTTPS, optional Bearer auth, a
JSON object in reply, and a valid continuous forecast. Browser
testing may additionally require CORS. Before activation, the private intake
service repeats the same probe server-side, where CORS does not apply.

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

The static design preview never stores a key. Production registration first
creates a non-secret intake, then uses a short-lived upload to place the key in
an encrypted secret store. Keys are redacted from packets and logs, may be
rotated without changing the entrant, and are deleted when the entrant is
revoked.

## Starter example

The dependency-free example under `examples/agent-api/` implements the exact
test fixture and includes sample request, response, and curl commands. It is a
contract example, not a production server.
