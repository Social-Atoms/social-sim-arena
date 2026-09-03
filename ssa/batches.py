"""The weekly submission batch: one deadline a week, for every round in it.

**What this replaces.** Until season 0's batch cutover every round carried its
own deadline -- its `lock_at`, at `release - 48h` -- and a submission was valid
if it landed before that moment. Season 0's 89 rounds lock on six different
weekdays (43 Wednesday, 17 Sunday, 12 Friday, 10 Monday, 5 Tuesday, 2
Saturday), so "the deadline" was six deadlines, and an entrant had to track all
of them. That is a bad participant experience, and it is also two measurement
problems that no amount of documentation fixes.

**Problem one: entrants were not answering from the same place.** A round is
open from its listing day, so one entrant could file a week out and another
ninety seconds before the lock. Both are legal, and the second one has up to
seven days more news. On a 48-hour horizon that is not a rounding error; it is
most of the question. A leaderboard built that way partly ranks patience.

**Problem two: the null saw data the entrant did not.** Baselines are frozen
from history available at the lock (`refresh.update_lock_snapshot`), and the
headline metric is `1 - CRPS(entrant)/CRPS(persistence)`. An entrant who filed
five days before the lock is divided by a null that read five more days of the
series. The bias is not constant either -- it scales with how early the file
landed, which varied per entrant and per round -- so a season mean over rounds
depended on the lock-day calendar as much as on the forecasters.

**The fix is one deadline per week, and the null frozen at that same moment.**
Every round is governed by the last batch deadline strictly before its lock.
Entrants answer everything in a batch by that one moment; the null is frozen
there too, so entrant and null read exactly the same history. What still varies
is the horizon -- how long after the deadline a given round locks, 0 to 7 days
-- and that now varies *identically for everyone*, which makes it a property of
the question and a covariate worth reporting, rather than a confound between
competitors.

Anyone may still file early. That is not unfairness: the deadline is common, so
waiting for it is an option every entrant has. What is unfair is a deadline
that differs per round, and a null that reads past it.

**Why Monday 12:00Z.** The modal round locks Wednesday 14:00Z (43 of 89), which
puts the dominant horizon at 2.1 days -- the same distance the model harness
already bought at under the old per-round window (`lock - 3d` to `lock - 2d`).
So the cutover barely moves the vantage point for half the season, and rounds
scored before and after it stay broadly comparable. Monday also leaves the
whole preceding weekend for the buying run to retry a failed provider, which a
common deadline needs because the insurance tail that used to run up to
`lock - 30min` cannot reach past the deadline any more without breaking the
equal-vantage rule that is the entire point.

**The cutover is dated, not retroactive.** Rounds whose governing deadline
falls before `FIRST_DEADLINE` keep the per-round lock rule they were bought and
scored under. Re-freezing an already-resolved round's null would silently
rewrite published scores, which is the one thing a benchmark may never do.
`governed_by_batch` is the single predicate for "which rule applies", so no
caller has to reimplement the cutover and get it subtly different.
"""
from datetime import datetime, timedelta, timezone

# Monday, as datetime.weekday() counts it (Mon=0 ... Sun=6).
BATCH_WEEKDAY = 0
BATCH_HOUR_UTC = 12

# The first deadline the batch rules govern. Rounds locking before this were
# bought and scored under the per-round lock rule and keep it forever.
FIRST_DEADLINE = datetime(2026, 9, 14, BATCH_HOUR_UTC, tzinfo=timezone.utc)

# How long before its deadline a batch is published, so entrants get a full
# week. Publication is a site/bundle concern, not a validity rule: a late-added
# round is still governed by the deadline computed from its lock.
PUBLISH_LEAD = timedelta(days=7)


def _parse(t):
    """Accept an ISO string (with or without `Z`) or an aware datetime."""
    if isinstance(t, datetime):
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(t).replace("Z", "+00:00"))


def deadline_for(lock_at):
    """The batch deadline governing a round that locks at `lock_at`.

    The last Monday 12:00Z *strictly* before the lock. Strictly, because a
    round locking exactly at a deadline must be answered by the previous one --
    otherwise its entrants would have no time between deadline and lock, and
    the round would be scored against forecasts filed at the same instant the
    answer stopped being predictable.

    Returns an aware datetime regardless of the cutover; ask
    `governed_by_batch` whether that deadline is the rule in force.
    """
    lock = _parse(lock_at)
    back = (lock.weekday() - BATCH_WEEKDAY) % 7
    candidate = (lock - timedelta(days=back)).replace(
        hour=BATCH_HOUR_UTC, minute=0, second=0, microsecond=0)
    if candidate >= lock:
        candidate -= timedelta(days=7)
    return candidate


def governed_by_batch(lock_at):
    """Whether the weekly deadline, rather than the round's own lock, applies.

    False for every round whose deadline predates the cutover. Those rounds
    were bought and scored under the per-round rule; re-freezing their nulls
    now would rewrite scores that are already published.
    """
    return deadline_for(lock_at) >= FIRST_DEADLINE


def effective_deadline(lock_at):
    """The moment a submission for this round must be in by.

    The batch deadline once the cutover applies, and the round's own lock
    before it. This is the one function a validator should call.
    """
    return (deadline_for(lock_at) if governed_by_batch(lock_at)
            else _parse(lock_at))


def freeze_at(lock_at):
    """The moment the round's baseline history is frozen.

    Deliberately the same instant as `effective_deadline`: the null must read
    the history the entrant read, and nothing after it.
    """
    return effective_deadline(lock_at)


def published_at(lock_at):
    """When the batch containing this round is published to entrants."""
    return deadline_for(lock_at) - PUBLISH_LEAD


def horizon_days(lock_at):
    """Days from the governing deadline to the lock -- the forecast horizon.

    Reported per round because it varies across a batch (0 to 7 days) while
    being identical across entrants, which is exactly what makes it a question
    property worth analysing rather than a confound worth removing.
    """
    return (_parse(lock_at) - effective_deadline(lock_at)).total_seconds() / 86400.0


def batch_of(lock_at):
    """A stable id for the batch a round belongs to, e.g. `batch-2026-09-14`."""
    return "batch-" + deadline_for(lock_at).strftime("%Y-%m-%d")
