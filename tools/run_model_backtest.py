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

from ssa import cutoffs, envfile, harness, model_backtest, refresh          # noqa: E402
from ssa.adapters import fredcsv, votehub                          # noqa: E402

envfile.load()   # local .env; CI passes secrets in the environment

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "backtest", "model_backtest.json")

# Series with a question template and enough history to warm up on.
SERIES = ["umich_sentiment", "yougov_approval", "mc_approval", "yougov_generic_margin"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true",
                    help="actually call the providers (default: plan only)")
    ap.add_argument("--entrants", default=",".join(harness.MODELS),
                    help="comma-separated entrant ids")
    ap.add_argument("--start", help="override the first release date (ISO day)")
    ap.add_argument("--end", help="last release date (ISO day)")
    ap.add_argument("--margin-days", type=int, default=cutoffs.MARGIN_DAYS)
    ap.add_argument("--on-unknown", default="raise",
                    help="'raise', 'exclude', or an ISO day to assume")
    ap.add_argument("--limit", type=int,
                    help="cap releases per entrant (smoke test)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    entrants = [e.strip() for e in args.entrants.split(",") if e.strip()]
    unknown = [e for e in entrants if e not in harness.MODELS]
    if unknown:
        sys.exit(f"unknown entrant(s): {', '.join(unknown)}")

    print("cutoffs")
    for e in entrants:
        print("  " + cutoffs.describe(e))

    if args.on_unknown == "exclude":
        entrants, dropped = cutoffs.partition(entrants, args.margin_days)
        if dropped:
            print(f"\ndropped (no credible cutoff, so no defensible window): "
                  f"{', '.join(dropped)}")
        if not entrants:
            sys.exit("every entrant was dropped")

    start = args.start or cutoffs.common_start(
        entrants, args.margin_days, on_unknown=args.on_unknown)
    print(f"\nscoring window starts {start}"
          + (" (overridden)" if args.start else " = latest cutoff + margin"))

    approval = votehub.approval_polls()
    generic = votehub.generic_ballot_polls()
    umich = fredcsv.umich_sentiment()
    series = refresh.build_series(approval, generic, umich)
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

    if not args.execute:
        print("\ndry run -- nothing called. Add --execute to run.")
        return

    missing = [e for e in entrants if not harness.has_key(e)]
    if missing:
        sys.exit(f"\nno API key for: {', '.join(missing)}. "
                 "The backtest never files mock forecasts, so it stops here "
                 "rather than write placeholders into a paper table.")

    print(f"\nrunning with {args.workers} workers...")
    records = model_backtest.execute(tasks, workers=args.workers,
                                     progress=max(10, len(tasks) // 20))
    result = model_backtest.score(records, series_map)
    result["start"] = start
    result["entrants"] = entrants
    result["calls"] = {"total": len(tasks), "made": todo}

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
