# When a question closes

Every question closes on its own clock. This page is the participant- and
operator-facing summary; `ssa/batches.py` is the implementation and owns the
reasoning.

## The rule

| | |
|---|---|
| **Listed** | a week before the question closes |
| **We call your endpoint** | in the 24 hours before it closes |
| **Closes** | the round's own `lock_at` — `release − 48h` for 94 of season 0's 116 rounds |
| **Horizon** | close → release: 2.0 days for those 94, larger for the rest |
| **Scored** | the first refresh after the release lands, within six hours |

A submission is on time if it arrives before that question closes. The null
freezes when the call window *opens*, which is where every entrant's context
freezes too, so neither the entrant nor the baseline reads anything the other
could not. Those are two different instants a day apart, and the day matters:
on `mc-2026-w37-approval` the entrants answered on a history ending at 40.0
while a null frozen at the close had read the 46.0 that landed inside the
window.

## Why 22 rounds close earlier than `release − 48h`

Because they ask about a period rather than a moment, and a lock inside that
period would leak the answer.

| rounds | closes | why |
|---|---|---|
| 8 Wikipedia weekly top-10 | 11 days before release | the measured week must begin after the close |
| 8 Google Trends baskets | 8 days before release | same |
| 2 Wikipedia and 2 Trends singles | 11 days before release | same |
| 2 midterm specials | 2026-10-30, 32 days before release | sealed four days before polls close; resolved on certified results |

These locks are properties of the question. No rule overrides them, and
`horizon_days` reports the distance so a reader can see which questions ask
further ahead.

## Why not one deadline a week

Season 0 tried it — Monday 12:00Z for every round in the week — and it is
recorded here because the reasoning was good and the outcome was not.

It fixed two real problems, both caused by entrants choosing *when* to answer:
one entrant could file a week out and another ninety seconds before the lock,
the second with up to seven days more news; and the null, frozen at the lock,
read data the early filer never saw. A board built that way partly ranks
patience.

It created a worse one. **The horizon stopped being a property of the
question.** Measured on the 79 rounds open on 2026-09-06, the distance from the
common deadline to the release ran from 2.0 days to 35.5, median 4.1, and 60 of
79 rounds were frozen days before they needed to be. Two questions on one board
were forecast a day and a month ahead of their answers and then compared.

The original problems do not bite any more, because nobody chooses when to
answer. Season 0 admits outside entrants through an endpoint the arena calls;
every entrant for a round is called inside the same 24-hour window, and what
the arena hands over — the frozen history, the persistence null, the news
corpus — is fixed at the window's opening, so being reached early or late in
the window changes nothing an entrant sees.

Prophet Arena arrives at the same design from the other direction: its windows
are per event and open only a few hours, and its one-deadline weekly set is an
on-ramp rather than the measurement.

**If a human or pull-request track returns**, it needs a short window opening
before each round's close — not a weekly deadline, which would reintroduce the
horizon spread for every round in the week.

## The call window

24 hours, ending 30 minutes before the close. Within it:

- the first call is aimed at the first refresh after the window opens;
- a failed call is retried by the six-hourly refresh until the 30-minute
  margin, so a brief outage is survivable;
- a valid forecast filed inside the window is final and is never re-bought;
- the news corpus and every context field are frozen at the window's opening,
  identical for an entrant called first and one retried last.

What the window bounds is only what an entrant looks up *for itself* between
the first call and the last retry. Three days of that was a real advantage to
whoever happened to be retried late, which is why it is a day.

## In code

```python
batches.effective_deadline(lock_at)   # the round's own lock: when it closes
batches.window_opens_at(lock_at)      # close - 24h: when calling starts, and
                                      # where the null and every context freeze
batches.freeze_at(lock_at)            # the close, for "had this been published
                                      # yet" -- what resolution reads
batches.published_at(lock_at)         # close − 7 days: when it is listed
batches.horizon_days(lock_at, release_at)   # close → release, for reporting
```

`tools/validate_submission.py` carries its own copy of the rule because it
imports nothing; `tests/test_batches.py` walks a year of hourly locks and
requires the two to agree to the second.
