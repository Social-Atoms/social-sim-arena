# Data sources, question resolution, and what was just built

Written 2026-08-11, **revised the same day** against `main` at `7354222` after
PR #3 (`crosstab-scoring`) and PR #4 (`slides-and-structure`) merged. Every
count was read out of the live files, not from memory.

> **Revision note.** The first version of this document said we had no
> demographic crosstabs at all. That was true of the ingestion path I audited
> and is **no longer true**: PR #3 landed a working YouGov crosstab adapter and
> a joint scoring layer. §2.2 has been rewritten. The conclusion changed from
> "we cannot do subgroups" to "we can extract them but nothing consumes them
> yet, and most cells cannot be scored weekly" — see §2.2 and §2.2.1.

---

## Part 1 — What we actually have

### 1.1 Four sources, and only three of them work

| # | Source | How it arrives | Registered series | Season rounds | Status |
|---|--------|----------------|-------------------|---------------|--------|
| 1 | **Silver Bulletin approval database** | published Google Sheet CSV | 4 | 4 | working |
| 2 | **Silver Bulletin generic-ballot database** | published Google Sheet CSV | 1 (+1 derived) | 3 | working |
| 3 | **University of Michigan** Surveys of Consumers | `tbmics.csv`, FRED as fallback | 1 | 3 | working, flaky upstream |
| 4 | **Civiqs** daily approval | *nothing* — JS dashboard only | 0 | 2 | **cannot resolve** |

Plus one round (`midterm-2026-house-seats`) that has no series at all and
resolves from certified election results by hand.

**So: 13 rounds, of which 3 can never be scored automatically** —
`civiqs-2026-w33-approval`, `civiqs-2026-w34-approval`,
`midterm-2026-house-seats`. They have zero forecasts filed and no machine-
readable ground truth. This decision is still open; see §3.

### 1.2 The six registered series, and the rounds on them

```
series                   source          rounds  survey instrument
umich_sentiment          umich                3  yes (5-item ICS)
yougov_approval          sb_approval          2  yes
mc_approval              sb_approval          2  yes
yougov_generic_margin    sb_generic           2  yes
yougov_econ_approval     sb_approval          0  yes
yougov_immig_approval    sb_approval          0  yes
```

Two registered series carry **no rounds at all**. They exist because they widen
the backtest (159 extra observations), but nothing in the live season asks
about them. That is free question surface sitting unused.

The 13 rounds in date order:

| lock | round | series |
|------|-------|--------|
| 08-12 | `umich-2026-08-prelim` | umich_sentiment |
| 08-12 | `civiqs-2026-w33-approval` | *unscoreable* |
| 08-15 | `mc-2026-w34-approval` | mc_approval |
| 08-16 | `yougov-2026-w34-approval` | yougov_approval |
| 08-16 | `yougov-2026-w34-generic` | yougov_generic_margin |
| 08-19 | `civiqs-2026-w34-approval` | *unscoreable* |
| 08-22 | `mc-2026-w35-approval` | mc_approval |
| 08-23 | `yougov-2026-w35-approval` | yougov_approval |
| 08-23 | `yougov-2026-w35-generic` | yougov_generic_margin |
| 08-26 | `umich-2026-08-final` | umich_sentiment |
| 09-09 | `umich-2026-09-prelim` | umich_sentiment |
| 10-30 | `midterm-2026-house-margin` | generic_ballot_margin |
| 10-30 | `midterm-2026-house-seats` | *unscoreable* |

`resolutions/resolved.json` currently holds **0** entries. The first release
lands **2026-08-14**.

### 1.3 Publication calendars (issue #64)

The Silver Bulletin sheet dates a poll by its field window, so the generator
refused all 21 of its series as `gate: schedule`. Its `createddate` column is
the day a poll entered the sheet, which is the day the arena first sees it: of
the polls entered since the archive began on 2026-08-18, all but two appear in
that day's or the next day's vintage, and every Economist wave in that day's.
Field end and entry day of each house's last eight waves, 2026-09-02 vintage:

