"""When a round closes: its own lock, and the weekly calendar it displays on.

**The deadline is the round's own `lock_at`.** For 94 of season 0's 116
rounds that is `release - 48h`; for the other 22 it is deliberately earlier,
because the round asks about a period rather than a moment -- a Wikipedia or
Trends week locks before the week it measures begins, and the midterm rounds
lock four days before the polls close and resolve on certified results a month
later. Those locks are properties of the question and no rule may override
them.

**What this replaces, and why it came back.** Season 0 briefly moved every
round onto one weekly deadline (Monday 12:00Z). That fixed two real problems
and created a worse one.

The two it fixed, both of which came from entrants choosing *when* to answer:
one entrant could file a week out and another ninety seconds before the lock,
and the second had up to seven days more news; and the null, frozen at the
lock, read data the early filer never saw. A board built that way partly ranks
patience.

The one it created: **the horizon stopped being a property of the question.**
Measured on the 79 rounds open on 2026-09-06, the distance from the common
deadline to the release ran from 2.0 days to 35.5, median 4.1, and 60 of 79
rounds were frozen days before they needed to be. Two questions on the same
board were forecast one day and one month ahead of their answers and then
compared. That is not a fair comparison; it is two different tasks.

**Why the original problems no longer bite.** Season 0 admits outside entrants
through an endpoint the arena calls (`docs/agent-api.md`). Nobody chooses when
to answer: every entrant for a round is called inside the same narrow window
before that round's lock, and the null freezes at the same lock. The two
problems above were problems of self-scheduling, and self-scheduling is gone.
Prophet Arena reaches the same place from the other direction: its windows are
per event and stay open only a few hours, and its weekly fixed-deadline set is
an on-ramp, not the measurement.

**If a human or pull-request track returns**, it needs a short window opening
before each round's lock -- not a weekly deadline. A weekly deadline
reintroduces the horizon spread for every round in the week, which is the
thing this module now exists to prevent.

**The weekly calendar stays, for display only.** `deadline_for` and
`published_at` still say which week a round belongs to and when the site lists
it. They no longer decide when a submission is late; `effective_deadline`
does, and it returns the round's own lock.
"""
import os
from datetime import datetime, timedelta, timezone

# Monday, as datetime.weekday() counts it (Mon=0 ... Sun=6).
BATCH_WEEKDAY = 0
BATCH_HOUR_UTC = 12

# The first deadline the batch rules govern. Rounds locking before this were
# bought and scored under the per-round lock rule and keep it forever.
FIRST_DEADLINE = datetime(2026, 9, 14, BATCH_HOUR_UTC, tzinfo=timezone.utc)

# How long before its own deadline a round is listed, so entrants get a full
# week. Publication is a site concern, not a validity rule: a late-added round
# is still governed by its lock.
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
    """Whether this round is listed under a weekly batch on the site.

    A grouping question now, not a validity one: the batch deadline no longer
    decides when anything is late. Kept because the site and the bundle tools
    ask which week a round belongs to, and because a round locking before the
    calendar existed belongs to no week at all.
    """
    return deadline_for(lock_at) >= FIRST_DEADLINE


def effective_deadline(lock_at):
    """The moment a submission for this round must be in by: its own lock.

    The one function a validator should call. It is the round's `lock_at`
    unconditionally -- `release - 48h` for most rounds, and deliberately
    earlier for the ones that ask about a period rather than a moment. See the
    module docstring for why the weekly deadline that briefly sat here was
    removed.
    """
    return _parse(lock_at)


# How long before a round closes the arena starts calling endpoints. **The
# canonical definition**: `harness.FILE_WINDOW_SECONDS` and the search
# adapter's cache age both read it from here. It used to be spelled out in
# `harness` and again in `ssa/adapters/search.py`, each parsing the same
# environment variable, with a test pinning the two together because the
# duplicate had already drifted once -- the window shrank from three days to
# one and the search cache went on serving three-day-old replies. This module
# imports nothing from the package, so both can read it and the duplicate goes
# away instead of being policed.
FILE_WINDOW = timedelta(
    hours=float(os.environ.get("SSA_FILE_WINDOW_HOURS") or "24"))
FILE_WINDOW_SECONDS = FILE_WINDOW.total_seconds()


def window_opens_at(lock_at):
    """When the arena starts calling this round's endpoints.

    Every entrant is called inside `[window_opens_at, effective_deadline)`, so
    this is the instant the round's inputs stop moving for the people answering
    it: whoever is called first and whoever is retried last are handed the same
    history and the same null.
    """
    return effective_deadline(lock_at) - FILE_WINDOW


def freeze_at(lock_at):
    """The moment after which the round's answer already exists somewhere.

    The round's own close. This is the boundary for questions of the form "had
    this been published yet" -- what `ssa/resolve.py` treats as already seen
    rather than as the answer, and what `ranking_round` refuses a measured week
    for ending before.

    **Not the same as the boundary the null is built on.** That one is
    `window_opens_at`: the null must read what the entrants read, and the
    entrants were handed their history when the window opened, up to a day
    earlier. `refresh.update_lock_snapshot` records both -- `history` at this
    instant, `answer_history` at the window's opening -- because only an
    observation time can tell them apart, and a monthly value's label date
    cannot.
    """
    return effective_deadline(lock_at)


def published_at(lock_at):
    """When this round is listed for entrants: a week before it closes.

    Per round, like the deadline it is measured from. Publishing by batch
    instead meant a round could appear anywhere from seven to fourteen days
    before its own close, which is the same calendar artefact the weekly
    deadline had.
    """
    return _parse(lock_at) - PUBLISH_LEAD


def horizon_days(lock_at, release_at=None):
    """Days from the deadline to the answer -- the forecast horizon.

    Measured to the release, because the deadline is now the lock and the
    distance between them is zero by construction. This is the number that
    makes two rounds comparable or not: 2.0 days for the 94 rounds that lock
    at `release - 48h`, and 8, 11 or 31 for the ones that ask about a period
    and must lock before it starts. It is identical for every entrant in a
    round, so it is a property of the question rather than a confound between
    competitors -- which is exactly what it stopped being when a weekly
    deadline made it range from 2 to 35 days across one board.

    `release_at` is optional only so the old one-argument call sites keep
    working; without it there is no horizon to report and it returns None.
    """
    if release_at is None:
        return None
    return (_parse(release_at) - effective_deadline(lock_at)).total_seconds() / 86400.0


def batch_of(lock_at):
    """A stable id for the batch a round belongs to, e.g. `batch-2026-09-14`."""
    return "batch-" + deadline_for(lock_at).strftime("%Y-%m-%d")
