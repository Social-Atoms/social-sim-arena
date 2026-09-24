"""Resolve rounds against the value that actually got published.

This is the other half of the loop. Forecasts have been filed since the season
opened and nothing has ever scored them, so `resolutions/resolved.json` is `{}`
and the live leaderboard is permanently empty no matter how long the cron runs.

**What counts as the answer.** A round asks for the *next scheduled release* of
a tracker. The answer is the first observation that was not in the history the
round's nulls saw, read from the lock snapshot refresh froze while the round
was open. Nothing here reads a number a human typed in.

Dates cannot do this job. Michigan's August value is dated 2026-08-01 -- its
month label -- and published on the 14th, so against a 2026-08-12 lock it sorts
*before* the lock while not existing at lock time. Anchoring on dates would
both hand the August round September's value and let that same August value
into the persistence baseline it is scored against. Only observation time
separates "existed at lock" from "labelled before lock", which is what the
snapshot records.

**Why it refuses more often than it resolves.** A resolution is the scoring
authority: once written it fixes every entrant's score for that round, and a
wrong one is worse than a missing one because it looks finished. So every
check below is a refusal, not a repair:

- the release time has to have passed;
- the series has to exist and have had history before the lock;
- there has to be an observation the frozen history did not contain;
- an existing resolution is never overwritten, even if it disagrees.

Anything that fails is reported with its reason and left for a human.

**Revisions count as releases.** Michigan publishes a preliminary mid-month
and a final at month end, and both revise the same monthly row. Comparing by
date alone, the final round would see nothing new and never resolve. Comparing
by (date, value), the preliminary round resolves against the value published on
the 14th, and the final round -- whose own snapshot already holds that
preliminary -- resolves against the revision. One row, two questions, two
answers. Two rounds still cannot claim the *same* (date, value), so a round
that would duplicate another's answer is refused by name.

Usage:
    python -m ssa.resolve              # report what would resolve, write nothing
    python -m ssa.resolve --write      # write resolutions/resolved.json
"""
import json
import os
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUESTIONS = os.path.join(ROOT, "questions", "season0.json")
RESOLVED = os.path.join(ROOT, "resolutions", "resolved.json")


