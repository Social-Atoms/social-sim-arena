# The persona arm: what it measures, what it currently does, and what it should do

Written 2026-08-14. The numbers in Part 1 come from one real run of
`tools/diagnose_persona.py`; all 192 raw replies are committed under
`diagnostics/`, so every count below can be recomputed without calling
anything. Part 2 is read off Ashokkumar, Hewitt, Ghezae & Willer, *Large
language models can predict the results of social science experiments*, Nature
(2026), doi 10.1038/s41586-026-10742-x.

The `persona` condition does not ask a model to forecast. It puts the real
survey instrument to each of 192 synthetic respondents in turn and lets the
pollster's arithmetic make the number. It is the method the silicon-sampling
literature describes and the one the industry sells, which is why it belongs on
a scoreboard next to a moving average — and why it has to be held to the same
evidentiary standard as everything else here.

---

## Part 1 — The diagnostic

**One model (`deepseek-flash`), one round (`civiqs-2026-w33-approval`), 192
calls, $0.02.** Reproduce with:

```bash
python tools/diagnose_persona.py            # plan only, calls nothing
python tools/diagnose_persona.py --rescore  # from the committed replies, free
```

### 1.1 Seventeen of twenty-four cells returned one answer eight times

| party | cells unanimous | answers given |
|---|---|---|
| Democrat | **8 / 8** | 64 × disapprove |
| Republican | **8 / 8** | 64 × approve |
| independent | 1 / 8 | 44 disapprove, 14 neither, 6 approve |

119 of 192 calls (62%) returned an answer already given by an earlier
respondent in the same cell. Within-cell entropy is **0.000 bits** for every
Democrat and every Republican cell; the whole-panel entropy is 1.291 bits.
Near-zero within and non-zero overall means the panel is deterministic per cell
and varies only between cells — a caricature of the axes, not a population.

This is not a determinism artifact. `ssa/harness.py` sets no `temperature`, no
`top_p` and no `seed`, so every call runs at the provider's default (1.0 for
most), and the eight replicates in a cell are eight *different* people —
gender, region and income vary by position. Eight distinct prompts at
temperature 1 still produced one answer.

### 1.2 The claimed precision is not the delivered precision

```
intra-cell ICC rho = 0.85    design effect = 6.94
nominal k = 8            ->  effective k = 1.15

panel_se as personas.resolution() claims it (k=8)   3.74 points
panel_se corrected for the observed agreement       9.85 points
panel_se the harness would report at k=1           10.58 points
```

`personas.panel_se()` is computed from the *weights* and never from the
answers, so the harness has no mechanism to notice this: it reports the k=8
number while delivering something near the k=1 number.

Two caveats, both stated rather than buried. The ICC counts genuine
between-cell difference as clustering, so 6.94 is an **upper bound** on the
design effect; the unambiguous facts are the within-cell mean square and the
17/24 count. And a second, independent understatement: the target here is a
*net*, so a respondent's score runs over [−1, +1] and its variance is up to 4×
a share's, while the published 3.74 is a share's error attached to a net's
target.

### 1.3 Unanimity is defensible for Democrats and not for Republicans

Against each subgroup's real answer distribution, the probability that eight
independent draws all agree:

| party | cells unanimous | expected | P(≥ observed) | real rates |
|---|---|---|---|---|
| Democrat | 8 | 6.27 | 0.14 | approve 2 / disapprove 97 / not sure 1 |
| Republican | 8 | 1.40 | **8.6 × 10⁻⁷** | approve 80 / disapprove 11 / neither 8 |
| independent | 1 | 0.58 | 0.45 | approve 26 / disapprove 72 / not sure 2 |

Real Democrats really are 97% disapprove, so eight-for-eight there is what the
population looks like. Republicans are 80/11/8 and the panel returned 64 out of
64. That one is a genuine collapse.

*(Republican rates are Civiqs's own party filter — same tracker, same three
options. Democrat and independent are the Economist/YouGov wave, a different
house, so read those two rows as indicative.)*

### 1.4 The topline is accidentally right

Against Civiqs as the dashboard read at the lock:

| | real | simulated | error |
|---|---|---|---|
| Republicans, % approve | 80.4 | **100.0** | **+19.6** |
| independents, % approve | 26.0 | 11.1 | −14.9 |
| Democrats, % approve | 2.0 | 0.0 | −2.0 |
| **topline net approval** | −24.5 | −20.2 | **+4.3** |

**The topline is off by 4.3 points because the subgroup errors cancel.** This is
the finding that matters most, and it has a direct consequence: adding
respondents cannot fix it. The Republican cell is not noisy, it is
deterministically wrong — 100% against 80.4%. Eighty replicates would still be
100%.

It is also a live demonstration that `scoring.profile_scores`'s split works as
designed: `level` error is small, `structure` error is large, and a scoreboard
reporting only the topline would have called this a success.

### 1.5 Only five of the sixteen crosstab cells could be compared at all

The panel is a 3-axis cross (party × age × education). The crosstab reports
marginals on 5 axes. What could not be compared, and why:

| cells | why |
|---|---|
| White, Black, Hispanic | **race is not an axis of the panel** |
| Male, Female | gender is assigned by position (`i % 2`), so the panel's gender marginal is 50/50 by construction and independent of party by construction |
| HS or less, Some college | the panel's education axis is binary; both fold into "no college degree" |
| College grad, Postgrad | both fold into "college graduate" |
| 30-44, 45-64 | the panel's buckets are 30-49 and 50-64; 45-49 falls on the other side |

## Part 2 — What the published method actually does

