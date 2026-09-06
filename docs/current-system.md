# Current system

Canonical as of 2026-08-29. When prose elsewhere conflicts with this page,
machine-readable configuration and code win, in this order:

1. `questions/season0.json` for rounds, releases, locks, and answer shapes;
2. schemas and `tools/validate_submission.py` for accepted submissions;
3. `.github/workflows/*.yml` for automation cadence and CI behavior;
4. `ssa/` for fetching, resolution, and scoring behavior;
5. this page for the human-readable summary.

Dated audits and the internal roadmap describe history or proposals. They are
not current protocol.

## Snapshot

- Season 0 contains 89 rounds across 14 tracker labels.
- Answer shapes currently in the season are 79 scalar distributions, 7 joint
  profiles, and 3 rankings.
- Forecasts normally lock 48 hours before release. Behavioral windows such as
  Wikipedia and Google Trends lock before the measured window begins, as
  declared in the round definition.
- The scheduled refresh runs every six hours at minute 17. GitHub scheduling is
  best-effort, so correctness depends on per-round lock timestamps, not exact
  cron arrival.
- Current public submission fallback is repository files. The Agent API and
  questionnaire contracts exist, but production activation still requires
  registration review and the documented security/operations gates.

## Registry coverage

`ssa.series.SERIES` currently registers 78 source series. Season 0 uses 26 of
those; 52 registered series do not appear in any round. The season also uses
three derived/special series outside that registry (`generic_ballot_margin`,
`house_seats`, and `wiki_top10_en`). Registration therefore means “available
to the pipeline,” not “approved or scheduled as a question.”

New rounds are still drafted by hand. The intended next step is a review-first
Round Factory that generates candidates from a release calendar and fixed
templates, then fails closed on source rights, history, volatility,
resolution, and schedule before a human freezes the round.

These counts will change when the season manifest changes. They should be
checked or generated from the manifest rather than copied into papers.

## Submission timing

Scientific locks remain per question. Participant ergonomics should use a
weekly bundle:

1. On a fixed weekday, publish a versioned bundle containing all questions
   locking during the following seven days.
2. A participant returns one bundle response.
3. Intake splits it into the existing per-round forecast records and validates
   every answer against that round's real `lock_at`.
4. Early answers are final unless the protocol explicitly permits replacement;
   a late answer is rejected for that round without invalidating earlier valid
   answers in the same bundle.

This creates one weekly participant action without weakening the
contamination-proof release-relative locks.

## Failure behavior today

### Source fetching

- Required empty poll inputs fail the refresh instead of publishing zeros.
- Raw upstream bodies and dated archives are retained for provenance.
- `ssa.health` tracks time since successful fetch and time since content last
  changed, using source-specific staleness budgets.
- There is no lagged Michigan/FRED resolution fallback. A stale value must not
  silently resolve a newer release.

Remaining gap: health problems are exposed in output, but the project still
needs a clearly owned alert/escalation policy and a release-level state for
“answer not obtainable by the resolution deadline.”

### LLM forecasting

- Entrant-round jobs run independently and concurrently.
- One failed provider does not discard successful forecasts from the same run.
- Failures are listed and make the refresh exit non-zero; scored mocks are not
  filed in production.
- Terminally unavailable configured routes may use an explicitly configured
  standby. The route is recorded in forecast notes and changes the input hash,
  so recovery re-runs the primary route.
- The second post-resolution refresh uses `SSA_SKIP_FILING=1`, preventing a
  timeout from causing the same paid LLM call twice.

Remaining gap: there is no durable per-entrant-round retry ledger showing
attempt count, next retry, terminal state, and distance to lock. That ledger
and its alert are the next reliability feature, not another generic retry loop.

## Source permission rule

A source is not approved merely because its terms omit an explicit ban.
Before activation, record separately:

- permission to access manually;
- permission to access with automation;
- permission to retain raw responses;
- permission to redistribute data or derived records;
- attribution requirements and evidence date.

Ambiguous automation or redistribution rights mean `permission-needed`, not
approved. The repository software license does not grant rights in third-party
data.

As of this snapshot, Conference Board, AAII, University of Michigan, and
YouGov require a formal source-by-source remediation decision before further
public historical accumulation. Their exact restrictions differ: do not treat
this sentence as one blanket legal conclusion. Until the rights matrix is
approved, no new source-dependent round or public raw/history snapshot should
be added merely because the data is viewable online.

## Question-generation direction

FutureX demonstrates a useful scalable mechanism: source-specific templates,
variable substitution, automated candidate generation, deterministic
filtering, and periodic publication. We should adopt that mechanism but not its
permission assumptions or point-answer design.

Every candidate for this arena must pass all of these gates:

1. approved access and redistribution status;
2. outcome does not exist at lock time;
3. scheduled, machine-verifiable resolution;
4. enough movement that persistence is not effectively the answer;
5. stable construct and unit across rounds;
6. meaningful connection to social simulation, market behavior, or collective
   attention—not merely a convenient webpage value;
7. human approval before the round is frozen.

For Market Research, prefer repeated behavioral quantities with an interpretable
population or choice process—for example category demand shares, opening-weekend
audience allocation, or consumer choice distributions—over isolated prices.
For Social Interaction, prefer composition and change in collective attention
(weekly top-list turnover, cross-topic attention shares, event-driven migration)
over forecasting the level of one famous page. Until at least two strong
families pass the gates, label these sections as pilots rather than padding the
benchmark with weak rounds.
