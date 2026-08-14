# Conditions, entrant ids, and the news corpus

Written 2026-08-14, against `main` at `dc245b5`. Every count in here was read
out of the live files.

An *entrant* in this arena is not a model. It is a model **plus a condition** —
what it was shown, and how it was asked. Two entrants can share weights and sit
on different leaderboard rows, and that is the point: the arena exists to
measure the conditions, not only the models.

---

## 1. The grid

Two axes.

**Information** — what the model is shown:

| id | shown |
|---|---|
| `recent10` | the last ten releases of the series, the same history the nulls read |
| `none` | the question and nothing else |
| `news` | `recent10`, plus a fixed news corpus frozen at the lock |
| `web` | `recent10`, plus the vendor's live search tool |

**Elicitation** — how it is asked, holding the information fixed:

| id | asked |
|---|---|
| direct | "give a mean and an sd" |
| `superfc` | the human forecasting protocol: outside view, decomposition, pre-mortem |
| `persona` | not asked to forecast at all — answers the real survey instrument as each of 192 weighted respondents, and the pollster's arithmetic makes the number |

Twelve cells. **Six are implemented, and they form a cross rather than a
filled grid:**

|  | direct | `persona` | `superfc` |
|---|---|---|---|
| `none` | ✅ | — | — |
| `recent10` | ✅ | ✅ | ✅ |
| `news` | ✅ | — | — |
| `web` | ✅ *(not in the season)* | — | — |

That shape is deliberate rather than unfinished: every implemented cell differs
from `recent10 × direct` in exactly one thing, so any difference is
attributable. Filling the rest is a cost decision, not a code decision, and the
costs are wildly uneven — see §4.

## 2. Entrant ids

An entrant id is the model key, then the condition as a suffix. The **default
condition on each axis is elided**, so the plain model name is `recent10 ×
direct`:

| cell | entrant id |
|---|---|
| `recent10` × direct | `claude-opus` |
| `none` × direct | `claude-opus-zeroshot` |
| `news` × direct | `claude-opus-news` |
| `web` × direct | `claude-opus-web` |
| `recent10` × `persona` | `claude-opus-persona` |
| `recent10` × `superfc` | `claude-opus-superfc` |

`harness.resolve()` reverses this by longest-suffix-first match, and
`harness.VARIANT_SUFFIX` is the table. The id is load-bearing in three places
at once — the forecast path `forecasts/<round_id>/<entrant>.json`, the entrant
record `entrants/<entrant_id>.json`, and the leaderboard row — and
`tools/validate_submission.py` checks all three agree.

### 2.1 When a second axis is added

The suffix table is currently flat: one suffix per condition, six entries. It
can express any single cell of the cross above and **cannot express a
combination** — there is no `news × superfc` id today.

The extension, when a combination is first needed, is to compose the two
suffixes with information first:

```
<model>[-<information, recent10 elided>][-<elicitation, direct elided>]

  claude-opus-news-superfc      news x superfc
  claude-opus-zeroshot-persona  none x persona
```

Every id in the table above survives this unchanged, because eliding the
defaults is what makes `claude-opus` and `claude-opus-news` mean what they
already mean. The change is `VARIANT_SUFFIX` and `resolve()`; nothing on disk
has to be renamed. **Doing it before a combination exists is free; doing it
after means rewriting committed forecasts**, so it should land with the first
combination and not later.

## 3. The news corpus

The `news` condition gives every entrant in a round the **same** corpus, built
from the Wikipedia Current Events portal as those pages stood **at the round's
lock**. Not a per-model search: one text, archived, reproducible.

- **Window.** The fourteen days before the lock, up to but not including the
  lock day — a page for the lock day is mid-write and would differ between an
  entrant filed at 09:00 and one filed at 13:00.
- **Size.** Measured over all 575 possible lock dates in the archive: median
  45,047 characters, about 11.3k tokens; range 24,722 to 74,176.
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
40–82 revisions in total, but the fourteen locks that ever read it resolve to
only **2–5** of them, and the page stops changing 2–6 days after its date.
Keying by lock stores up to fourteen copies of the same text and is useless to
a different set of locks — adding a series would refetch the same days again.

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

## 4. Turning an arm on

Nothing is on by default. `SSA_ELICITATION` names the arms that file:

```
SSA_ELICITATION=news             just the news corpus
SSA_ELICITATION=news,superfc     two of them
SSA_ELICITATION=1                every arm
                                 (unset) none
```

In CI it comes from the repository variable of the same name, so **merging the
code for an arm never starts paying for it**. An unknown name raises rather
than silently filing nothing.

The costs are two orders of magnitude apart, which is the whole reason the
switch takes a list. From `tools/estimate_arms.py`, for a full season across
all 15 active models:

| arm | calls | $ / season |
|---|---|---|
| `superfc` | 195 | 1.30 |
| `news` | 195 | 7.77 |
| `persona` | 31,680 | 39.34 |

`persona` is one call per simulated respondent per round — 24 quota cells × 8
replicates × 11 rounds with a survey instrument. That is also why the
unimplemented `news × persona` cell is not merely "the next one to add": the
same 11.3k-token corpus would ride on all 31,680 calls, which prices that one
cell near a thousand dollars a season before any prompt caching or batch
discount.

Run `tools/estimate_arms.py` before turning anything on. It calls nothing.