Ashokkumar et al. built an archive of 70 preregistered nationally
representative survey experiments (469 effects, 119,330 participants) and asked
an LLM to simulate responses. GPT-4-derived predictions correlated with real
treatment effects at **r = 0.85** (r_adj = 0.92), matching pooled human
forecasters (r = 0.84).

### 2.1 Their prompt

```
[Introduction]
You are a [Liberal/conservative], [Age], [Race/ethnicity], [Gender],
American with [Education level], who identifies as [Party].
The first page of the survey says: [Experimental stimulus text].
The next page of the survey says: [Outcome question].
Please choose a number from: [Outcome scale].
You choose:
```

Ours, `personas.describe()`:

```
You are {gender} living in {region}, aged {age}, {education},
with a household income of {income}.
Politically you identify as {party}.
```

Four differences, in the order they matter:

**Their axes are ideology / age / race / gender / education / party.** Ours are
gender / region / age / education / income / party. Same count, different set.
Race is three of the sixteen crosstab cells and we do not have it. Ideology and
party are two different things and the first is what drives opinion; we have
only the second. Region and income are texture in our panel — they do not enter
the weights, so they buy no resolution.

**They ask for a number on a scale and average the numbers. We ask for one of
three categories and tally shares.** A low-variance model on a numeric scale
gives 4, 4, 5, 4, 3 — dispersion survives. On a three-way choice it gives
approve × 8 — dispersion collapses to a point. Part of §1.1 is forced by the
response format, not only by the model.

**Their prompt ends `You choose:`** — a completion framing. Ours is a chat
instruction carrying meta-constraints ("do not explain, hedge, or mention that
you are playing a role"). Completion framing is less likely to elicit the
assistant's safe, typical answer.

**They sample each prompt five times and average**, explicitly "to reduce
idiosyncratic responding". We ask each prompt once.

### 2.2 Their own ablation on panel size

Extended Data Fig. 2, correlation with observed effects against ensemble size:

```
1 prompt   r = 0.64
2          r = 0.68
4          r = 0.75
8          r = 0.80    <- knee
15         r = 0.81
30         r = 0.83
60         r = 0.84
120        r = 0.84
```

**It saturates around eight.** Going from 8 to 120 is fifteen times the cost
for 0.04 of correlation.

This curve is for *treatment effects* — differences, where profile-level noise
averages out fast — and ours is a subgroup *level*, which needs enough
respondents inside each cell. So it does not transfer as "eight is enough". It
does transfer as: the published evidence says this saturates early, and panel
size is not where the money should go.

### 2.3 Three findings that bear directly on our design

**Levels are systematically biased; differences are not.** They report
b = 0.56 — LLM effect-size predictions are roughly twice the real ones,
r.m.s.e. 11.09 pp — and conclude that predictions "should be taken as estimates
of *relative* effect size", with absolute values recalibrated against benchmark
data. Our arena scores absolute levels. The persona arm is being asked to do
the thing this literature says it is worst at. The mitigation is available: we
hold real subgroup ground truth (Civiqs party filters, YouGov crosstabs) and
can regress on it.

**Model generation dominates everything else.** GPT-3 Davinci r = 0.24,
GPT-3.5 r = 0.81, GPT-4 r = 0.92; open-weight Gemma-3 27B 0.81, GPT-OSS 120B
0.85, DeepSeek v3 0.86. **Part 1 was run on the cheapest model we have.** Until
the same diagnostic runs on frontier models, "persona collapses" is a claim
about cheap models only.

**Variance underestimation is a known result, not our discovery.** They cite it
directly and their survey of 460 social scientists ranks it a top concern
(M = 76.8/100). §1.1 is a reproduction. That makes it real and citable; it does
not make it new.

**And one point of support for the arm's existence.** On why simulate rather
than ask directly: *"we adopted the latter strategy because simulation more
closely mirrors the original data-generating process and avoids the human
judgements required by direct forecasting."* That is exactly the `persona` vs
direct axis, with a published rationale.

## Part 3 — What to change

Ordered by evidence, not by ease. **Nothing here should be built before the
diagnostic runs on frontier models** (~$0.71 for glm, gpt-5.6-luna and
claude-opus); if `claude-opus` does not collapse on a four-point scale, half of
this is unnecessary.

| change | evidence |
|---|---|
| Axes become **ideology / age / race / gender / education / party** | §2.1; simultaneously aligns the panel with the sixteen crosstab cells (§1.5) |
| Drop region and income as texture, or make them weighting axes | they cost tokens and buy no resolution |
| Response format becomes the **four-point instrument** (strongly/somewhat approve, somewhat/strongly disapprove) | §2.1; three-way choice collapses, and the four-point version is the real instrument — `yougov_strong_approval` and `mc_strong_approval` already publish it |
| Sample each prompt several times and average | §2.1; temperature is already the provider default, so nothing blocks this |
| Add a `You choose:` completion-framed variant | §2.1, cheap control |
| **Do not raise k; consider lowering it** | §2.2 |
| Recalibrate levels against Civiqs/YouGov subgroup truth | §2.3 |

The axis change needs a joint distribution rather than the product of three
marginals — `ssa/personas.py` already flags this as the known error, noting
that party and education are correlated in the USA and that multiplying
marginals flattens it. A quota panel raked to match population marginals on all
six axes covers all sixteen crosstab cells at roughly today's panel size, since
the crosstab reports marginals and not the full joint. The table has to be
computed once from microdata (CPS or ANES) and **written out and committed**,
the same way `PARTY`, `AGE` and `EDUCATION` are today, because the repository's
determinism invariant applies to our own code.

That invariant is about our code, not about the provider: `scoring.py` uses a
seeded `random.Random`, and no call site sets a temperature. Repeated sampling
of a prompt does not violate it.
