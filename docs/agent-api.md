# Agent API contract

Status: versioned intake contract for `ssa-agent-api-v1`.

The API route is deliberately small. A participant exposes one HTTPS
OpenAI-compatible chat-completions base URL; the arena reuses its existing
request runner, response-text extraction, validation, and filing window. There
is no arena SDK or long-running process for participants to install.

## Endpoint and authentication

The arena calls:

```text
POST {base_url}/chat/completions
Content-Type: application/json
Authorization: Bearer {api_key}    # only when a key was supplied
```

The request body is the ordinary chat-completions shape:

```json
{
  "model": "ssa-agent",
  "messages": [{"role": "user", "content": "{...JSON prompt envelope...}"}]
}
```

Dedicated agent endpoints may ignore the fixed `model` value. The JSON string
in `content` conforms to
[`schema/agent-api-request.schema.json`](../schema/agent-api-request.schema.json).
The endpoint returns an OpenAI-compatible response. The arena reads the same
response text locations already supported by `ssa/harness.py`, preferring
`choices[0].message.content`.

The decoded response content conforms to
[`schema/agent-api-response.schema.json`](../schema/agent-api-response.schema.json).
It contains `schema_version` and one typed `forecast`. `reasoning_trace` and
`crosstabs` are optional.

## Call and lock policy

- The first call is due between 72 and 48 hours before `lock_at`.
- The current runner uses a 15-second connection timeout and a 600-second read
  timeout.
- A valid forecast filed in that window is final and is never called again.
- A missing or invalid forecast is retried by the existing scheduled refresh,
  rather than a second retry service, until 30 minutes before the lock.
- HTTP/authentication errors, timeouts, malformed JSON, and schema failures are
  recorded as failed attempts. They never create a forecast.
- Every request is idempotent by entrant, round, and input hash.
- The server's receipt time controls the lock. Client timestamps are ignored.

The moment an endpoint's forecasts are due is the **batch deadline**, not the
round's `lock_at` — see [`docs/submission-window.md`](submission-window.md).
The call window above is the arena's buying schedule, not a participant-visible
due date.

## Contract test

```bash
python examples/agent-api/server.py
python tools/probe_agent_api.py --base-url http://127.0.0.1:8787/v1
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

`--entrant <id>` refuses to probe a registration whose
`entrants/<id>.json` carries `"status": "revoked"`. Revocation stops both
routes at once: the bundle intake refuses an upload before issuing a receipt,
and the probe refuses to make the call. A revocation the arena does not honour
is a revocation in name only.

The browser's **Test connection** button sends the fixed non-scored fixture in
`examples/agent-api/request.json`. It checks HTTPS, optional Bearer auth, the
OpenAI response wrapper, decoded JSON, and a valid continuous forecast. Browser
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
