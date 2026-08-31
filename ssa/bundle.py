"""The weekly bundle: one batch, one deadline, every shape of question in it.

A *bundle* is what a participant is handed on Monday: the batch of rounds due
at the next deadline, in one payload, in one shape. `ssa/batches.py` owns the
calendar and this owns the payload. Issue #47 consumes it, so the field names
here are a contract with that code and with `schema/bundle.schema.json`.

**It is built from the reviewed season file, never from candidates.**
`tools/generate_rounds.py` proposes rounds into `questions/candidates/`; a
human moves the ones they accept into `questions/season0.json`; this reads only
that file. The separation is the whole reason the frozen season file stays
trustworthy -- if the bundle could reach into the candidate pile, generation
would become publication, and no round in front of an entrant would carry a
human's decision behind it.

**The deadline shown is `effective_deadline`, never `lock_at`.** A round still
locks at `release - 48h`, but that is the arena's clock, not the participant's:
under the batch rule they answer everything by one Monday 12:00Z and the null
freezes at the same instant. Showing `lock_at` as the deadline would tell an
entrant they had up to seven more days than they do, and would re-open exactly
the unequal-vantage problem the batch cutover closed. `horizon_days` is
reported per round so the difference is visible rather than hidden.

**Every question carries `cells` and `items`, even when empty.** The bundle
mixes three shapes -- a scalar topline, a 16-cell population profile, an
ordered list -- and the natural encoding would be to include a key only for
the shape that has one. That pushes a `KeyError` into every consumer that
iterates the batch, which is the most common way a mixed-shape payload breaks
its reader. So both keys are always lists, `target_type` is the field that says
which shape a question is, and an empty list means "this shape carries none".

An empty `items` on a `ranking_list` round is not a degenerate case either: the
Wikipedia top-10 round is drawn from every article on the wiki, and naming a
candidate list would make it a different and much easier question
(`ranking_round.spec_for` refuses one). A closed-set ranking -- the five-query
Trends basket -- names its items and they appear here.

**The bundle is frozen by its hash, not by a field inside it.** `canonical` is
the repository's one serialization (`sort_keys`, no whitespace, UTF-8) and
`digest` hashes it. Embedding the digest in the payload would make the payload
unable to hash itself, so the emitter prints it and the operator records it.
"""
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

from . import batches, ranking_round

SCHEMA_VERSION = "1.0.0"

# Fields copied straight off a reviewed round, in the order they are written.
# `release_estimated` is deliberately not among them: a bundle states when the
# publisher is expected to release, and whether the arena inferred that date is
# an operator concern that belongs on the site, not a field a forecaster acts
# on.
_COPIED = ("round_id", "tracker", "series", "question", "unit", "target_type",
           "release_at", "lock_at")


def next_deadline(now=None):
    """The deadline of the batch a bundle emitted now would cover.

    The first Monday 12:00Z strictly after `now`, but never earlier than the
    cutover: rounds whose deadline predates `batches.FIRST_DEADLINE` were
    bought and scored under the per-round lock rule, and bundling them would
    hand a participant a deadline their round was never governed by.

    Strictly after, because a bundle emitted at 12:00Z on a Monday is for the
    week that starts then. The deadline that instant belongs to has closed.
    """
    now = now or datetime.now(timezone.utc)
    days = (batches.BATCH_WEEKDAY - now.weekday()) % 7
    nxt = (now + timedelta(days=days)).replace(
        hour=batches.BATCH_HOUR_UTC, minute=0, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=7)
    return max(nxt, batches.FIRST_DEADLINE)


def rounds_in(rounds, batch_id):
    """The reviewed rounds governed by `batch_id`, in lock order.

    Membership is asked of `batches.batch_of`, never recomputed here. Two
    implementations of the same calendar is how a round ends up in one batch
    on the site and another in the bundle.
    """
    got = [r for r in rounds if batches.batch_of(r["lock_at"]) == batch_id]
    return sorted(got, key=lambda r: (r["lock_at"], r["round_id"]))


def question(r):
    """One reviewed round as a bundle question."""
    out = {k: r[k] for k in _COPIED}
    out["horizon_days"] = round(batches.horizon_days(r["lock_at"]), 1)
    out["resolve"] = r["resolve"]
    out["cells"] = list(r.get("cells") or [])
    # A ranking round's item set comes from the validated spec rather than
    # from the raw block, so a closed-set round that names its items in a
    # spelling the scorer would reject fails here instead of in front of an
    # entrant.
    items = []
    if ranking_round.is_ranking(r):
        items = list(ranking_round.spec_for(r).get("items") or [])
    out["items"] = items
    return out


def build(rounds, deadline):
    """The bundle for one deadline, as the dict that gets serialized."""
    batch_id = "batch-" + deadline.strftime("%Y-%m-%d")
    return {
        "schema_version": SCHEMA_VERSION,
        "batch_id": batch_id,
        "deadline": deadline.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "published_at": (deadline - batches.PUBLISH_LEAD).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "questions": [question(r) for r in rounds_in(rounds, batch_id)],
    }


def canonical(obj):
    """The repository's one serialization, as bytes. Do not vary it.

    Identical to what `locks/` and the leaderboard hash with. A bundle whose
    digest was computed under different separators is not comparable to any
    other hash this project has ever published.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(obj):
    """sha256 of `canonical(obj)` -- what freezes a published bundle."""
    return hashlib.sha256(canonical(obj)).hexdigest()


def load_season(root):
    """The reviewed rounds. Accepts both season-file shapes."""
    with open(os.path.join(root, "questions", "season0.json")) as fh:
        season = json.load(fh)
    return season["rounds"] if isinstance(season, dict) else season
