# Conditions, entrant ids, and the news corpus

Written 2026-08-14, against `main` at `dc245b5`. Every count in here was read
out of the live files.

An *entrant* in this arena is not a model. It is a model **plus a condition** —
what it was shown, and how it was asked. Two entrants can share weights and sit
on different leaderboard rows, and that is the point: the arena exists to
measure the conditions, not only the models.

---

## 1. The two axes

A condition is a **pair**, not a name.

**Context** — what the model is shown:

| id | shown |
|---|---|
| `none` | the question and nothing else |
| `recent10` | the last ten releases, the same history the nulls read |
| `news` | `recent10` plus a fixed news corpus frozen at the lock |
| `web` | `recent10` plus live search |

**Elicitation** — how it is asked:

| id | asked |
|---|---|
| `direct` | "give a mean and an sd" |
| `superfc` | the human forecasting protocol — base rate, decomposition, pre-mortem |
| `persona` | not asked to forecast at all; answers the real survey instrument as each weighted respondent, and the pollster's arithmetic makes the number |

The two are **orthogonal**, and every one of the twelve cells is meaningful.

This was not always the code's shape. `news` used to sit in a tuple called
`ELICITATION_VARIANTS` beside `persona` and `superfc` — but news changes *what
the model is shown* while those two change *how it is asked*, so the tuple mixed
the axes and made `news × superfc` unnameable. The arena could not express "the
forecasting protocol, on a model that has also read the news", which is an
obvious thing to want to measure.

Which cells run:

|  | `direct` | `superfc` | `persona` |
|---|---|---|---|
| `none` | ✅ season | ○ | ○ |
| `recent10` | ✅ season | ○ | ✗ |
| `news` | ○ | ○ | ✗ |
| `web` | ○ *(live-only)* | ○ | ✗ |

✅ = files every round by default. ○ = **nameable and runnable**, off until
`SSA_ELICITATION` asks for it. ✗ = **refused**, see below. Nothing but the two
season cells costs anything by default.

### 1.1 Why persona carries only `none`

`build_persona_prompt` takes a persona and the survey instrument and nothing
else: no series history, no release date, no mention that a forecast is wanted.
That is the design, and `ssa/personas.py` states it — *"everything the round
knows and the respondent would not know is withheld here on purpose; that
asymmetry is the experiment"*.

A real respondent does not know the tracker's own past readings. **A synthetic
one shown them has stopped being a respondent and become a forecaster wearing a
persona**, which is a different thing from what this arm claims to measure.

The enforcement is not a policy, it is a fact about the prompt:
`recent10 × persona` and `none × persona` build a **byte-identical** prompt
today, so offering both would put the same work on the leaderboard twice under
different names. `harness.ELICITATION_CONTEXTS` refuses it, and `resolve()`
refuses to read back an id it could not build — so `<model>-persona` is no
longer a valid id and `<model>-zeroshot-persona` is.

`news × persona` is the coherent extension and the one the literature actually
runs — a real respondent *does* read the news. It needs the digest wired into
`build_persona_prompt` first; until then it is refused rather than silently
producing a prompt with no news in it.

## 2. Entrant ids

Model, then the context suffix, then the elicitation suffix. **The default on
each axis is elided**, which is what keeps every id already on disk valid:

```
<model>[-<context, recent10 elided>][-<elicitation, direct elided>]
```

| cell | entrant id |
|---|---|
| `recent10` × `direct` | `claude-opus` |
| `none` × `direct` | `claude-opus-zeroshot` |
| `news` × `direct` | `claude-opus-news` |
| `web` × `direct` | `claude-opus-web` |
| `recent10` × `superfc` | `claude-opus-superfc` |
| `none` × `persona` | `claude-opus-zeroshot-persona` |
| **`news` × `superfc`** | **`claude-opus-news-superfc`** |
| **`none` × `persona`** | **`claude-opus-zeroshot-persona`** |

Context first, then elicitation, so the id reads in the order the prompt is
built: what it saw, then how it was asked. `harness.resolve()` reverses it by
stripping the longest suffix on each axis in turn and returns
`(model, context, elicitation)`; `harness.entrant_id()` builds it.

The id is load-bearing in three places at once — the forecast path
`forecasts/<round_id>/<entrant>.json`, the entrant record
`entrants/<entrant_id>.json`, and the leaderboard row — and
`tools/validate_submission.py` checks all three agree. `tests/test_conditions.py`
asserts that every id already committed resolves to the same condition and
rebuilds to the same string, because separating the axes must rename nothing.

## 3. The news corpus

The `news` condition gives every entrant in a round the **same** corpus, built
from the Wikipedia Current Events portal as those pages stood **at the round's
lock**. Not a per-model search: one text, archived, reproducible.

- **Window.** The seven days before the lock, up to but not including the lock
  day — a page for the lock day is mid-write and would differ between an entrant
  filed at 09:00 and one filed at 13:00.
- **Size.** Measured over 63 weekly lock dates from 2025-06-01 to 2026-08-12:
  median **20,185 characters, about 5,000 tokens**; range 10,583 to 30,708.
  Fourteen days at eight items a category ran to 47,291 characters (~11,800
  tokens) against a forecast prompt whose other content is roughly 400, which
  made the condition "the model sees the news and, somewhere in it, a question".
