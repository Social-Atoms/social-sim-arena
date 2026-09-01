"""Is each source still answering, and does the arena still know the answer?

A pipeline that fetches on a schedule has two failure modes and they need
opposite handling. A **flake** -- one dropped connection, one 500 -- must not
cost a run; the next one in six hours fixes it. An **outage** must be loud
immediately, because everything downstream keeps working perfectly while
publishing numbers that stopped moving.

Nothing here distinguished them. A fetch failure printed a line and the run
carried on serving the archive, which is right for a flake and silently wrong
for an outage: the site renders, the leaderboard updates, `generated_at` ticks
forward, and the underlying series has not changed in a week.

That is not hypothetical. Civiqs began returning 403 to the GitHub Actions
runners on 2026-08-14 -- verified from a runner on 2026-08-16 as an IP-range
block, since a complete Chrome header fingerprint is refused too while Michigan
and Silver Bulletin both answer 200. The archive kept serving. Each run printed
"serving the archive, which is now a day behind" and moved on. It was three
days behind before anyone counted.

**The budget is per source, because staleness means different things.** Civiqs
publishes daily, so two days without a change is a problem. Michigan publishes
twice a month, so forty days without one is normal. One global threshold is
either useless or noisy, so each source declares its own and the check is
against that.

**Two clocks, not one.** `fetched_at` answers "did the request work"; and
`changed_at` answers "is the upstream still moving". A source can answer every
request with a body that has not changed in a month -- FRED does exactly this
for a month at a time -- and only the second clock sees it.
"""
from datetime import datetime, timezone

from . import provenance

# (max days since a successful fetch, max days since the content last moved).
#
# The first is about us: our request stopped working. The second is about them:
# they stopped publishing, or we are being served a cache. Both are generous
# against the source's own cadence rather than tight, because a check that
# cries wolf gets muted and then it is worse than nothing.
BUDGETS = {
    # Daily model output. A weekend gap is normal; three days is not.
    "civiqs": (2, 4),
    # Poll aggregation, updated most days. New polls arrive constantly, so a
    # week without any change means the sheet is frozen, not that nobody polled.
    "sb_approval": (2, 7),
    "sb_generic": (2, 10),
    # Twice a month: preliminary mid-month, final at month end. Forty days
    # covers a late release plus a holiday without complaining.
    "umich": (3, 40),
    # Weekly, published Thursdays. Twelve days is a release plus a holiday
    # skip; the adapter itself refuses a page whose newest row is over 21
    # days old, so `stale` here fires before the fetch starts failing loudly.
    "aaii": (3, 12),
}
DEFAULT_BUDGET = (3, 30)
CIVIQS_WEEKLY_CHANGE_BUDGET_DAYS = 10


def _age_days(stamp, now):
    if not stamp:
        return None
    then = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (now - then).total_seconds() / 86400.0


def civiqs_rows(now=None, archive=None):
    """Civiqs judged from its own archive, which is its provenance.

    It does not go through `provenance.record`: its snapshots are already one
    immutable dated file per (tracker, filters, day), and the raw page is 2 MB
    of HTML that would be stored twice. So the newest file's date is the fetch
    clock, and the newest *reading inside* it is the change clock -- which is
    the pair that matters, because the run keeps serving the archive when the
    fetch fails and the two then drift apart silently.
    """
    import glob
    import json as _json
    import os as _os
    now = now or datetime.now(timezone.utc)
    root = archive or _os.path.join(
        _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "civiqs")
    out = []
    for d in sorted(glob.glob(_os.path.join(root, "*"))):
        if not _os.path.isdir(d) or _os.path.basename(d).startswith("_"):
            continue
        files = sorted(glob.glob(_os.path.join(d, "*.json")))
        if not files:
            continue
        day = _os.path.basename(files[-1])[:-5]
        try:
            snap = _json.load(open(files[-1]))
            end = snap.get("end_date")
        except Exception:                          # noqa: BLE001
            end = None
        out.append({"key": _os.path.basename(d), "vintages": len(files),
                    "newest_snapshot": day, "newest_reading": end})
    return out


def _civiqs_registered_change_budgets(default_days):
    """Archive key -> freshness budget for every registered Civiqs series."""
    # Local imports avoid making the lightweight manifest health module part
    # of the series/adapters import graph at module load time.
    from . import series as series_registry
    from .adapters import civiqs
    out = {}
    for spec in series_registry.SERIES.values():
        if spec.get("source") != "civiqs":
            continue
        cfg = spec["civiqs"]
        key = civiqs.archive_key(cfg["name"], cfg.get("filters"))
        out[key] = (CIVIQS_WEEKLY_CHANGE_BUDGET_DAYS
                    if cfg["name"] == "describe_feeling_us" else
                    default_days)
    return out


