# Pairwise leaderboard

Proposed aggregation change, prepared 2026-10-16. This changes the Overall
competition standings, not frozen forecasts, resolutions, or the scoring of
individual answers. The Original skill view and task-specific boards retain
their existing calculations. The chart continues to show original skill/CRPS.

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

The auxiliary Top 3 column splits tied places across the boundary. For
example, with one clear winner and three tied runners-up, each runner-up gets
2/3 of a top-three credit. With fewer than three submissions, every submission
gets one credit. Consequently a top-three appearance is less selective in a
small field. The total is a count of appearances (possibly fractional), not a
percentage or a proper score.

Skill is unchanged: the average of published per-question
`1 - entrant_loss / persistence_loss` over the method's answered questions,
excluding missing answers. Vac's disjoint specialist answers are combined
before this average. Pairwise and Top 3 are rank summaries and do not inherit
the propriety of the underlying probabilistic scoring rules.

## Active comparison field

The current team selections are: Apodex-FutureFlow,
H2O.ai SuperAgent, Vac, YuLan-OneSim, and ZhengZheng Forecaster. Vac combines
only `vac-public-opinion`, `vac-sentiment-expectations`,
`vac-population-profiles`, and `vac-collective-attention`.

worldVac and H2O.ai Marina are distinct alternatives, not fallback answers;
they are not selected in this proposed field. Team selections must be
confirmed before this proposal is published. No method is selected based on
the outcome of a particular question. Multiple specialist answers from Vac
on one question stop the board with an explanation instead of choosing the
best answer after resolution.

The roster is discovered from the published entrant registry and resolved
scores. Registered participants enter automatically after their first valid
scored answer. The public board also includes active registered LLM methods and
their generated harness variants. Retired methods, crowd, statistical
baselines and known house tests are excluded. The current snapshot contains
five external methods and twelve model configurations; these are not size
limits. Existing team selections above remain explicit overrides.

A registration with no scored answer
does not enter the field or shrink Common. After the first scored answer, a
new entrant changes the opponent field and may shrink Common, even to empty.
Retirements can also change the field. Historical totals are recomputed for
the current active roster; they are not frozen historical league standings.
Chart selections do not change the comparison field.

## Windows

- **All**: every comparable resolved question in the published season data.
  Registration dates do not trim this window, so it measures historical
  contribution and penalizes pre-registration absence.
- **S0**: comparable questions with `lock_at >= season0_main.since`
  (currently 2026-09-30T00:00:00Z). Resolution dates do not select this window.
- **Common**: the intersection of scored submissions from every method in
  the active field, across all resolved history. No common question
  means an explicit empty board, not a fallback to a different window.

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
refresh. A deployment can compute the proposal from an existing payload.

The public page offers one comparison field, including models. All and S0 use
the existing range controls; Common sits immediately beside S0 and switches
the Overall ranking to the shared-question intersection. The chart retains
its existing history view. There is no External only public control or URL mode.

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
