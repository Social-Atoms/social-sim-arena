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

## 2. Two ways in, and the difference matters

**The endpoint is part of the condition, not a detail of it.** Two hosts can
serve different weights under one model name, quantise differently, or reach a
different reasoning depth. A forecast produced through OpenRouter is not
guaranteed to be the forecast the direct route would have produced.

That is not an argument against falling back — it is an argument for the
fallback being *narrow, visible, and self-undoing*. There are two ways an
entrant reaches OpenRouter, and they answer different needs:

| | **Standby** (automatic) | **Force** (`SSA_OPENROUTER`) |
|---|---|---|
| when | the vendor route fails **terminally** | always |
| set by | nobody — it is the default when `OPEN_ROUTER` exists | a human, per entrant |
| reverts | by itself, the run after the account is fixed | when the variable is removed |
| use it for | an outage | a deliberate, whole-season choice |

### What makes the standby safe

**It only triggers on a failure that will still be there in six hours.** A 500,
a read timeout, a plain rate limit — those are fixed by waiting, and switching
endpoints on one of them would put two endpoints' forecasts in one season for
reasons nobody recorded. A disabled organisation, a dead key, a zero balance, a
model not served here — those are not fixed by waiting.

A `429` is deliberately **not** terminal on its own. It is the same status code
for "you are going too fast" and for "you have no money", and only the response
body tells them apart. Treating every 429 as terminal would send a burst of
ordinary rate limiting straight to the standby and bill it.

**A fallback forecast keeps the standby's own input hash, not the vendor's.**
This is the mechanism that makes it self-undoing. The moment the vendor account
comes back, the direct hash no longer matches what is on disk, the next run
re-asks the vendor, and the entrant is upgraded out of the standby without
anyone having to notice it had been demoted. Storing the *direct* hash instead
would pin the entrant to the standby for the rest of the season.

**The standby's own cache is checked before it is billed.** A fallback forecast
never matches the direct hash, so without this every six-hourly run would
re-buy an answer to a prompt that had not changed.

**One dead account costs one failed probe, not one per entrant.** The first
terminal failure marks that route dead *for the process*, and the remaining
entrants on it skip straight to the standby. Nothing is written down, so the
next run tests the vendor again — persisting it would turn a temporary outage
into a permanent reroute.

**The run says so.** `ssa.refresh` prints every route that fell back, and every
forecast produced that way carries `via=openrouter` in its notes. A silent
fallback is the failure this whole design is shaped against: the site would
keep rendering, the leaderboard would keep updating, and four entrants would
have quietly moved to a different endpoint at a lower reasoning depth.

**The backtest never falls back.** `ssa/model_backtest.py` calls the provider
directly and is not routed through this path. A backtest whose releases came
from two endpoints is not a comparable mean CRPS, and the fix there is the
force switch — one endpoint for the whole run — not a per-call retry.

## 3. Forcing a route for a whole season

Repository variable, `Settings → Secrets and variables → Actions → Variables`:

```
SSA_OPENROUTER = claude-opus,claude-opus-5,claude-sonnet,claude-fable,gpt-5.6-luna,gpt-5.6-sol,gpt-5.6-terra
```

or `SSA_OPENROUTER=1` for every routable model. The secret `OPEN_ROUTER` must
hold the key. A name that matches no model **raises** — a typo that quietly
routed nothing would leave the outage in place with the variable set, and the
run would fail exactly as it did before while the log said the fix was applied.

Locally, the same variable works in a `.env`.

**The standby needs no variable at all** — only the `OPEN_ROUTER` secret. If it
is set, every model in the route table has somewhere to go when its vendor
stops answering. Delete the secret and the arena is back to failing loudly,
which is also a valid choice.

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
