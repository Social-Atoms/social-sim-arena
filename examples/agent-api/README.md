# SSA Agent API starter example

Run the dependency-free fixture server:

```bash
python examples/agent-api/server.py
```

Run the contract test against it:

```bash
python tools/probe_agent_api.py --url http://127.0.0.1:8787/forecast
```

That checks the transport, all three round shapes, and idempotency. Probing
only a scalar fixture is how an endpoint passes today and fails on the first
profile round of the season, after the deadline — so each shape is a separate
verdict.

Or send the single fixed request by hand:

```bash
curl -sS http://127.0.0.1:8787/forecast \
  -H 'Content-Type: application/json' \
  --data-binary @examples/agent-api/request.json
```

The response should match the shape in `response.json`.

## Authentication

```bash
SSA_EXAMPLE_API_KEY=secret python examples/agent-api/server.py
SSA_PROBE_KEY=secret python tools/probe_agent_api.py \
    --url http://127.0.0.1:8787/forecast --key-env SSA_PROBE_KEY
```

With a key configured the probe also sends a *wrong* one and requires a 401 or
403. An endpoint that answers an unauthenticated caller is an endpoint anyone
can file forecasts through under your entrant id, and configuring a key without
ever testing that it is enforced is the common version of that mistake.

The probe reads the key from an environment variable you name, never from an
argument: a key on the command line is in `ps` output, in shell history, and in
the log of whoever pastes the command into an issue. Never commit or email one.

## Timeouts

```bash
python examples/agent-api/server.py --port 8788 --delay 5
python tools/probe_agent_api.py --url http://127.0.0.1:8788/forecast --timeout 1
```

`--delay` stalls the reply so a timeout can be seen against something real. The
arena allows 15s to connect and 600s to read, but a round that needs most of
that has no room left for a retry before the batch deadline.

Production submissions must use public HTTPS; localhost and HTTP are used only
by this local fixture. See `docs/agent-api.md` for timing, retry, validation,
trace, and crosstab rules, and `docs/participant-quickstart.md` for the route
in context.
