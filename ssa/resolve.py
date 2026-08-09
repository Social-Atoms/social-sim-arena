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

**Known ambiguity, deliberately refused.** Michigan publishes a preliminary
mid-month and a final at month end, and both rounds ask about the same month
while `umich_sentiment` carries one point per month. Resolving both against
that single point would score two different questions against one answer, so
rounds that would consume a point another round already claimed are refused
and named. Fixing it properly means a series that distinguishes the two
releases, which is a data-source change, not a scoring one.

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
    publication date, which is the whole reason snapshots exist.
    """
    from . import refresh
    snap = refresh.read_lock_snapshot(r["round_id"])
    if snap and snap.get("history"):
        return snap["history"]
    return [p for p in (series.get(r["series"]) or [])
            if p["date"] < r["lock_at"][:10]]


def candidate(r, series):
    """(point, reason). The next release after the freeze, or why not.

    `point` is None whenever anything is unclear; `reason` always explains.
    """
    points = series.get(r["series"])
    if not points:
        return None, f"no series '{r['series']}' in the pipeline"
    hist = pre_lock_history(r, series)
    if not hist:
        return None, (f"no frozen history for '{r['series']}': the round locked "
                      "before snapshots existed and its answer cannot be "
                      "identified from dates alone")
    # The answer is the first observation that was not in the frozen history.
    # Anchoring on dates instead would resolve Michigan's August round against
    # September, because the August value is dated 2026-08-01 and so sorts
    # before a 2026-08-12 lock despite not existing until the 14th.
    seen = {p["date"] for p in hist}
    after = [p for p in points if p["date"] not in seen
             and p["date"] >= hist[-1]["date"]]
    if not after:
        return None, "no release has landed in the series since the lock yet"
    return after[0], None


def resolve_round(r, series, now):
    """(resolution, reason). Resolution is None unless every check passes."""
    if now < _parse(r["release_at"]):
        return None, f"release_at {r['release_at'][:16]} has not passed"
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
        key = (rd["series"], res["observed_date"])
        owner = claimed.get(key) or next(
            (o for o, v in resolved.items()
             if v.get("series") == rd["series"]
             and v.get("observed_date") == res["observed_date"]), None)
        if owner:
            skipped.append((rid, f"observation {res['observed_date']} is already "
                                 f"the resolution for {owner}; two rounds cannot "
                                 "share one answer"))
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
    if skipped:
        print(f"\nnot resolved ({len(skipped)}), each needs a human or more time:")
        for rid, why in skipped:
            print(f"  {rid:32s} {why}")

    if new and do_write:
        merged = write(resolved, new)
        print(f"\nwrote {os.path.relpath(RESOLVED, ROOT)} -- {len(merged)} total")
    elif new:
        print("\ndry run: pass --write to record these")


if __name__ == "__main__":
    main()
