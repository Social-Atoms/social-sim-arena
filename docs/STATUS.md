# Social Simulation Arena — full status

Read out of the repository on **2026-08-12 08:10 UTC**, against `origin/main` at
`f739aca`. Nothing here is from memory.

> **Most urgent thing on this page:** `umich-2026-08-prelim` locks today at
> **14:00 UTC — about 6 hours from now**. All 30 model entrants and 4 baselines
> have filed. It releases 08-14 and will be the **first round this benchmark
> ever resolves**. Everything about resolution is untested against a real
> release. See §5.

---

## 1. Tasks — what the arena asks

### 1.1 The unit of work

```
source  →  series  →  round  →  (one number, locked 48h before release)
```

A **task** here is a *series* (a tracker we can resolve). A **round** is one
scheduled release of that series. There are no subtasks in production yet: every
round asks for a single national number.

### 1.2 Sources: 4, of which 3 resolve

| # | Source | Arrives as | Series | Rounds | State |
|---|---|---|---|---|---|
| 1 | Silver Bulletin approval DB | Google Sheet CSV | 4 | 4 | working |
| 2 | Silver Bulletin generic ballot | Google Sheet CSV | 1 (+1 derived) | 3 | working |
| 3 | U. Michigan Surveys of Consumers | `tbmics.csv`, FRED fallback | 1 | 3 | working, flaky host |
| 4 | Civiqs daily trackers | HTML-embedded JSON + `civiqs/` archive | 2 | 2 | working, see §9 |
| 5 | **YouGov crosstab workbook** (new, PR #3) | keyless XLSX | 0 | 0 | **extractor works, not wired** |

### 1.3 Series registered (6, plus 2 Civiqs — see §9)

| series | source | rounds | has survey instrument |
|---|---|---|---|
| `umich_sentiment` | umich | 3 | yes (5-item ICS) |
| `yougov_approval` | sb_approval | 2 | yes |
| `mc_approval` | sb_approval | 2 | yes |
| `yougov_generic_margin` | sb_generic | 2 | yes |
| `yougov_econ_approval` | sb_approval | **0** | yes |
| `yougov_immig_approval` | sb_approval | **0** | yes |
| `civiqs_net_approval` | civiqs | 2 | yes |
| `civiqs_net_approval_rep` | civiqs | **0** | no (§9) |
| `generic_ballot_margin` | derived in `refresh` | 1 | no |

Three registered series carry no rounds. They widen the backtest only — and
note that widening the backtest is not free once a model runs it (§9).

### 1.4 The 13 rounds, all currently `open`

| lock (UTC) | release | round | series | scoreable |
|---|---|---|---|---|
| **08-12** | 08-14 | `umich-2026-08-prelim` | umich_sentiment | ✅ |
| 08-12 | 08-14 | `civiqs-2026-w33-approval` | civiqs_net_approval | ✅ (§9) |
| 08-15 | 08-17 | `mc-2026-w34-approval` | mc_approval | ✅ |
| 08-16 | 08-18 | `yougov-2026-w34-approval` | yougov_approval | ✅ |
| 08-16 | 08-18 | `yougov-2026-w34-generic` | yougov_generic_margin | ✅ |
| 08-19 | 08-21 | `civiqs-2026-w34-approval` | civiqs_net_approval | ✅ (§9) |
| 08-22 | 08-24 | `mc-2026-w35-approval` | mc_approval | ✅ |
| 08-23 | 08-25 | `yougov-2026-w35-approval` | yougov_approval | ✅ |
| 08-23 | 08-25 | `yougov-2026-w35-generic` | yougov_generic_margin | ✅ |
| 08-26 | 08-28 | `umich-2026-08-final` | umich_sentiment | ✅ |
| 09-09 | 09-11 | `umich-2026-09-prelim` | umich_sentiment | ✅ |
| 10-30 | 12-01 | `midterm-2026-house-margin` | generic_ballot_margin | ✅ |
| 10-30 | 12-01 | `midterm-2026-house-seats` | house_seats | ❌ |

**12 of 13 scoreable** (10 before the Civiqs adapter landed; see §9). **0 resolved so far** (nothing has released yet).

### 1.5 Subtasks — available but not in production

| kind | how many | fetch needed? | blocked on |
|---|---|---|---|
| issue × intensity × population cells (SB file) | 16 exist, **4 registered** | no, already downloaded | one registry row each |
| Michigan sub-indices (ICC, ICE) | +2 | no | one registry row each |
| generic ballot LV screen | +1 | no | one registry row |
| **YouGov demographic crosstabs** | **16 cells × 81 waves** | already works | schema field + round defs + resolution branch |

Crosstab cells (`ssa/adapters/yougov_xtab.py`, verified live 2026-08-12,
81 waves 2025-01-28 → 2026-08-10):

- party: Democrat, Independent, Republican
- age: Under 30, 30-44, 45-64, 65+
- race: White, Black, Hispanic
- gender: Male, Female
- education: HS or less, Some college, College grad, Postgrad
- (`Other` excluded by name: base 69, noise 4.8pts)

**Caveat measured today: only 7 of 17 series carry signal above weekly noise.**
For Republican, all three older age bands, White, Male and all four education
bands, every point of week-to-week movement is measurement noise over 81 waves.
Crosstab rounds should be **monthly against a 4-wave average**, which roughly
halves the noise, not weekly against a single wave.

---

## 2. Preregistered models

17 entered, **15 active** (kimi and minimax are configured but their gateway
namespace is not activated). All six provider keys are configured.

| model key | provider id | api | training cutoff | conf. |
|---|---|---|---|---|
| `gpt-5.6-luna` | gpt-5.6-luna | openai | 2026-02-16 | declared |
| `gpt-5.6-sol` | gpt-5.6-sol | openai | 2026-02-16 | declared |
| `gpt-5.6-terra` | gpt-5.6-terra | openai | 2026-02-16 | declared |
| `claude-opus` | claude-opus-4-8 | anthropic | 2026-01-01 | declared |
| `claude-opus-5` | claude-opus-5 | anthropic | 2026-05-01 | declared |
| `claude-sonnet` | claude-sonnet-5 | anthropic | 2026-01-01 | declared |
| `claude-fable` | claude-fable-5 | anthropic | 2026-01-01 | declared |
| `gemini-pro` | gemini-3.1-pro-preview | gemini | 2026-02-13 | reported |
| `gemini-flash` | gemini-3.6-flash | gemini | 2026-07-21 | reported |
| `grok` | grok-4.5 | openai | 2026-02-01 | declared |
| `qwen-3.7` | qwen3.7-max-2026-05-20 | openai | 2026-05-20 | reported |
| `qwen-3.8` | qwen3.8-max | openai | 2026-06-01 | **unknown** |
| `deepseek-pro` | deepseek-v4-pro | openai | 2025-05-01 | reported |
| `deepseek-flash` | deepseek-v4-flash | openai | 2026-07-31 | reported |
| `glm` | glm-5.2 | openai | 2026-03-01 | reported |
| `kimi` | kimi/kimi-k3 | openai | 2026-07-13 | reported · **not activated** |
| `minimax` | MiniMax/MiniMax-M3 | openai | 2026-06-01 | **unknown** · **not activated** |

Cutoffs live in `ssa/cutoffs.py`, keyed by **entrant, not model id** — changing
a model without revisiting its cutoff silently dates the window from the old
model's boundary. `MARGIN_DAYS = 30` pushes every window past the stated date.

### Baselines and reference forecasters (5 + 1)

`persistence` (the reference, skill = 0 by definition), `trend`, `ewma`,
`climatology`, `crowd` (equal-weight pool of all submissions), plus
`human-crowd`.

---

## 3. Harness — how a model is asked

### 3.1 Information conditions (live now)

| condition | entrant suffix | what it sees |
|---|---|---|
| `recent10` | *(none)* | question, unit, methodology, cadence, release date, **last 10 releases** |
| `none` | `-zeroshot` | the same minus all history |

**15 models × 2 conditions = 30 live entrants.** Both are scored as separate
entrants because they answer different questions.

### 3.2 Elicitation conditions (built, **uncommitted**, off by default)

| condition | suffix | what changes |
|---|---|---|
| `persona` | `-persona` | not asked to forecast at all — answers the real survey instrument as 192 weighted synthetic respondents; the pollster's arithmetic makes the number |
| `superfc` | `-superfc` | same data, elicited through the superforecaster protocol (outside view → decomposition → pre-mortem → calibration) |
| `web` | `-web` | same prompt + provider-hosted search; **live rounds only**, refused at backtest plan time |

Currently wired for 3 models (`claude-opus`, `gpt-5.6-terra`, `gemini-pro`) =
9 entrants. Gated behind `SSA_ELICITATION=1`; without it nothing runs and
nothing bills. Full-season cost estimate: **$8.24** (`tools/estimate_arms.py`).

### 3.3 Harness facts that matter

- **Max reasoning effort everywhere**, and the parameter is not portable:
  OpenAI `reasoning_effort: xhigh` (GPT-5.6 rejects `max`); Anthropic
  `thinking:{type:adaptive}` + `output_config:{effort:max}`; xAI tops out at
  `high`; the gateway models send nothing.
- **Temperature is never set.** Reproducibility comes from the committed raw
  replies, not from re-running.
- **Anthropic calls are streamed** — at max effort one reply exceeds ten
  minutes, and a buffered request that idles that long gets its socket closed.
- **Timeouts** are `(connect 15s, read 600s)`.
- **Cost control is the input hash.** Every filed forecast carries
  `in=<12 hex>` = hash of (model @ endpoint, exact prompt). A round costs one
  call per model per *new observation*, not one per 6-hourly refresh.
- **No mocks.** A failed call raises and the round goes unfiled; it is never
  replaced by a placeholder.

---

## 4. Results so far

### 4.1 Backtest — complete, committed, reproducible for free

2,887 calls · 339 releases in window · **22 answered by every entrant** ·
window 2025-05-31 → 2026-07-23 · 3 failures (claude-sonnet), excluded not mocked.

`python tools/run_model_backtest.py --rescore` regenerates the table from the
committed cache without calling anything.

**Headline, on the 22 matched releases.** The site's headline metric is now
**arena score**, not skill: 0 is copying the last release, 100 is the oracle —
a perfect forecast whose only remaining error is the survey's own sampling
noise, which nobody can predict. Both are shown because the backtest JSON
stores skill and the pages render arena.

| entrant | skill | arena score |
|---|---|---|
| claude-opus-5 | +30.6% | **50.3** |
| **ewma (baseline)** | +30.4% | **49.9** |
| gpt-5.6-terra | +28.5% | 46.8 |
| claude-opus | +28.3% | 46.5 |
| grok | +28.2% | 46.4 |
| glm | +27.5% | 45.2 |
| climatology (baseline) | +26.5% | 43.5 |
| trend (baseline) | +23.7% | 39.0 |
| deepseek-pro | +18.3% | 30.0 |
| persistence (reference) | 0.0% | 0.0 |
| claude-opus-5-zeroshot | −0.4% | −0.6 |
| … other zero-shot arms … | −24% … −160% | −39 … **−263** |

Three things to say plainly:

1. **The best entrant captures about half the attainable accuracy (50 of 100),
   and a moving average captures the same half.** One model of thirteen beats
   `ewma`, by 0.4 arena points over 22 releases. That is noise, not a result.
2. **`climatology` — take the long-run average — beats five frontier models.**
3. **Every zero-shot arm is at or below persistence**, down to −263. This is
   both the ablation and the cleanest contamination evidence: without the ten
   numbers we hand them, the models are far worse than copying the last value.
   Their skill comes from the data in the prompt, not from knowing the public.

> **Implementation note worth fixing.** `arena_score` and `noise_floor` exist in
> `ssa/scoring.py`, but nothing in the pipeline calls them — `site/index.html`
> re-implements the estimator in JavaScript (`noiseFloorJS`, `window.__oracleCrps`)
> and computes the published metric client-side. Two implementations of one
> estimator will drift. The headline number should be computed once, in
> `refresh`, and published in `data.json`.

### 4.2 Live season

341 forecast files committed across 10 rounds (34 per round = 30 models + 4
baselines). **0 resolved.** The leaderboard is legitimately empty until 08-14.

---

## 5. Risk register — what is untested

| # | Risk | Status |
|---|---|---|
| 1 | **Resolution has never run against a real release.** First is 08-14. | 10 unit tests pass; zero real executions |
| 2 | `midterm-2026-house-margin` fails ~1 call/refresh (claude-sonnet) | streaming fix landed 08-10; not yet observed under load |
| 3 | Michigan's own host 404s intermittently | FRED fallback exists but lags a month |
| 4 | 3 rounds can never resolve | decision still open, see §6 |
| 5 | Crosstab work exists but no round can use it | schema/rounds/resolve missing |

---

## 6. Open decisions — none of these are mine to make

1. **The 3 dead rounds** (`civiqs` ×2, `midterm-2026-house-seats`). The paper
   counts "five trackers" twice, gives Civiqs its own row in the targets table,
   and builds a methodology paragraph on it (the 329-of-335 back-dated-points
   observation, plus an "archived snapshot" mechanism that does not exist in
   code). So dropping Civiqs is not a code-only change. Best move: ask Paul
   where his eight months of Civiqs observations actually live.
2. **`main.tex` vs the code.** Five trackers, 323 releases, six models,
   "temperature 0", the archived-snapshot claim — all now disagree.
3. **Whether to run the elicitation arms**, and at what panel size.
4. **Whether to register the 12 free subgroup cells** (~half a day, no cost).
5. **Crosstab rounds**: monthly-against-4-wave-average is the only defensible
   cadence (§1.5).

---

## 7. What changed recently, by author

**Mine (ssa-bot), 08-09 → 08-11 — all on `main`:**

- per-tracker model curves; charts no longer draw 30 lines
- fixed `val() ?? 0` inventing data: `crowd` was drawn as a real curve at
  exactly 0% skill, and every model was drawn flat at 0 before its cutoff
  ("no skill" vs "not yet eligible")
- **`SSA_BASE_GATEWAY` rename had silently rerouted glm + both qwens** to the
  public DashScope host, invalidating their whole backtest cache
- Anthropic streaming; read timeout 120s → 600s
- **resolution was being skipped**: every workflow step had `if: always()`
  except `ssa.resolve --write`
- `--rescore`: rebuild the backtest table from cache, cannot call a provider
- lock snapshots can no longer be overwritten with an empty history
- **fixed a bare `else:` that made `ssa/refresh.py` unimportable** (from PR #4);
  the whole pipeline was dead until this landed

**Paul, 08-08 → 08-11:**

- **crosstab ground truth + profile scoring** (PR #3) — the substantial one
- `energy_score`, `profile_scores` (level vs structure), `noise_floor`,
  `arena_score` (0–100 scale)
- pitch deck, later moved to its own private repo `Social-Atoms/ssa-slides`
- structure pass: deleted the placeholder era
- site: URL↔tab binding, vendor icons, zero-shot labelling

**Repo hygiene, 08-11:** history rewritten to remove the one commit/blob
mentioning the venue; four merged branches deleted; **anyone with a clone must
`git fetch && git reset --hard origin/main`.**

---

## 8. Uncommitted on my machine

```
M  ssa/harness.py        persona/superfc/web conditions, search tools, guard
M  ssa/model_backtest.py per-series trajectories, backtest web refusal
M  ssa/refresh.py        SSA_ELICITATION gate
M  ssa/series.py         survey instruments for 5 series
?  ssa/personas.py       24 quota cells × 8 = 192 respondents, aggregators
?  tests/test_personas.py    16 tests
?  tools/estimate_arms.py    cost/accuracy table, calls nothing
?  entrants/*-persona|superfc|web.json   9 records
?  docs/                 this file + data-and-resolution.md
```

Five test suites pass. Nothing has been run against a provider from this work,
so **nothing has been billed**.

---

## 9. Civiqs — wired up, and why it needed an archive first

Added 2026-08-12. Supersedes the "dead" row in §1.2 and the two ❌ in §1.4.

### What Civiqs is

Civiqs publishes ~24 national trackers of **registered voters** — Trump
approval and favourability, the economy, inflation, abortion, guns, right
track/wrong track, the ACA, four separate AI-attitude questions — each as a
daily series back to 2025-01-20, filterable by age, education, gender, party,
race and home state. There is no API. The numbers are the Remix loader payload
embedded in the page HTML, under the route key `routes/_app.results_.$question`;
`ssa/adapters/civiqs.py` brace-scans it out and parses it. Subgroup filters are
query parameters keyed on the demographic's **label** (`?party=Republican`),
not its predictor id (`?party_3=Republican`, silently ignored — it returns the
national series under a subgroup's name, so a filtered fetch is verified rather
than trusted).

### Raw waves vs a modeled tracker — the distinction that shaped the design

Every other source in this repository publishes **survey waves**. YouGov and
Morning Consult field a sample, weight it once, publish a number, and that
number is true forever. Michigan does the same monthly. A wave carries real
publication noise: `scoring.noise_floor` measures 0.78 points on the YouGov
topline, which is the floor no forecaster can beat because the respondents have
not been interviewed yet when the round locks.

Civiqs is a **modeled tracker**: MRP over a rolling panel (123k cumulative
interviews), emitting a smoothed daily estimate. Two consequences, and both are
load-bearing:

1. **It revises its entire published history every night.** The number printed
   against 2025-06-06 today is not the number that was printed against
   2025-06-06 in June 2025. A round scored against the live page would find
   every point of its frozen history moved by resolution time, and
   `ssa.resolve` — which answers a round with the first `(date, value)` pair the
   freeze did not contain — would hand back a **revised old point** as this
   week's release. `tests/test_civiqs.py` demonstrates exactly that failure.
2. **There is almost no noise to beat.** Mean day-to-day change is 0.069
   points; `noise_floor` on the daily series returns m = 0.000. On the Friday
   series it is m = 0.10 against 0.78 for YouGov. Persistence is a very strong
   baseline here. That is a true fact about a smoothed target, not something to
   compensate for, and nothing in the pipeline tries to.

### The archive — `civiqs/`, committed

The paper already claims these rounds "resolve against the value displayed at
resolution time, preserved in our own archived snapshot". That archive did not
exist. It does now: one immutable JSON file per `(tracker, filters, fetch
date)` under `civiqs/`, carrying Civiqs's own provenance (`run_id`,
`job_finish_time`, `end_date`, `sample_size`) plus the series it saw. The first
snapshot of a key is written in full (29 KB, 569 days) and is the backfill base;
later ones keep a 60-day tail, which leaves sixty overlapping vintages of every
day and makes the nightly revisions measurable from the repository.

**A point dated `d` is the freshest reading available on `d`, according to the
earliest snapshot taken on or after `d`.** For a day we archived that is
literally the number on the dashboard when we looked; for a day before the
archive began it is that day's estimate as recorded in our first snapshot, which
is the only record that exists. Once written a point never changes, because
snapshots are only ever added forward in time.

The archive doubles as the fetch cache. Each page is ~2 MB and the host
rate-limits (bursts return `SSLError(SSLZeroReturnError)`), so requests are
serialized at 0.6 s spacing with backoff, and once a day's snapshot exists that
day costs no further requests. The six-hourly cron therefore fetches each
tracker **once a day**, not four times.

### Sampled on Fridays, deliberately

Both Civiqs rounds ask for a **Friday** dashboard value against a **Wednesday**
lock. `ssa.resolve` answers a round with the first observation after the freeze,
so on a *daily* series that is Thursday's number — a wrong resolution, which
`resolve.py`'s own docstring rates worse than a missing one. The registered
series are sampled on Fridays so the resolver's "next release" is the release
the round names. It also puts the persistence null a week back rather than a day
back, which is the only honest null for a week-ahead question. A round asking
about another weekday needs its own series id. **`ssa/resolve.py` was not
touched.**

### Series registered: 2

| series | filter | Friday points | rounds | survey instrument |
|---|---|---|---|---|
| `civiqs_net_approval` | — (national) | 81 | 2 | yes (`net_approve_share`) |
| `civiqs_net_approval_rep` | `party=Republican` | 81 | 0 | no — panel cannot be cut by party |

Both run 2025-01-24 → 2026-08-07 and are net points (approve − disapprove).
`civiqs-2026-w33-approval` and `civiqs-2026-w34-approval` now build baselines
(persistence −23.9 ± 1.5) and are **scoreable**, taking the season from 10/13 to
12/13.

Six candidate cells were measured on their Friday net series over 81 weeks
before any were registered — `mad` is mean absolute week-over-week change,
`corr` is the correlation of weekly changes with the national series:

| cell | m (noise floor) | mad | range | corr(national) | registered |
|---|---|---|---|---|---|
| national | 0.102 | 0.578 | 18.6 | 1.000 | ✅ |
| `party=Republican` | 0.000 | 0.490 | 20.2 | **0.752** | ✅ |
| `party=Democrat` | 0.019 | **0.092** | 3.3 | 0.810 | ❌ floor-bound |
| `party=Independent` | 0.367 | 1.359 | 33.7 | **0.968** | ❌ echoes national |
| `age=18-34` | 0.104 | 0.604 | 26.3 | **0.978** | ❌ echoes national |
| `age=65+` | 0.189 | 0.517 | 9.6 | 0.877 | ❌ second cut, weak case |

Every cell clears its own noise floor trivially — `m` is near zero everywhere,
because this is a smoother's output — so "signal above noise" does not
discriminate here the way it does on real waves. Two other tests do. Democrats
sit at 1.5% approve with 3.3 points of range in nineteen months and 0.09 points
of weekly movement: a perfect forecast beats persistence by under a tenth of a
point, so there is no question there. Independents and under-35s move with the
national number at 0.97+, so registering them scores one quantity twice and
doubles its weight in every average — the same reason `Adults` is absent from
the YouGov cells. Republicans are the exception: real amplitude and the *lowest*
correlation with the national line of the six, which makes them the one cut that
tests whether a model has the coalition rather than just the level.

`civiqs_approval` (percent approve) was also considered and **rejected**: its
weekly changes correlate with net at 0.992 and it regresses onto net as
`net ≈ 1.81 × approve + c` with a 0.20-point residual sd. It is the same
question in different units.

### Consequences the maintainer has to decide on

- **The LLM backtest bills more now.** `tools/run_model_backtest.py` takes its
  target list straight from `series_registry.SERIES`, so these two series enter
  the billed backtest automatically and there is no `--series` flag to exclude
  them. Roughly ~30–40 post-cutoff Fridays × 2 series × entrants of additional
  calls. **Decide before the next `--execute`.**
- **`civiqs/` must be committed**, and the archive only starts 2026-08-12.
  Everything before that is backfilled from the first snapshot and therefore
  carries whatever revisions Civiqs had already applied by then. That boundary
  is knowable from the directory listing and from `civiqs.archive_start()`.
- **`ssa/personas.py` gained one aggregator**, `net_approve_share`, because a
  net is a difference rather than a share and the persona arm had no way to
  express one. Without it the Civiqs rounds could not run that arm at all.
- **A refresh has to run before 22:00 UTC today** for
  `civiqs-2026-w33-approval` to get a lock snapshot; the 12:17 and 18:17 crons
  both qualify.
