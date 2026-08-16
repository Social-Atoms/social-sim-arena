"""Run the LLM backtest and write backtest/model_backtest.json.

Dry-run by default -- it prints the release window, the call count, and a cost
estimate, and touches no provider. Nothing bills until --execute.

  python tools/run_model_backtest.py                      # plan + cost only
  python tools/run_model_backtest.py --limit 3            # tiny smoke test plan
  python tools/run_model_backtest.py --execute --workers 6
  python tools/run_model_backtest.py --execute --entrants deepseek,qwen

The window starts at the latest training cutoff across the entrants being
scored (plus a margin), because that is the only stretch of history none of
them could have memorized. Narrowing the entrant set widens the window, which
is the whole reason --entrants exists: a run without the most recently trained
model gets meaningfully more releases to score on.

Re-running is free for anything already in cache/model_backtest/, so an
interrupted run resumes and a rerun after adding one entrant only pays for that
entrant.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa import cutoffs, envfile, harness, model_backtest, refresh
from ssa import series as series_registry          # noqa: E402

envfile.load()   # local .env; CI passes secrets in the environment

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "backtest", "model_backtest.json")

# Every registered series. Taken from the registry rather than listed here, so
# adding a tracker to ssa/series.py puts it in the backtest automatically
# instead of leaving a second list to fall out of date.
SERIES = list(series_registry.SERIES)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-spend", type=float, default=25.0,
                    help="refuse to start if the estimate exceeds this many "
                         "dollars (default 25). The estimate is advice; this "
                         "is the brake.")
    ap.add_argument("--execute", action="store_true",
                    help="actually call the providers (default: plan only)")
    ap.add_argument("--rescore", action="store_true",
                    help="rebuild the table from the committed cache; makes no "
                         "provider calls and bills nothing")
    ap.add_argument("--entrants",
                    default=",".join(e for e, *_ in harness.season_entrants()),
                    help="comma-separated entrant ids; defaults to every model "
                         "in both information conditions")
    ap.add_argument("--start", help="override the first release date (ISO day)")
    ap.add_argument("--end", help="last release date (ISO day)")
    ap.add_argument("--margin-days", type=int, default=cutoffs.MARGIN_DAYS)
    ap.add_argument("--on-unknown", default="raise",
                    help="'raise', 'exclude', or an ISO day to assume")
    ap.add_argument("--limit", type=int,
                    help="cap releases per entrant (smoke test)")
    ap.add_argument("--common-window", action="store_true",
                    help="score every entrant on one shared window instead of "
                         "each on its own post-cutoff stretch")
    ap.add_argument("--workers", type=int, default=20)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    entrants = [e.strip() for e in args.entrants.split(",") if e.strip()]
    try:
        models = {e: harness.resolve(e)[0] for e in entrants}
    except KeyError as e:
        sys.exit(f"unknown entrant: {e}")

    print("cutoffs (per model; both conditions share one)")
    for m in sorted(set(models.values())):
        print("  " + cutoffs.describe(m))

    if args.on_unknown == "exclude":
        keep, dropped = cutoffs.partition(sorted(set(models.values())),
                                          args.margin_days)
        entrants = [e for e in entrants if models[e] in keep]
        if dropped:
            print(f"\ndropped (no credible cutoff, so no defensible window): "
                  f"{', '.join(dropped)}")
        if not entrants:
            sys.exit("every entrant was dropped")

    if args.start:
        start = args.start
    elif args.common_window:
        start = cutoffs.common_start(sorted(set(models.values())),
                                     args.margin_days,
                                     on_unknown=args.on_unknown)
    else:
        # Each entrant on its own post-cutoff window by default. A shared
        # window is bounded by the most recently trained model and throws away
        # most of the history the older ones could legitimately be scored on.
        # Keyed by entrant id, but the cutoff belongs to the model: both
        # conditions of one model share its training boundary.
        start = {e: cutoffs.usable_start(models[e], args.margin_days)
                 for e in entrants}
    if isinstance(start, dict):
        print("\nscoring window: each entrant from its own cutoff + margin")
    else:
        print(f"\nscoring window starts {start}"
              + (" (overridden)" if args.start else " = latest cutoff + margin"))

    # **Restore the committed evidence into the cache before anything is
    # planned or priced.** The per-call cache is gitignored -- one file per
    # provider call is right for resuming a run and wrong for a repository --
    # and the same replies are committed, consolidated, under backtest/runs/.
    # But nothing here ever read them back, so a fresh clone saw an empty
    # cache, priced the whole plan at full rate, and re-bought 2,887 replies
    # that were sitting in the repo it had just downloaded. Every collaborator
    # paid for the run again, and the estimate printed below agreed with them.
    #
    # Restoring is free, local, and idempotent, so it happens unconditionally
    # rather than behind a flag nobody knew to pass.
    counts = model_backtest.restore_runs()
    if counts["restored"]:
        print(f"restored {counts['restored']} previously-paid replies from "
              f"{counts['files']} committed run file(s) "
              f"({counts['successful']} successful, {counts['failures']} "
              "failures); these will not be re-billed")

    series = series_registry.build_all()
    series_map = {k: series[k] for k in SERIES if k in series}

    tasks = model_backtest.plan(series_map, entrants, start, end=args.end)
    if args.limit:
        seen, capped = {}, []
        for t in tasks:
            k = t["entrant"]
            if seen.get(k, 0) >= args.limit:
                continue
            seen[k] = seen.get(k, 0) + 1
            capped.append(t)
        print(f"\n--limit {args.limit}: {len(tasks)} -> {len(capped)} calls "
              "(smoke test, NOT a citable number)")
        tasks = capped

    if not tasks:
        sys.exit("\nno releases in the window -- every series ends before "
                 f"{start}. Check data freshness, or pass --start to override.")

    dates = sorted({t["date"] for t in tasks})
    per_series = {}
    for t in tasks:
        per_series[t["series"]] = per_series.get(t["series"], 0) + 1
    todo = sum(1 for t in tasks if not t["cached"])
    usd, per_entrant = model_backtest.estimate_cost(tasks)

    print(f"\nreleases {dates[0]} .. {dates[-1]}  ({len(dates)} dates)")
    for s in sorted(per_series):
        n = per_series[s] // max(len(entrants), 1)
        print(f"  {s:24s} {n:4d} releases x {len(entrants)} entrants")
    print(f"\ncalls {len(tasks)} total, {len(tasks) - todo} cached, {todo} to make")
    print(f"estimated cost ${usd:.2f}" + ("" if todo else " (fully cached)"))
    for e in sorted(per_entrant, key=lambda k: -per_entrant[k]):
        print(f"  {e:14s} ${per_entrant[e]:6.2f}")

    # A ceiling, because the estimate above is advice and this is a brake.
    # An unguarded --execute is one typo in --entrants or --start away from a
    # three-figure bill, and the failure is silent: it looks exactly like a
    # correct run until the invoice arrives.
    if args.execute and not args.rescore and usd > args.max_spend:
        sys.exit(
            f"\nestimated ${usd:.2f} exceeds the ${args.max_spend:.2f} "
            "ceiling, so nothing was called.\n"
            "  --rescore                rebuild the table from the cache, free\n"
            "  --limit N                smoke-test N releases per entrant\n"
            "  --entrants a,b           narrow the roster\n"
            f"  --max-spend {usd:.0f}{' ' * max(0, 12 - len(f'{usd:.0f}'))}"
            "run it anyway, having read the number")

    if args.rescore:
        # Scoring changed, the replies did not. Rebuilding from the cache keeps
        # the published table a function of the committed run rather than of a
        # second, differently-sampled one -- the models run at their providers'
        # default temperature, so re-calling would not reproduce it anyway.
        records, uncached = model_backtest.replay(tasks)
        if not records:
            sys.exit("\nnothing cached for this plan -- the prompt or the "
                     "entrant set has changed, so there is nothing to rescore.")
        print(f"\nrescoring {len(records)} cached calls, no provider contacted"
              + (f" ({uncached} planned calls were never made)" if uncached else ""))
        made = 0
    elif not args.execute:
        print("\ndry run -- nothing called. Add --execute to run, "
              "or --rescore to rebuild the table from the cache for free.")
        return
    else:
        missing = sorted({m for m in models.values() if not harness.has_key(m)})
        if missing:
            sys.exit(f"\nno API key for: {', '.join(missing)}. "
                     "The backtest never files mock forecasts, so it stops here "
                     "rather than write placeholders into a paper table.")

        print(f"\nrunning with {args.workers} workers...")
        records = model_backtest.execute(tasks, workers=args.workers,
                                         progress=max(10, len(tasks) // 20))
        made = todo

    result = model_backtest.score(records, series_map)
    result["start"] = start
    result["entrants"] = entrants
    result["calls"] = {"total": len(tasks), "made": made}
    if args.rescore:
        # A rescore made no calls; overwriting the count with 0 would erase the
        # record of what the run actually cost. Keep the original.
        prev = {}
        if os.path.exists(args.out):
            with open(args.out) as f:
                prev = json.load(f)
        result["calls"] = prev.get("calls", {"total": len(tasks)})
        result["rescored_from_cache"] = True

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(result, f, indent=2, sort_keys=True)
        f.write("\n")

    print(f"\nwrote {os.path.relpath(args.out, ROOT)}")
    print(f"scored {result['matched_releases']} releases answered by all entrants "
          f"(of {result['releases']} in window)")
    if result["failures"]:
        print("failures (excluded from scoring): "
              + ", ".join(f"{k}={v}" for k, v in sorted(result["failures"].items())))
    print(f"\n{'entrant':16s} {'n':>4s} {'CRPS':>8s} {'skill':>8s}")
    for row in result["matched"]:
        tag = " (baseline)" if row.get("baseline") else ""
        print(f"{row['entrant']:16s} {row['rounds']:4d} "
              f"{row['mean_crps']:8.3f} {row['mean_skill']:8.3f}{tag}")


if __name__ == "__main__":
    main()