| house | field end → entered |
|---|---|
| Economist/YouGov | 07-13→07-14 Tue, 07-20→07-21 Tue, 07-27→07-28 Tue, 08-03→08-04 Tue, 08-10→08-11 Tue, 08-17→08-18 Tue, 08-24→08-25 Tue, 08-31→09-01 Tue |
| Morning Consult | 07-13→07-14 Tue, 07-19→07-21 Tue, 07-26→07-29 Wed, 08-02→08-06 Thu, 08-10→08-25 Tue, 08-17→08-18 Tue, 08-24→09-01 Tue, 08-30→09-01 Tue |
| Ipsos | 06-15→06-15 Mon, 06-22→06-24 Wed, 07-13→07-16 Thu, 07-26→07-28 Tue, 08-03→08-04 Tue, 08-17→08-18 Tue, 08-24→08-25 Tue, 08-31→09-01 Tue |
| Rasmussen | 08-20→08-21 Fri, 08-21→08-24 Mon, 08-24→08-25 Tue, 08-25→08-26 Wed, 08-26→08-27 Thu, 08-27→08-28 Fri, 08-28→08-31 Mon, 08-31→09-01 Tue |
| Navigator | 03-16→03-18 Wed, 04-06→04-08 Wed, 04-27→04-29 Wed, 05-18→05-20 Wed, 06-08→06-15 Mon, 06-30→07-07 Tue, 07-27→07-29 Wed, 08-24→08-26 Wed |
| RMG | 06-23→06-26 Fri, 07-14→07-17 Fri, 07-21→07-24 Fri, 07-29→07-31 Fri, 08-04→08-11 Tue, 08-11→08-18 Tue, 08-17→08-21 Fri, 08-25→08-28 Fri |

Economist/YouGov is scheduled where its item runs weekly. Its window usually
opens Friday and closes Monday, and the wave enters the sheet the next day, 84 of
84 since 2025-01-28: Tuesday in 69, Wednesday in 15, each of those after a
window that closed Tuesday (13 weeks of early 2025, Labor Day 2025, Memorial
Day 2026). `PUBLICATION` in
`ssa/series.py` records entry Tuesday and release Wednesday 14:00Z: entrants
file by the Monday 12:00Z batch deadline while the wave is still in the field,
the wave enters Tuesday, and the round resolves on it Wednesday. A wave that
enters late resolves the round late; nothing moves at the deadline. The twelve
hand-written `yougov-2026-w34..w39` rounds release Tuesday 14:00Z with a
Sunday lock; under the batch deadline that shape resolves on the wave entered
the day after the previous Monday, which is why the calendar records
Wednesday instead. On every
run the generator re-checks the calendar against the newest archived sheet and
refuses when the last eight waves show more than one slip: an entry off
Tuesday, or a gap between entries that is not within eight days. The second
kind is what refuses the issue-specific YouGov items the pollster asks only
some weeks.

Entry day is checked against the pollster, not only against the sheet.
Each row of the sheet carries the wave's topline PDF in its `url` column. On
2026-09-04 those eight URLs were fetched by hand, one HEAD and one GET each;
nothing in this repository fetches them, and nothing should. All eight
returned 200. `Last-Modified` is the CDN's own record of when the file went
up; `/CreationDate` is the stamp the tables were written with, in UTC here:

| entered | Last-Modified | /CreationDate |
|---|---|---|
| 07-14 Tue | Tue 07-14 13:00Z | Mon 07-13 22:35Z |
| 07-21 Tue | Tue 07-21 13:09Z | Tue 07-21 12:59Z |
| 07-28 Tue | Tue 07-28 13:00Z | Mon 07-27 23:25Z |
| 08-04 Tue | Tue 08-04 12:56Z | Mon 08-03 21:17Z |
| 08-11 Tue | Tue 08-11 13:02Z | Tue 08-11 12:58Z |
| 08-18 Tue | Fri 08-21 12:37Z | Wed 08-19 01:02Z |
| 08-25 Tue | Tue 08-25 13:11Z | Mon 08-24 23:28Z |
| 09-01 Tue | Tue 09-01 13:08Z | Mon 08-31 22:24Z |

Seven of the eight went up on the Tuesday the sheet entered them, within a
quarter hour of 13:00Z. The eighth is not a slip in the calendar but a
replaced file: the 08-18 wave's PDF was re-uploaded on the Friday, and its own
stamp is Tuesday evening in the pollster's time zone, 01:02Z on the Wednesday.
So the entry day is the publication day, on evidence that does not come from
the sheet.

