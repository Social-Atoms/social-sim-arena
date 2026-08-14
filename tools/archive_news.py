"""Pull the whole news corpus onto disk, once, so no run ever waits on it.

The news condition needs, for every historical release, the Current Events
pages as they read at that release's lock. Fetched lazily that is thousands of
requests spread through a backtest, redone whenever the set of series changes,
and dependent on Wikipedia being reachable at the moment a run happens. Fetched
ahead of time it is one bounded pass that any later run reads from disk.

The unit of storage is the *revision*, not the lock -- see `newsdigest.DAYS`.
That is what makes one pass enough: a day archived now answers every lock that
will ever read it, including locks for series that do not exist yet.

    python tools/archive_news.py                    # plan only, fetches nothing
    python tools/archive_news.py --execute          # the pass itself
    python tools/archive_news.py --execute --start 2025-01-01 --end 2026-08-13

Free and keyless, but it is a favour Wikipedia is doing us: requests are
serialised and spaced by `newsdigest.MIN_INTERVAL`, so the pass takes tens of
minutes and should be run once rather than casually. It resumes exactly where
it stopped, so an interruption costs nothing.
"""
import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa.adapters import newsdigest as nd

DEFAULT_START = "2025-01-01"      # the poll history the backtest can reach


def days_between(start, end):
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    return [a + timedelta(days=i) for i in range((b - a).days + 1)]


def state(d, window):
    """What one day still needs: 'done', 'open' (window not closed yet), or
    'todo'. 'open' days are archived as far as they can be and revisited."""
    body = nd.load_day(d)
    if not body or not body.get("fetched_at"):
        return "todo"
    if body["fetched_at"] < nd.archive_asofs(d, window)[-1]:
        return "open"
    return "done"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end", default=None,
                    help="default: yesterday, since today's page is mid-write")
    ap.add_argument("--window", type=int, default=nd.ARCHIVE_WINDOW_DAYS,
                    help="how many days of locks each page must answer")
    ap.add_argument("--execute", action="store_true",
                    help="actually fetch; without it this only prints the plan")
    ap.add_argument("--refresh", action="store_true",
                    help="refetch indexes already stored (rarely needed)")
    args = ap.parse_args()

    end = args.end or (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    days = days_between(args.start, end)
    todo = [d for d in days if args.refresh or state(d, args.window) != "done"]
    done = len(days) - len(todo)

    print(f"span      {args.start} .. {end}  ({len(days)} days)")
    print(f"archived  {done} complete, {len(todo)} to fetch")
    if not args.execute:
        # Two requests a day: one for the revision index, one for the content
        # of every revision the window resolves to, batched. A round trip to
        # the API measured ~2.8s, well above MIN_INTERVAL, so the wall clock is
        # latency and not our own spacing.
        est, per = len(todo) * 2, 2.8
        print(f"estimate  ~{est} requests, ~{est * per / 60:.0f} min "
              f"at ~{per}s a round trip")
        print(f"store     {nd.DAYS}")
        print("\nnothing fetched. re-run with --execute")
        return

    t0 = time.monotonic()
    calls = revs = missing = 0
    for i, d in enumerate(todo, 1):
        try:
            body, n = nd.archive_day(d, args.window, refresh=args.refresh)
        except Exception as e:               # noqa: BLE001 - report and continue
            print(f"  {d} FAILED {type(e).__name__}: {e}", flush=True)
            continue
        calls += n
        revs += len(body["revisions"])
        if body.get("missing"):
            missing += 1
        if i % 25 == 0 or i == len(todo):
            el = time.monotonic() - t0
            rate = i / el if el else 0
            left = (len(todo) - i) / rate / 60 if rate else 0
            print(f"  {i}/{len(todo)}  {d}  {calls} requests  "
                  f"{el / 60:.1f}m elapsed, ~{left:.0f}m left", flush=True)

    open_now = sum(1 for d in days if state(d, args.window) == "open")
    print(f"\ndone      {calls} requests, {revs} revisions stored")
    if missing:
        print(f"missing   {missing} days had no page at all")
    if open_now:
        print(f"open      {open_now} days are too recent to finish; "
              f"re-run after their {args.window}-day window closes")
    size = sum(os.path.getsize(os.path.join(nd.DAYS, f))
               for f in os.listdir(nd.DAYS)) if os.path.isdir(nd.DAYS) else 0
    print(f"store     {nd.DAYS}  {size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
