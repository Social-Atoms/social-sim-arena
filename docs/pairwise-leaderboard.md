# Pairwise leaderboard

The Overall competition standings aggregate published question scores without
changing frozen forecasts, resolutions or the scoring of individual answers. Both Overall metrics use the same methods, grouping and question window.
Original skill retains the published per-question skill formula and averages
answered questions in that shared window. Task-specific boards retain their existing scientific scores. The Overall chart
has separate Pairwise win rate, Original skill and CRPS views.

## Question score and total

For a resolved question with `m >= 2` scored methods in the selected field:

```
question_points = 100 * (opponents_with_larger_loss
                        + 0.5 * opponents_with_equal_loss) / (m - 1)
pairwise_total = sum(question_points) / N
```

`N` is the same number of comparable questions for every method in that
window. A missing answer contributes zero and is not an opponent that others
can beat. Questions with fewer than two scored methods are excluded, rather
than manufacturing a win for a sole submitter. All questions have equal
weight; no domain weights are applied.

Use each question's published raw CRPS, energy score, or list loss (lower is
better); never compare losses from different questions directly. Equality
uses the published loss precision, so results can differ from comparisons of
internal unrounded scores. Rank is competition rank, with ties sharing a rank.

100 means submitting on every comparable question and strictly beating every
opponent on each one. It does not mean a perfect prediction of the outcome.
With full coverage, 50 means an average half-win against submitted opponents.
With missing answers, the total reflects both coverage and relative results.

Skill is unchanged: the average of published per-question
`1 - entrant_loss / persistence_loss` over the method's answered questions,
excluding missing answers. Vac's disjoint specialist answers are combined
before this average. Pairwise is a rank summary and does not inherit the
propriety of the underlying probabilistic scoring rules. The page displays
Pairwise win rate or Original skill, never auxiliary Top 3/skill annotations
in a method subtitle. Both views share the original method/setup labels and
exclude test accounts and historical endpoint demos.

## Active comparison field

The current external methods are Apodex-FutureFlow, H2O.ai SuperAgent,
H2O.ai Marina, Vac, worldVac, YuLan-OneSim and ZhengZheng Forecaster. Vac combines
only `vac-public-opinion`, `vac-sentiment-expectations`,
`vac-population-profiles`, and `vac-collective-attention`.

worldVac and H2O.ai Marina are independent methods, included alongside Vac
and SuperAgent rather than used as fallback answers. No method is selected based on
the outcome of a particular question. Multiple specialist answers from Vac
on one question stop the board with an explanation instead of choosing the
best answer after resolution.

The roster is discovered from the published entrant registry and resolved
scores. Registered participants enter automatically after their first valid
scored answer. The public board also includes active registered LLM methods and
their generated harness variants, plus registered statistical baselines and
Crowd. Persistence, Trend, EWMA, Climatology and Crowd occupy ordinary
comparison slots. Retired/revoked registrations and known house tests are
excluded. The roster has no fixed size limit. Vac and Anonymous use the explicit
grouping rules described here; other registered methods remain independent.

Season 0 eligibility was fixed on 2026-10-19 to the exact 24 method IDs in
`SEASON0_METHODS` in `site/pairwise.js`: the seven external methods above,
twelve existing model variants, and five baselines. No prefix matching,
registration time or first-answer time can grant a new method this eligibility.
Vac is one method, with its existing four-account mapping preserved.

A registration with no scored answer does not enter any field. After its first
valid scored answer, a later participant, model or harness variant joins All
only. It cannot become an S0/Common opponent or shrink their shared-question
intersection. Anonymous remains one All-only group. This is a current-season
eligibility rule, not registration approval or a rule for future seasons.
This freezes eligibility, not forecasts or results. Crowd continues to pool
available submissions under its existing rules, so new teams may contribute
to future Crowd forecasts; existing published scores are not rewritten here.
Retirements and revocations can still change the active field. Historical totals are recomputed for
the current active roster; they are not frozen historical league standings.
Chart selections do not change the comparison field.

## Windows

- **All**: every comparable resolved question in the published season data.
  Registration dates do not trim this window, so it measures historical
  contribution and penalizes pre-registration absence.
- **S0**: only active methods in the fixed Season 0 cohort; comparable questions with `lock_at >= season0_main.since`
  (currently 2026-09-30T00:00:00Z). Resolution dates do not select this window.
- **Common**: the intersection of scored submissions from every method in
  the active fixed Season 0 cohort, across all resolved history (not just S0 dates). No common question
  means an explicit empty board, not a fallback to a different window.

Trend, EWMA and Climatology currently answer number questions only. Their
missing profile/ranking answers score zero in All/S0, and their coverage also
constrains Common to questions they answered.

Changing the field recomputes both the eligible questions and the opponents.
All and S0 totals can move down when newly resolved questions are added;
adding one's own answer to an existing fixed question set cannot reduce its
total if all other answers are unchanged. Common selects on observed
participation, so its results describe that subset, not necessarily the full
season. The page names the window, denominator, and field size explicitly.

## Reproduce without network or paid calls

```sh
node tests/site/pairwise.js
PYTHONPATH=. python tests/test_pairwise.py
```

The browser reads `rounds[].scores` already present in `site/data.json`.
There is no new forecast call, source pull or generated score artifact to
refresh. A deployment computes the standings from an existing payload.

The public page offers one comparison field, including models. All and S0 use
the existing range controls; Common sits immediately beside S0 and switches
the Overall ranking and curves to the shared-question intersection. New visits
and the bare leaderboard entry default to S0 Pairwise win rate. Explicit All,
metric and entrant selections remain shareable. There is no External only
public control or URL mode.

## Curves and display defaults

Pairwise curves use the same scoring pass and current active method roster
as the standings. They accumulate equally weighted question points in
resolution order, combine same-day resolutions into one checkpoint, and
end at the displayed score. A single-question window shows a score point.
The Pairwise axis is 0–100, with no oracle or persistence-reference labels.

Changing the right-hand metric selects its matching left-hand curve. The
chart can independently select Pairwise, Skill or CRPS without changing
the standings metric. Skill/CRPS curves use the shared grouped methods and
question window; those means omit unanswered questions, as their standings
columns do.

Default curves show every eligible external method, including both H2O
methods and both Vac methods, with no top-N or answer-coverage gate. Add
representative model variants (the best arm per vendor in S0, omitting the
lowest vendor when at least three are available), Persistence and Crowd.
Trend, EWMA and Climatology remain selectable. Hiding a curve never removes
a method from scoring or Common. Explicit manual selections persist; the
updated selection-storage version avoids inheriting the previous defaults
that hid teams.

## Anonymous team

Anonymous A1–A6 form one All-only method, `anonymous-combined`. They do not
enter S0 or the Common opponent field/intersection. On a question answered
by multiple accounts, use the first valid scored answer in the fixed order
A1, A2, A3, A4, A5, A6. Thus A1 wins any overlap when it has a valid answer;
without A1, the lowest-numbered available account is used. Never choose by
observed loss or skill, never average the six scores, and count at most one
answer/opponent/Top 3 credit for the team per question. Registration must be
approved separately; defining this group does not itself admit its accounts.
Vac's existing overlap error remains unchanged.