This is also the check the lock depends on. The round locks Monday 14:00Z and
the wave is published the next day, so the answer cannot be public when
entrants file. The tightest of the eight is the 08-04 wave, whose tables were
written 08-03 21:17Z, seven hours after that round would have locked and
sixteen before it went up.

The other five houses cannot carry a locked round and stay refused: Morning
Consult's waves enter on mixed weekdays, weeks late and sometimes two at once;
Ipsos is ad hoc; Rasmussen is a daily tracker whose number is public on its
own site first; Navigator polls every three weeks or so; RMG entered a week
late in two of the last eight.

---

## Part 2 — Resolution: you are right, and the reason is specific

The current questions are all **one national topline per release**. That is as
coarse as this benchmark gets, and the granularity is limited by the ingestion
path, not by the pipeline.

### 2.1 What the source file does carry — free subtasks, available today

The Silver Bulletin approval file has a `subgroup` column with four *kinds* of
cut in it, and we use one value from it (`All polls`) plus two issue values.
Reading the file directly:

```
subgroup values: All polls 1289 · Voters 1082 · Strong 792 · Weak 792
                 Economy 510 · Immigration 447 · Trade 299 · Cost 270
                 Adults 400
population values: RV 2235 · A 2146 · LV 1479
```

Those are three different axes wearing one column name:

| axis | values | what it is |
|------|--------|------------|
| **issue** | Economy, Immigration, Trade, Cost | approval on a named policy area |
| **intensity** | Strong, Weak | strongly vs somewhat approve |
| **population** | Adults, Voters (+ the `population` column: A / RV / LV) | who was asked |

Crossing pollster × subgroup × population, the cells with enough history to be
worth registering — release counts read from `enddate`:

| pollster | subgroup | pop | releases | registered? |
|---|---|---|---|---|
| YouGov | All polls | A | 117 | ✅ `yougov_approval` |
| YouGov | Adults | A | 117 | ❌ |
| YouGov | Strong | A | 114 | ❌ |
| YouGov | Weak | A | 114 | ❌ |
| YouGov | Voters | RV | 83 | ❌ |
| YouGov | Economy | A | 82 | ✅ `yougov_econ_approval` |
| YouGov | Cost | A | 82 | ❌ |
| YouGov | Immigration | A | 75 | ✅ `yougov_immig_approval` |
| YouGov | Trade | A | 29 | ❌ |
| Morning Consult | All polls | RV | 80 | ✅ `mc_approval` |
| Morning Consult | Voters | RV | 80 | ❌ |
| Morning Consult | Economy | RV | 75 | ❌ |
| Morning Consult | Immigration | RV | 75 | ❌ |
| Morning Consult | Trade | RV | 71 | ❌ |
| Morning Consult | Strong | RV | 68 | ❌ |
| Morning Consult | Weak | RV | 68 | ❌ |

**We register 4 of 16 available cells.** Registering the rest is one row each in
`ssa/series.py` and costs no extra fetch — the data is already downloaded on
every refresh and thrown away.

Three of these are scientifically interesting rather than merely more:

- **Strong vs Weak** is an *intensity* question. "Does approval soften before it
  falls" is a real hypothesis, and a model that gets the topline right while
  getting the strong/weak split wrong has been caught doing something other
  than modelling people.
- **Adults vs Voters, same pollster, same wave** is the cleanest natural
  experiment in the file: identical fieldwork, different population, several
  points apart. A simulator that claims to represent a population should get
  the *gap* right, and that is a much sharper test than either level.
- **Issue approval vs overall approval** tests whether a model has one
  undifferentiated "Trump sentiment" or actually distinct policy attitudes.

### 2.2 Demographic crosstabs — the situation changed today

The Silver Bulletin aggregator CSV carries **no** demographic columns. I checked
them directly:

```
approval columns: adjusted_approve, adjusted_disapprove, adjusted_net, approve,
                  createddate, disapprove, enddate, influence, net, pollster,
                  population, president, samplesize, sponsors, startdate,
                  subgroup, timestamp, tracking, url, weight
demographic crosstab columns: NONE
```

**But PR #3 added a second ingestion path that does.** `ssa/adapters/yougov_xtab.py`
pulls the Economist/YouGov tracker's own workbook from a keyless endpoint, where
each sheet is a subgroup. I ran it live while writing this: **81 waves,
2025-01-28 to 2026-08-10**, 16 scored cells across five dimensions.

