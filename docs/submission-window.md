# The weekly submission window

One deadline a week, for every round in that week's batch. This page is the
contract that `#47` (participant onboarding) and `#48` (question generation)
build against. `ssa/batches.py` is the implementation and owns the reasoning;
this page is the operator- and participant-facing summary.

## The rule

| | |
|---|---|
| **Batch published** | Monday 12:00Z, one week ahead |
| **Batch deadline** | Monday 12:00Z |
| **What is in a batch** | every round whose lock falls after that deadline and before the next one |
| **Round lock** | unchanged: `release − 48h` |
| **Horizon** | deadline → lock, 0 to 7 days, varying by round |
| **First batch under this rule** | `batch-2026-09-14` |

A submission is on time if it arrives before its batch deadline. Filing early is
allowed and always was; the deadline is common, so waiting for it is an option
every entrant has.

## Why it is not each round's own lock

Season 0's 89 rounds lock on six different weekdays:

| Mon | Tue | Wed | Thu | Fri | Sat | Sun |
|---|---|---|---|---|---|---|
| 10 | 5 | 43 | 0 | 12 | 2 | 17 |

Under the old rule that was six deadlines to track, and two measurement
problems that no amount of documentation fixes.

**Entrants were not answering from the same place.** A round is open from its
listing day, so one entrant could file a week out and another ninety seconds
before the lock — both legal, the second with up to seven days more news. On a
48-hour horizon that is most of the question, so the leaderboard partly ranked
patience.

**The null read data the entrants did not.** Baselines were frozen from history
available at the *lock*, and the headline metric is
`1 − CRPS(entrant) / CRPS(persistence)`. An entrant who filed five days early
was divided by a null that had read five more days of the series. The bias
scaled with how early each file landed, so a season mean over rounds partly
measured the lock-day calendar.

Both close if the deadline is common **and the null freezes at that same
moment**. Entrant and null then read exactly the same history.

## What still varies, deliberately

The horizon — how long after the deadline a round locks — varies across a
batch, and that is kept rather than removed. It is identical for every entrant,
so it is a property of the question, not a confound between competitors. For
`batch-2026-09-14`:

```
09-07 Mon 12:00Z   batch published (13 rounds)
09-14 Mon 12:00Z   DEADLINE — everything above is due
09-14 Mon 14:00Z   mc-2026-w38-approval        locks   +0.1d
09-15 Tue 14:00Z   aaii-2026-09-17             locks   +1.1d
09-16 Wed 14:00Z   civiqs ×7                   lock    +2.1d
09-16 Wed 22:00Z   civiqs-profile-2026-w38     locks   +2.4d
09-20 Sun 14:00Z   hh + yougov ×3              lock    +6.1d
```

Report it per round. "Skill against horizon" is a real result the old design
could not produce, because horizon was an entrant choice rather than a
question property.

## Why Monday 12:00Z

The modal round locks Wednesday 14:00Z (43 of 89), putting the dominant horizon
at 2.1 days — the same distance the model harness already bought at under the
per-round window (`lock − 3d` to `lock − 2d`). The cutover therefore barely
moves the vantage point for half the season, and rounds either side of it stay
broadly comparable.

Monday also leaves the weekend for the buying run to retry a failed provider.
A common deadline needs that: the insurance tail that used to retry up to
`lock − 30min` cannot reach past the deadline any more without breaking the
equal-vantage rule that is the entire point.

## The cutover is dated

Rounds whose deadline falls before **2026-09-14T12:00:00Z** keep the per-round
lock rule they were bought and scored under. Re-freezing a resolved round's null
would silently rewrite published scores.

At the time of writing: 31 of 89 rounds fall under the batch rule; all 5
resolved rounds and their frozen snapshots keep the old one.

Ask `batches.governed_by_batch(lock_at)` rather than comparing dates by hand.

## Implementation notes

`ssa/batches.py` is the single source of truth:

```python
batches.effective_deadline(lock_at)   # when a submission is due
batches.freeze_at(lock_at)            # when the null freezes — same instant
batches.governed_by_batch(lock_at)    # which rule applies
batches.horizon_days(lock_at)         # deadline -> lock, for reporting
batches.batch_of(lock_at)             # "batch-2026-09-14"
batches.published_at(lock_at)         # deadline − 7d
```

`tools/validate_submission.py` carries a **mirror** of the calendar rather than
importing it, because that file deliberately imports nothing from `ssa/` so CI
can run it on a bare checkout. `tests/test_batches.py` walks a year of hourly
locks and fails if the two ever disagree by a second. Change the calendar in
both, or the test will say so.

### For #47 (participant onboarding)

- The deadline shown to a participant is `effective_deadline`, never `lock_at`.
- A bundle is a batch: one deadline, many rounds, mixed horizons.
- `batch_of` is the natural bundle id.
- Say plainly that early filing is allowed and that the deadline is shared, so
  nobody believes waiting is an edge. It is not, and after this change it is
  not scored as one either.

### For #48 (question generation)

- A generated round joins the batch implied by its lock; nothing extra to set.
- A round generated after its batch deadline has passed cannot be published
  into that batch — it belongs to the next one, or it is dropped.
- The publication lead is `deadline − 7d`, so a round must be frozen and
  reviewed before then to appear in that bundle.

### Still open

The model harness still buys inside the per-round window
(`harness.FILE_WINDOW_SECONDS`, `lock − 3d` to `lock − 2d`) rather than at the
batch deadline. Until that moves, our own entrants and external entrants answer
from slightly different points for rounds whose horizon is not ~2 days. Moving
it requires the retry budget to fit before the deadline, which is why it is
sequenced with the automation work rather than done here.
