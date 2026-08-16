"""Fetch and archive every registered Civiqs tracker, from a machine that can.

Civiqs began returning 403 to the GitHub Actions runners on 2026-08-14. Verified
from a runner on 2026-08-16: all three header sets are refused, including a
complete Chrome fingerprint, while Michigan's tables and the Silver Bulletin
sheet both answer 200 from the same job. So it is the runner's IP range, and no
header, User-Agent or backoff will get past it.

That makes it a *deployment* problem, not a code one, and this is the piece the
deployment needs: the fetch, separated from the refresh, runnable anywhere.

    python tools/fetch_civiqs.py              # what it would fetch, no requests
    python tools/fetch_civiqs.py --execute    # fetch and archive

Free and keyless. Each tracker page is ~2 MB and the archive is also the fetch
cache, so a tracker already archived today costs nothing.

**Why this matters more than it looks.** Civiqs republishes its entire daily
history every night, so the number printed against a past date today is not the
number that was printed against it then. The dated snapshot under `civiqs/` is
the only record of what the dashboard actually showed, and it is what every
Civiqs resolution is checked against. A day not fetched is a day gone, and no
later run can recover it -- unlike a poll average, which can be rebuilt from the
polls.

Run it daily from somewhere Civiqs answers, and commit the result:

    python tools/fetch_civiqs.py --execute && git add civiqs && git commit ...

A self-hosted runner does the same thing without a human in the loop, which is
the better end state; this is what makes the interim survivable.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa import health, series as series_registry            # noqa: E402
from ssa.adapters import civiqs                              # noqa: E402


def registered():
    """(name, filters) for every Civiqs series the registry declares.

    Deduplicated, because two series can read one page: the national tracker
    and its party-filtered view are separate archive keys but the registry may
    name the same tracker twice.
    """
    seen, out = set(), []
    for sid, spec in sorted(series_registry.SERIES.items()):
        if spec.get("source") != "civiqs":
            continue
        cfg = spec["civiqs"]
        key = (cfg["name"], tuple(sorted((cfg.get("filters") or {}).items())))
        if key in seen:
            continue
        seen.add(key)
        out.append((sid, cfg["name"], cfg.get("filters")))
    return out


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--execute", action="store_true",
                    help="actually fetch; without it this only prints the plan")
    ap.add_argument("--all-trackers", action="store_true",
                    help="every tracker in the Civiqs catalogue, not only the "
                         "registered ones -- for surveying what could be added")
    args = ap.parse_args()

    if args.all_trackers:
        rows = [(None, r["name"], None) for r in civiqs.trackers(use_archive=False)]
    else:
        rows = registered()

    print(f"{len(rows)} tracker page(s) to archive"
          f"{' (whole catalogue)' if args.all_trackers else ' (registered series)'}")
    for sid, name, filt in rows:
        f = "?" + "&".join(f"{k}={v}" for k, v in sorted((filt or {}).items())) if filt else ""
        print(f"  {(sid or '-'):30s} {name}{f}")

    if not args.execute:
        print("\nnothing fetched. re-run with --execute")
        print("(each page is ~2 MB; a tracker already archived today costs no request)")
        return

    t0, ok, failed = time.monotonic(), 0, []
    for sid, name, filt in rows:
        try:
            civiqs.as_displayed(name, filt, net=True, weekday=4)
            ok += 1
        except Exception as e:                     # noqa: BLE001 - reported
            failed.append((name, f"{type(e).__name__}: {e}"))
            print(f"  FAILED {name}: {type(e).__name__}: {e}")
    print(f"\narchived {ok}/{len(rows)} in {(time.monotonic() - t0) / 60:.1f} min")
    if failed:
        print(f"{len(failed)} failed; the archive still serves the last good day, "
              "but a day not fetched is a day gone")

    print("\nhealth:")
    for line in health.report(health.check()):
        print(line)
    print("\ncommit `civiqs/` so the runs that cannot fetch it can still resolve.")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