- **Which categories, and why they were not narrowed.** Measured per category
  over the same lock dates, the two most relevant to every target here are the
  two smallest:

  | category | share of corpus |
  |---|---|
  | Armed conflicts and attacks | 29.8% |
  | Law and crime | 18.4% |
  | Disasters and accidents | 16.1% |
  | Politics and elections | 15.3% |
  | International relations | 10.4% |
  | **Business and economy** | **4.5%** |
  | Health and environment | 2.3% |

  Cutting to the topical categories would change length and relevance at once
  and neither effect could be read off the result, so only length was cut.
  Whether the rest earn their tokens is a separate arm to run against this one.

  That Business and economy is 4.5% is a fact about the source rather than a
  knob — Wikipedia's Current Events portal barely covers economics, so a corpus
  sized for Michigan sentiment carries roughly five hundred tokens that mention
  the economy at all.
- **Fixed, not retrieved.** The corpus does not depend on the question. There
  is no query, no relevance filter, no per-topic retrieval. Two rounds locking
  at the same instant get byte-identical text. This is what makes the condition
  auditable: with live search, every model reads something different and nobody
  can reconstruct afterwards what any of them read.

### 3.1 The archive

`news/days/<date>.json`, one file per calendar day, **keyed by revision rather
than by lock**. Each file holds the page's full revision index, plus the parsed
items of every revision that any lock in the following sixteen days resolves
to, pooled so that near-identical successive revisions are stored once.

Why by revision: measured on five sample days, a Current Events page carries
40–82 revisions in total, but the sixteen locks that can ever read it resolve to
only **2–5** of them, and the page stops changing 2–6 days after its date.
Keying by lock stores up to sixteen copies of the same text and is useless to
a different set of locks — adding a series would refetch the same days again.

The archive grid stays at sixteen days even though the digest window is now
seven. It is what a *refetch* would cost rather than what a prompt costs, the
589 days already committed were collected against it, and narrowing it would
save nothing already spent while silently stopping a widened window from being
answerable offline.

Each file records `fetched_at` and **answers only asofs earlier than it**. An
index fetched at time T cannot know about edits after T, so serving a later
asof would silently return a stale revision. Days close to the present are
therefore served from the network until their window closes, and from disk
forever after.

Current state: **589 days, 2025-01-01 to 2026-08-12, 8.2 MB, 2417 revisions**.
All 575 lock dates in that span build offline with zero missing days.

```bash
python tools/archive_news.py                      # plan only, fetches nothing
python tools/archive_news.py --execute            # the pass itself, ~2 requests/day
```

Re-runnable and resumable; it knows which days it has not finished. The most
recent ~16 days are always unfinished by construction (their locks have not
happened), so re-run periodically to close them.

### 3.2 In the backtest

`ssa/model_backtest.py` builds the same corpus at each historical round's
`lock_at`, which is what makes the condition safe there: the pages are read as
they stood at that instant, not as they read today. Run it by naming the news
entrants:

```bash
python tools/run_model_backtest.py --entrants claude-opus-news,grok-news
```

Web search is refused in the backtest (`harness.assert_prospective`) because
the outcome was published months ago. News is not refused, and the reason is
exactly the revision-timestamp discipline above.

## 4. Turning a cell on

Nothing but the two season cells is on. `SSA_ELICITATION` names the cells that
also file:

```
SSA_ELICITATION=news              news x direct
SSA_ELICITATION=superfc           recent10 x superfc
SSA_ELICITATION=news+superfc      news x superfc
SSA_ELICITATION=news,superfc      two separate cells
SSA_ELICITATION=1                 every non-season cell
                                  (unset) none
```

A bare axis name pairs with the other axis's default, which is exactly what it
meant before the axes were separated — a workflow variable set earlier keeps
working. `+` composes across the axes. An unknown name raises rather than
silently filing nothing.

In CI it comes from the repository variable of the same name, so **merging the
code for a cell never starts paying for it**.

The costs are two orders of magnitude apart, which is the whole reason the
switch takes a list. From `tools/estimate_arms.py`, for a full season across
all 15 active models:

| arm | calls | $ / season |
|---|---|---|
| `superfc` | 195 | 1.30 |
| `news` | 195 | 3.55 |
| `persona` | 31,680 | 39.34 |

`news` is `estimate_arms.py`'s figure rescaled from the 13k-token corpus it
assumes to the ~5k one now sent; the tool's own constant is due an update.

`persona` is one call per simulated respondent per round — 24 quota cells × 8
replicates × 11 rounds with a survey instrument. That is also why the
unimplemented `news × persona` cell is not merely "the next one to add": the
same 5k-token corpus would ride on all 31,680 calls, pricing that one cell at
about **$446 a season** before any prompt caching or batch discount. At the old
14-day corpus it was $993, so shortening the window nearly halved the most
expensive cell in the design — which is a reason to run the length ablation
before building that cell, not after.

Run `tools/estimate_arms.py` before turning anything on. It calls nothing.
