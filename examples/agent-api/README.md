# SSA Agent API starter example

Run the dependency-free fixture server:

```bash
python examples/agent-api/server.py
```

In another terminal, send the sample chat-completions request:

```bash
curl -sS http://127.0.0.1:8787/v1/chat/completions \
  -H 'Content-Type: application/json' \
  --data-binary @examples/agent-api/request.json
```

The response should match the shape in `response.json`. Production submissions
must use public HTTPS; localhost and HTTP are used only by this local fixture.
If the submitted endpoint requires authentication, it must accept:

```text
Authorization: Bearer YOUR_KEY
```

See `docs/agent-api.md` for timing, retry, validation, trace, and crosstab rules.