def _parse(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def load_resolved(path=RESOLVED):
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def pre_lock_history(r, series):
    """What the round's nulls actually saw, from the frozen lock snapshot.

    Read from the snapshot rather than recomputed, and identical to what
    refresh.build_rounds uses for a locked round. If the two ever diverge,
    entrants get scored against an answer their own baselines could have seen,
    and the skill numbers stop meaning anything.

    Falls back to the date filter only for rounds that locked before snapshots
    existed; that path cannot tell a monthly value's label date from its
    publication date, which is the whole reason snapshots exist. The filter is
    `batches.freeze_at`, which returns the lock itself for exactly those
    pre-cutover rounds -- so the documented path is unchanged, and a
    post-cutover round that somehow lost its snapshot falls back to the
    boundary its null actually used rather than one up to a week later.
    """
    from . import batches, refresh
    snap = refresh.read_lock_snapshot(r["round_id"])
    if snap and snap.get("history"):
        return snap["history"]
    freeze_date = batches.freeze_at(r["lock_at"]).strftime("%Y-%m-%d")
    return [p for p in (series.get(r["series"]) or [])
            if p["date"] < freeze_date]


# Rounds whose frozen history is known not to hold what was public at the
# lock, so "the first release after the freeze" is not their answer. Named one
# by one, because the general test for it -- two or more new periods dated
# before the lock -- also matches Morning Consult rounds that resolved
# correctly: Silver Bulletin files MC polls days late, so a week can carry two
# points fielded before a lock and published after it.
HAND_ONLY = {
    # Frozen through a filter that read only the `Voters` label, so its history
    # ends 2026-08-29; the 09-06 and 09-12 RV waves were already out under `All
    # polls` before the 09-20 lock. With the filter repaired the first point
    # after the freeze is 09-06 = 37, published 09-09, and not the 09-19 wave
    # the round asked about.
    "yougov-2026-w39-rv-approval": (
        "frozen history ends 2026-08-29 but the 09-06 and 09-12 RV waves were "
        "published before the 09-20 lock under a label the snapshot's filter "
        "did not read; the first point after the freeze was public before "
        "anyone answered. Resolve by hand or withdraw"),
}


def candidate(r, series):
    """(point, reason). The next release after the freeze, or why not.

    `point` is None whenever anything is unclear; `reason` always explains.
    """
    if r.get("round_id") in HAND_ONLY:
        return None, HAND_ONLY[r["round_id"]]
    points = series.get(r["series"])
    if not points:
        # Civiqs publishes only a JS dashboard, and Silver Bulletin has carried
        # three of its polls since May 2025 -- not a series. The House seat
        # count has no tracker at all; it resolves from certified results. Both
        # say so in their own `resolve` field, so a hand-written entry in
        # resolved.json is the intended path and this refusal is not a failure.
        return None, (f"no series '{r['series']}' in the pipeline; this round "
                      "resolves by hand, see its `resolve` field")
    hist = pre_lock_history(r, series)
    if not hist:
        return None, (f"no frozen history for '{r['series']}': the round locked "
                      "before snapshots existed and its answer cannot be "
                      "identified from dates alone")
    # The answer is the first observation the frozen history did not contain,
    # compared by (date, value) rather than date alone.
    #
    # A revision is a release. Michigan publishes a preliminary mid-month and a
    # final at month end, and both revise the same monthly row: 2026-08-01
    # carries the preliminary on the 14th and the final on the 28th. Keyed on
    # date, the final round would see nothing new and never resolve; keyed on
    # the pair, the preliminary round resolves against the value published on
    # the 14th and the final round -- whose own snapshot already holds that
    # preliminary -- resolves against the revision. Two questions, two answers,
    # from one row.
    seen = {(p["date"], p["value"]) for p in hist}
    first_frozen_date = hist[-1]["date"]
    after = [p for p in points
             if (p["date"], p["value"]) not in seen
             and p["date"] >= first_frozen_date]
    # A new period outranks a revision of an old one. Both are "releases the
    # frozen history does not contain", and until 2026-09-22 the earlier of
    # them won on date order -- which was right while a vintage only ever
    # revised the month it also reported.
    #
    # Michigan's September party addenda does both: it adds 2026-09-01 and
    # restates 2026-08-01 from 39.1 to 40.2. The three
    # `umich-party-2026-09-*` rounds ask for "September 2026 preliminary", and
    # taking the earlier candidate answered them with the revised August
    # number instead -- three wrong resolutions that would have looked
    # finished, which `resolve.py` exists to refuse.
    #
    # The preliminary/final pair this rule was written for is untouched: a
    # final round's snapshot already holds the preliminary for its own month
    # and no later month exists yet, so there is nothing strictly later and
    # the revision is still what it resolves against.
    later = [p for p in after if p["date"] > first_frozen_date]
    if later:
        after = later
    if not after:
        # Name the source and the day it stopped. The bare version of this
        # message stood in the six-hourly report for a fortnight over rounds
        # nobody could act on without first going to look up which series they
        # meant and whether it had moved -- so nobody did. A report that costs
        # a lookup to read does not get read.
        newest = max(p["date"] for p in points)
        return None, (
            f"'{r['series']}' has not published since the lock: its newest "
            f"observation is {newest} and the frozen history already ends at "
            f"{first_frozen_date}")
    return after[0], None


# The two refusals that mean "another resolver owns this round", as opposed to
# "this round is stuck". They are constants because `main` has to tell the two
# apart to report them differently, and matching on the shape of a sentence is
# not a contract. `resolve_round` returns these verbatim.
ELSEWHERE_RANKING = ("ranking round: resolved as an ordered list by "
                     "refresh.build_ranking_leaderboard, not as a scalar")
ELSEWHERE_PROFILE = ("profile round: resolved as a cell vector by "
                     "refresh.build_profile_leaderboard, not as a scalar")
HANDLED_ELSEWHERE = (ELSEWHERE_RANKING, ELSEWHERE_PROFILE)


def handled_elsewhere(why):
    """True when a skip means another resolver owns the round, not that it is
    blocked. Eight of the fourteen skips on 2026-09-22 were these, and mixing
    them into one list is why the six that needed a person went unread for two
    weeks."""
    return why in HANDLED_ELSEWHERE


def resolve_round(r, series, now):
    """(resolution, reason). Resolution is None unless every check passes."""
    if now < _parse(r["release_at"]):
        return None, f"release_at {r['release_at'][:16]} has not passed"
    from . import profile_round, ranking_round
    if ranking_round.is_ranking(r):
        # A ranking round's answer is an ordered list, and this function only
        # knows how to produce a scalar. The refusal matters for a second reason
        # here: a ranking round names a `series` that is not in the registry at
        # all (its target is a list, and the registry holds scalar series), so
        # without this it would take the "no series in the pipeline" path and be
        # reported as a round awaiting a human -- every week, for every ranking
        # round, drowning the reports that do need one.
        return None, ELSEWHERE_RANKING
    if profile_round.is_profile(r):
        # A profile round's answer is a sixteen-cell vector, and this function
        # only knows how to produce a scalar. Refusing here is not tidiness:
        # a profile round names an anchor `series` it shares with the scalar
        # rounds on the same tracker, so without this the first of them past
        # its release would claim the other's observation -- writing a single
        # number as the profile's resolution, and blocking the scalar round
        # that the number actually answers. `refresh.build_profile_leaderboard`
        # reads the vector from the cells' own series instead.
        return None, ELSEWHERE_PROFILE
    point, why = candidate(r, series)
    if point is None:
        return None, why
    return {
        "value": point["value"],
        "observed_date": point["date"],
        "series": r["series"],
        "method": ("first point in the series after the pre-lock history, "
                   "the same freeze the round's baselines used"),
        "resolved_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, None


def resolve_all(season, series, resolved, now):
    """(new_resolutions, skipped) for every round past its release.

    Two rounds are never allowed to claim the same observation: Michigan's
    preliminary and final both ask about one month while the series carries one
    point for it, and scoring both against it would answer two questions with
    one number.
    """
    new, skipped, claimed = {}, [], {}
    for rd in season["rounds"]:
        rid = rd["round_id"]
        if rid in resolved:
            continue                      # never overwritten
        if now < _parse(rd["release_at"]):
            continue                      # not due; not a problem
        res, why = resolve_round(rd, series, now)
        if res is None:
            skipped.append((rid, why))
            continue
        # Keyed on (series, date, value): a revision to the same row is a
        # different observation, which is exactly how Michigan's preliminary
        # and final stay distinct while sharing a date.
        key = (rd["series"], res["observed_date"], res["value"])
        owner = claimed.get(key) or next(
            (o for o, v in resolved.items()
             if v.get("series") == rd["series"]
             and v.get("observed_date") == res["observed_date"]
             and v.get("value") == res["value"]), None)
        if owner:
            skipped.append((rid, f"observation {res['observed_date']}={res['value']} "
                                 f"is already the resolution for {owner}; two "
                                 "rounds cannot share one answer"))
            continue
        claimed[key] = rid
        new[rid] = res
    return new, skipped


def write(resolved, new, path=RESOLVED):
    """Merge and write. Existing entries win; this only ever adds."""
    merged = dict(resolved)
    for k, v in new.items():
        merged.setdefault(k, v)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(merged, f, indent=2, sort_keys=True)
        f.write("\n")
    return merged


def main(argv=None):
    import sys
    argv = sys.argv[1:] if argv is None else argv
    do_write = "--write" in argv

    from . import envfile, refresh, series as series_registry
    from .adapters import silverbulletin as sb
    envfile.load()
    now = refresh.now_utc()

    sources = {
        "sb_approval": sb.fetch(sb.APPROVAL_URL),
        "sb_generic": sb.fetch(sb.GENERIC_URL),
        "umich": series_registry.michigan_history(),
    }
    series = refresh.build_series(
        sb.approval_polls(rows=sources["sb_approval"]),
        sb.generic_ballot_polls(rows=sources["sb_generic"]),
        sources["umich"], sources)

    with open(QUESTIONS) as f:
        season = json.load(f)
    resolved = load_resolved()

    new, skipped = resolve_all(season, series, resolved, now)

    print(f"already resolved: {len(resolved)}")
    if new:
        print(f"\nresolving {len(new)}:")
        for rid, res in sorted(new.items()):
            print(f"  {rid:32s} = {res['value']}  "
                  f"(observed {res['observed_date']}, {res['series']})")
    else:
        print("\nnothing new to resolve")
    stuck = [(rid, why) for rid, why in skipped if not handled_elsewhere(why)]
    elsewhere = [rid for rid, why in skipped if handled_elsewhere(why)]
    if stuck:
        print(f"\nNEEDS ATTENTION ({len(stuck)}) -- past release and not "
              f"resolvable from the pipeline:")
        for rid, why in stuck:
            print(f"  {rid:32s} {why}")
    else:
        print("\nnothing is stuck")
    if elsewhere:
        # Counted, not listed. These are ranking and profile rounds, which
        # `refresh.build_ranking_leaderboard` and
        # `refresh.build_profile_leaderboard` resolve; the scalar resolver was
        # never going to. Printing them one per line put eight lines of noise
        # above six lines of work.
        print(f"\nresolved by another resolver, not by this one "
              f"({len(elsewhere)}): {', '.join(sorted(elsewhere))}")

    if new and do_write:
        merged = write(resolved, new)
        print(f"\nwrote {os.path.relpath(RESOLVED, ROOT)} -- {len(merged)} total")
    elif new:
        print("\ndry run: pass --write to record these")


if __name__ == "__main__":
    main()