| dimension | cells |
|---|---|
| party | Democrat, Independent, Republican |
| age | Under 30, 30-44, 45-64, 65+ |
| race | White, Black, Hispanic |
| gender | Male, Female |
| education | HS or less, Some college, College grad, Postgrad |

`Other` is excluded by name, with the reason recorded in the adapter: median
weighted base 69, measurement noise 4.8 points against real weekly movement
under 1 point. Excluding it explicitly rather than silently carrying it is the
right call.

Alongside it, `ssa/scoring.py` gained a joint scoring layer:

- **`energy_score`** — the multivariate generalisation of CRPS. A crosstab round
  asks for a vector, and averaging per-cell CRPS would score the marginals and
  ignore the joint: two entrants with identical per-cell uncertainty, one
  believing subgroups move together and one believing they move independently,
  would be indistinguishable. On 80 waves those two constructions differ by 7%
  of energy score while their mean per-cell CRPS differs by 1%.
- **`profile_scores`** — decomposes into `level` (national mean) and
  `structure` (what is left after removing it). This is the RQ3 measurement:
  a model that reads the national mood off a headline but has no idea how
  approval decomposes scores well on level and badly on structure.
- **`noise_floor`** — measures a series' measurement noise from the lag-1
  autocovariance of its first differences, rather than computing it from base
  sizes. Measuring beats computing: panel overlap and weighting make the real
  wobble 0.78 points on the YouGov topline against 1.44 from the textbook
  standard error.
- **`arena_score`** — 0 to 100, where 0 is copying the last release and 100 is
  the noise ceiling no forecaster can beat. Correctly documented as
  season-level only; on a single wave it produced 2430 and −792 on adjacent
  cells.

This is careful work and the reasoning in it is sound.

#### 2.2.1 The problem it surfaces: most cells cannot be scored weekly

I ran `noise_floor` over all 81 waves for every cell. `m` is measurement noise,
`s` is real week-to-week movement, both in points:

| cell | noise m | signal s | forecastable weekly? |
|---|---|---|---|
| US Registered Voters | 0.78 | 0.47 | yes |
| Democrat | 1.05 | 0.96 | yes |
| Independent | 2.09 | 1.31 | yes |
| Black | 1.98 | 1.48 | yes |
| Under 30 | 2.81 | 1.16 | yes |
| Female | 1.18 | 0.73 | yes |
| Hispanic | 3.23 | 0.68 | yes |
| Republican | 1.95 | **0.00** | no |
| 30-44 / 45-64 / 65+ | 2.1–2.3 | **0.00** | no |
| White / Male | 1.2–1.4 | **0.00** | no |
| HS or less / Some college / College grad / Postgrad | 2.4–3.5 | **0.00** | no |

**7 of 17 series carry measurable weekly signal. The other 10 do not** — over
81 waves, every point of their week-to-week movement is measurement noise.

That is not a defect in the adapter; it is a fact about weekly subgroup polling
with bases of 200–400. It has a direct consequence for round design: **a weekly
round on "Postgrad approval" is a round on a coin flip.** Every entrant,
including a perfect one, is scored on noise, and `arena_score`'s ceiling
(`persistence_crps − noise/√π`) collapses toward zero, which is exactly the
regime where it returns nonsense.

The fix is cadence, not method. Averaging four consecutive waves — which
averages noise down rather than throwing data away — roughly halves it:

| cell | weekly noise | 4-wave-average noise |
|---|---|---|
| Democrat | 1.05 | 0.23 |
| 45-64 | 2.14 | 0.56 |
| College grad | 2.58 | 0.74 |
| Postgrad | 3.50 | 1.63 |

So the honest design is **monthly crosstab rounds against a 4-wave average**,
not weekly rounds against a single wave. (Note: subsampling every 4th wave
instead of averaging leaves ~20 points and makes `noise_floor` unstable — it
returned values like `inf` and `20.63`. Those are artifacts; do not cite them.)

#### 2.2.2 What is not wired up yet

The extraction and the scoring both exist and are tested. **Nothing consumes
them.** I checked:

- no module imports `yougov_xtab`; `refresh.py` does not build the series
- `questions/season0.json` has **no crosstab round** (still 13 rounds, none
  referencing a profile or subgroup)