def check(now=None, manifest=None, budgets=None, civiqs_archive=None,
          civiqs_change_budgets=None):
    """One row per source: how old, against what budget, and the verdict.

    `state` is one of:
      ok       within budget on both clocks
      stale    the content has not moved for longer than the source's cadence
               explains -- they stopped, or we are being served a cache
      failing  our own fetches have stopped working
      unknown  never fetched, so there is nothing to judge

    Read from the provenance manifest rather than from live requests, so this
    costs nothing and can run anywhere, including in a test.
    """
    now = now or datetime.now(timezone.utc)
    m = manifest if manifest is not None else provenance.load_manifest()
    budgets = budgets or BUDGETS
    rows = []
    for name in sorted(set(m) | set(budgets)):
        row = m.get(name) or {}
        max_fetch, max_change = budgets.get(name, DEFAULT_BUDGET)
        fetched = _age_days(row.get("fetched_at"), now)
        changed = _age_days(row.get("changed_at"), now)
        if fetched is None:
            state = "unknown"
        elif fetched > max_fetch:
            state = "failing"
        elif changed is not None and changed > max_change:
            state = "stale"
        else:
            state = "ok"
        rows.append({"source": name, "state": state,
                     "fetched_days": None if fetched is None else round(fetched, 1),
                     "changed_days": None if changed is None else round(changed, 1),
                     "budget_fetch_days": max_fetch,
                     "budget_change_days": max_change,
                     "url": row.get("url"), "file": row.get("file")})
    # Civiqs keeps its own archive rather than a manifest row; fold it in so a
    # single table answers "is anything rotting".
    try:
        cq = civiqs_rows(now, civiqs_archive)
    except Exception:                              # noqa: BLE001
        cq = []
    if cq:
        mf, mc = (budgets or BUDGETS).get("civiqs", DEFAULT_BUDGET)
        key_budgets = (dict(civiqs_change_budgets)
                       if civiqs_change_budgets is not None else
                       _civiqs_registered_change_budgets(mc))
        by_key = {r["key"]: r for r in cq if r["key"] in key_budgets}
        missing_keys = sorted(set(key_budgets) - set(by_key))
        tracker_health = []
        for key in sorted(by_key):
            item = by_key[key]
            fetch_age = _age_days(
                item["newest_snapshot"] + "T00:00:00Z", now)
            reading = item.get("newest_reading")
            change_age = (_age_days(reading + "T00:00:00Z", now)
                          if reading else None)
            tracker_health.append({
                "key": key,
                "fetched_days": round(fetch_age, 1),
                "changed_days": (None if change_age is None else
                                 round(change_age, 1)),
                "change_budget_days": key_budgets[key],
                "newest_snapshot": item["newest_snapshot"],
                "newest_reading": reading,
            })
        failing_keys = sorted(
            item["key"] for item in tracker_health
            if item["fetched_days"] > mf)
        stale_keys = sorted(
            item["key"] for item in tracker_health
            if item["changed_days"] is None or
            item["changed_days"] > item["change_budget_days"])
        worst_fetch = max(
            tracker_health, key=lambda item: item["fetched_days"],
            default=None)
        # Report the actual age/budget pair closest to (or furthest over) its
        # limit.  Showing a weekly 7-day age against the daily 4-day budget
        # would contradict the source state even when both are correct.
        worst_change = max(
            (item for item in tracker_health
             if item["changed_days"] is not None),
            key=lambda item: (item["changed_days"] /
                              item["change_budget_days"]),
            default=None)
        for r in rows:
            if r["source"] == "civiqs":
                state = ("failing" if missing_keys or failing_keys else
                         "stale" if stale_keys else "unknown"
                         if not tracker_health else "ok")
                r.update({"fetched_days": (
                              worst_fetch["fetched_days"]
                              if worst_fetch else None),
                          "changed_days": (
                              worst_change["changed_days"]
                              if worst_change else None),
                          "budget_change_days": (
                              worst_change["change_budget_days"]
                              if worst_change else mc),
                          "trackers": len(key_budgets),
                          "tracker_health": tracker_health,
                          "newest_snapshot": max(
                              (item["newest_snapshot"]
                               for item in tracker_health), default=None),
                          "newest_reading": max(
                              (item["newest_reading"]
                               for item in tracker_health
                               if item["newest_reading"]), default=None),
                          "stale_keys": stale_keys,
                          "failing_keys": failing_keys,
                          "missing_keys": missing_keys,
                          "state": state})
    return rows


def problems(rows):
    return [r for r in rows if r["state"] in ("failing", "stale")]


def report(rows):
    """The table a run prints. Returns the lines rather than printing them, so
    a caller can put them in a commit message or a page as easily as a log."""
    out = []
    for r in rows:
        mark = {"ok": "ok", "stale": "STALE", "failing": "FAILING",
                "unknown": "never fetched"}[r["state"]]
        f = "-" if r["fetched_days"] is None else f"{r['fetched_days']:.1f}d"
        c = "-" if r["changed_days"] is None else f"{r['changed_days']:.1f}d"
        out.append(f"  {r['source']:14s} {mark:13s} fetched {f:>7s} "
                   f"(budget {r['budget_fetch_days']}d)   "
                   f"unchanged {c:>7s} (budget {r['budget_change_days']}d)")
    return out
