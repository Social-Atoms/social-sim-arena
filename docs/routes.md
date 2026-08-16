# Routes: how an entrant is reached, and when that is allowed to change

Written 2026-08-16, against `dev` at the merge of #32.

A **route** is where a model is actually called: which key opens it, which wire
protocol it speaks, which host serves it, and what the model is called on that
host. Every model has a direct route — its own vendor. Some also have an
**OpenRouter** route: one key, one OpenAI-compatible endpoint, many vendors
behind it.

This is not a convenience. It exists because of a failure mode no code change
fixes.

---

## 1. What happened

On **2026-08-14**, within about a day of each other:

| provider | what the API returned | entrants dead |
|---|---|---|
| Anthropic | `HTTP 400 — "This organization has been disabled."` | 4 |
| OpenAI | `HTTP 401 account_deactivated`, then after a new key, `HTTP 429 insufficient_quota` | 3 |

Seven of fifteen entrants, failing on every six-hourly refresh, while the round
locks kept arriving on schedule and did not wait for anyone. The pipeline was
working exactly as designed — `ssa.refresh` refuses to file a placeholder and
exits non-zero — so every run was red and seven leaderboard rows were empty.

The most likely trigger is that both accounts were being called from a
**mainland-China IP**, which neither provider serves. That explains two
independent providers acting within a day of each other, and it is not
something the arena's code can be written around.

---

## 2. Why the switch is manual

The obvious design is a failover: catch the error, retry on OpenRouter. That is
the wrong design here, for one reason.

**The endpoint is part of the condition, not a detail of it.** Two hosts can
serve different weights under one model name, quantise differently, or reach a
different reasoning depth. A forecast produced through OpenRouter is not
guaranteed to be the forecast the direct route would have produced.

So an automatic failover would move an entrant to a different endpoint
mid-season, on a transient 429, with nothing anywhere saying it happened — and
the leaderboard would go on comparing rows that were no longer comparable.

Instead:

- the switch is a repository **variable a human sets**, per entrant;
- it is **off unless set**, so merging the code moves nothing;
- every forecast records the route it came from.

## 3. Turning it on

Repository variable, `Settings → Secrets and variables → Actions → Variables`:

```
SSA_OPENROUTER = claude-opus,claude-opus-5,claude-sonnet,claude-fable,gpt-5.6-luna,gpt-5.6-sol,gpt-5.6-terra
```

or `SSA_OPENROUTER=1` for every routable model. The secret `OPEN_ROUTER` must
hold the key. A name that matches no model **raises** — a typo that quietly
routed nothing would leave the outage in place with the variable set, and the
run would fail exactly as it did before while the log said the fix was applied.

Locally, the same variable works in a `.env`.

## 4. What changes when a route changes, and what must not

**Must not change — the entrant id.** `claude-opus` stays `claude-opus`. The id
is the forecast path, the entrant record and the leaderboard row all at once; a
model reached a different way is the same entrant, or every file it ever filed
is orphaned.

**Changes, and must — the cache key.** `call_identity` is `model @ base`, and
both the live harness and the backtest hash it. Rerouting changes both halves,
so every cached reply correctly misses and is re-requested. Reusing a reply the
current endpoint never produced would put one model's forecast under another's
name.

**Changes, and is recorded — the reasoning depth.** OpenRouter normalises
effort to one parameter across vendors, so the vendor-specific blocks do not
apply:

| route | what is sent | 
|---|---|
| OpenAI, direct | `reasoning_effort: xhigh` |
| Anthropic, direct | `thinking: {type: adaptive}`, `output_config: {effort: max}` |
| any, via OpenRouter | `reasoning: {effort: high}` |

`high` is the top of OpenRouter's unified scale and it is **not** the same
depth as either direct ceiling. A routed entrant is the same weights asked to
think somewhat less hard. That is a real difference, and it is why every
forecast's notes carry `via=direct` or `via=openrouter` — so a file says which
endpoint answered it without anyone having to reconstruct the configuration
that was live that week.

**Refused outright — the `web` context.** OpenRouter serves Claude and GPT but
does not proxy Anthropic's `web_search_20260209` or OpenAI's hosted
`web_search`. Its own `:online` plugin is third-party search bolted on, which
is a different condition wearing the same name. A routed model asked for the
`web` condition raises by name rather than being attempted, because the bad
outcome is not a 400 — it is a host that accepts the unknown field, ignores it,
and files a "web" forecast identical to its closed-book twin.

## 5. What is deliberately not routable

`qwen-3.7` and `qwen-3.8`. OpenRouter carries `qwen/qwen3.7-max`, the **floating
alias**, and not the dated `qwen3.7-max-2026-05-20` snapshot this entrant is
pinned to. An alias that rolls forward mid-season silently swaps the entrant,
and scores from before and after the swap are not comparable. The pin is worth
more than the redundancy, and the DashScope key works.

## 6. If you are adding a model to the route table

1. Check the slug against `https://openrouter.ai/api/v1/models` — keyless and
   free. Do not derive it: `vendor/model` is a convention, not a rule (Kimi is
   `moonshotai/`, GLM is `z-ai/`, Grok is `x-ai/`), and a derived id that is
   wrong is a 404 at lock time.
2. No `:batch` suffix — a different product with its own latency contract — and
   no `~` prefix, which marks a floating alias.
3. If the model is in `WEB_CAPABLE`, nothing needs doing: the `web` refusal is
   keyed on the route, not on the model.
4. Do not change `cutoffs.CUTOFFS`. A route is where the weights are served
   from, not which weights they are; the training cutoff belongs to the model.

`tests/test_routes.py` checks all of the above and runs keyless in the refresh
job itself, because what it guards is that job's own configuration.