- `schema/forecast.schema.json` has **no `profile` field**, so an entrant
  literally cannot submit one — `validate_submission.py` would reject it
- there is no resolution path: `ssa/resolve.py` resolves scalars against a
  series, and a profile is a vector

So the gap between here and a scoreable crosstab round is: a schema field, a
series builder, round definitions, and a resolution branch. The hard parts —
getting the data and knowing how to score a vector — are done.

### 2.3 The generic-ballot file is genuinely flat

```
subgroup: All polls (540, the only value)
population: RV 381 · LV 137 · A 22
```

One useful cut here: **RV vs LV**, the likely-voter screen. That is a real and
much-argued modelling choice, and 137 LV observations is enough to register.
Nothing else in this file subdivides.

### 2.4 Michigan is already finer than we use

The ICS headline is built from five named questions, and the survey also
publishes the two sub-indices (Current Conditions, Expectations) plus each
component. We forecast only the headline.

Registering **ICC and ICE as their own series** is nearly free and is a sharper
test than the headline: the headline can be right by luck when a rise in
expectations cancels a fall in current conditions, and the two sub-indices
catch exactly that. Note the persona arm already asks all five component
questions (§4.1), so it produces the components whether we score them or not.

### 2.5 Summary of available granularity

| level | scored today | available now, no new fetch | blocked on |
|---|---|---|---|
| national topline | 6 series | +10 (issue × intensity × population) | one registry row each |
| index components | 1 (ICS) | +2 (ICC, ICE) | one registry row each |
| likely-voter screen | — | +1 (generic ballot LV) | one registry row |
| **demographic crosstabs** | **none** | **16 cells × 81 waves, extractor works** | schema field, round defs, resolution branch |

The bottom row is the one that moved today. The data and the scoring maths are
done; what is missing is the plumbing between them and the round lifecycle.

---

## Part 3 — Still-open decisions (unchanged, listed so they are not lost)

0. **Wire up the crosstab path** (new, and now the highest-value item). The
   extractor and the joint scorer exist and are tested; no round can use them
   until the schema takes a `profile`, `refresh` builds the series, and
   `resolve` grows a vector branch. Design it monthly against a 4-wave average,
   not weekly — §2.2.1 shows 10 of 17 cells have no measurable weekly signal.

