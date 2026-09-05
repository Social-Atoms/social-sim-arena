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

## Signatures

The server verifies the arena's signature on every request (`verify_signature`
in `server.py`, the fifteen lines a participant copies) against the public
keys in `site/keys.json`; the probe signs with the published test key, so the
two agree out of the box. `--no-verify` accepts unsigned requests, which is a
participant's right and the probe reports as "does not verify".

```bash
python examples/agent-api/server.py
python tools/probe_agent_api.py --url http://127.0.0.1:8787/forecast
```

The probe also sends one request with a corrupted signature and reports
whether it was refused with 401 or 403. Nothing here is a secret of yours: the
only private key in the exchange is the arena's, and the test key is public by
design.

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