1. **The 3 unscoreable rounds.** Civiqs ×2 and house-seats. The paper counts
   "five trackers" twice and gives Civiqs its own row plus a whole methodology
   paragraph (the 329-of-335 back-dated-points observation, and an "archived
   snapshot" resolution mechanism that does not exist in code). Swapping Civiqs
   out is therefore not a code-only change. Best option remains: ask Paul where
   the eight months of Civiqs observations he cites actually live.
2. **`main.tex` vs the code.** Five trackers, 323 releases, six models,
   "temperature 0", the archived-snapshot claim — all now disagree with the
   implementation.
3. **Whether to run the new arms at all**, and at what panel size (§4.4).

---

## Part 4 — What I built since the last review (uncommitted, not pushed)

All of this is in the working tree only. Five test suites pass. Nothing has
been run against a provider, so **nothing has been billed**.

### 4.1 `persona` — silicon sampling as a scored condition

New file `ssa/personas.py`, plus survey instruments in `ssa/series.py`.

The model is **not asked to forecast**. It answers the tracker's *real survey
instrument* in character as each of 192 weighted synthetic respondents, and the
pollster's own arithmetic turns those answers into the number. The model never
sees the series, the release date, or the fact that a forecast is wanted.

Decisions worth checking:

- **Michigan is computed, not asked.** The ICS is a published formula over five
  specific questions, so personas answer those five and we reproduce
  `(Σ relative scores)/6.7558 + 2.0`. Asking a respondent for "the index" is
  asking them to guess a normalisation they cannot see. Verified against the
  formula's fixed points: all-neutral → 76.0, all-favourable → 150,
  all-unfavourable → 2.
- **Approval divides by everyone asked.** In the real data approve+disapprove
  sums to ~96–97, so there is a genuine "not sure" residual; dividing it away
  would inflate every simulated number by 3–4 points. (This also corrected a
  wrong sentence in the `yougov_approval` methodology text, which had been
  shown to models.)
- **Registered voters are reweighted, not filtered** — each cell carries a
  registration rate (0.52 for 18-29 no-degree up to 0.88 for 65+ graduates).
- **A panel that mostly refused is rejected, not published.** Below 75% of
  panel weight the forecast raises. A model that will not play certain personas
  produces a *biased* panel, not a small one, and publishing the survivors
  would hide precisely that.

**Known methodological weakness, stated in the module docstring:** cell weights
are the product of three independent marginals (party × age × education). Those
are correlated in reality, so the simulated electorate is slightly more
moderate than the real one. Fixing it needs CPS or ANES joint microdata.

### 4.2 `superfc` — the forecasting protocol as a condition

Same information as the default condition, elicited through the human
superforecasting process: outside view and base rate → decomposition →
pre-mortem → explicit calibration check. Isolates what the *process* is worth,
separately from the model. Test asserts both conditions show identical data, so
any score difference is elicitation and not information.

### 4.3 `web` — live search, prospective only

`harness.assert_prospective()` raises, **at backtest plan time**, before
anything bills. In a live round the answer does not exist at lock time, so
search cannot leak it; in the backtest it was published months ago and search
reads it. There is no prompt that prevents this and no way to audit afterwards
what was retrieved.

Also fixed while wiring this: Gemini takes `tools` at the body root, and we
were about to nest it under `generationConfig`, where it is accepted and
silently ignored — a "web" condition that never searched.

### 4.4 The cost/accuracy finding — read this before choosing to run

`tools/estimate_arms.py` (calls nothing):

```
per cell  panel   discretisation error (points)
       1     24     10.71
       2     48      7.57
       4     96      5.35
       8    192      3.79   <- default
      16    384      2.68
      32    768      1.89
```

**One respondent per cell cannot resolve these trackers.** A respondent answers
all-or-nothing while the group they represent answers a share; collapsing that
is worth ~10 points on its own, against trackers that move 1–2 points between
releases. Error falls as 1/√n, so each additional point of resolution costs 4×.

Full season, 3 models × 3 conditions: **$8.24**. A persona backtest over the 22
matched releases would be ~$10 per model.

**Honest caveat for the paper:** the persona arm's `sd` is computed by the
harness, not reported by the model. Its CRPS is therefore partly a property of
our calibration, unlike every other entrant. Once a season of persona residuals
exists, calibrate on those instead.

### 4.5 Safety

New conditions are **off unless `SSA_ELICITATION=1`**. Pushing this cannot start
spending on its own. Nine entrant records were generated and all 45 records
pass `tools/validate_submission.py`.

### 4.6 How persona sampling and the crosstab path fit together

Worth stating explicitly, because it changes what the persona arm is for.

Before PR #3, the persona arm could only ever be scored on the topline — the
one comparison where a moving average is already competitive, and where 24 or
192 simulated respondents lose to it on discretisation error alone. That made
it a hard sell.

With crosstabs, the persona arm has the one job nothing else can do: `ewma`
cannot tell you what under-30s think, and a topline forecaster has no opinion
about `structure` at all. `profile_scores` already splits `level` from
`structure`, which is exactly the split that separates "read the national mood
off a headline" from "has a model of a society".

Two things follow:

- The persona panel's weighting axes (party × age × education) should be
  reconciled with the scored cells (party, age, race, gender, education). Race
  and gender are scored but are not weighting axes in `ssa/personas.py`; they
  are assigned by position. To be scored on race cells the panel needs race as
  a real axis.
- The independence approximation in the panel weights matters more here. On a
  topline the party-education correlation partly averages out; on a per-cell
  profile it does not, and it will show up directly as `structure` error.

### 4.7 Also fixed since last review (already on `main`)

- **Streaming for Anthropic.** Raising the read timeout 120s → 600s fixed two of
  three chronically failing rounds; the third then returned `RemoteDisconnected`
  and once a real 600s timeout. One reply genuinely exceeds ten minutes, and a
  request that puts nothing on the wire for ten minutes gets its socket closed.
  Now streamed; thinking deltas are dropped so reasoning never reaches the
  forecast parser.
- **Resolution was being skipped.** Every workflow step carried `if: always()`
  except `ssa.resolve --write`. Since `ssa.refresh` exits non-zero when any one
  provider call fails — which it did all day — resolution never ran. Harmless
  while all rounds are open; from 08-14 it would have silently left the
  leaderboard empty.
