"""Recover the Conference Board CCI's first prints from the Internet Archive.

    python tools/backfill_cci.py                 # what it would fetch
    python tools/backfill_cci.py --execute       # fetch, parse, write

The Conference Board publishes only the current release for free. The history
is a $2,370 subscription -- and it is the *revised* history, which is not what
a round resolves against. The Archive holds monthly captures of the release
page, and each one carries the number **as it was published**, before the
following month restated it. That is the series the arena actually needs, and
it costs nothing.

Free and keyless. Slow and polite on purpose: the replay endpoint returns 503
under any pressure, and under sustained pressure it starts answering with a
1 KB stub instead of the page -- which parses as "no reading on this page" and
looks exactly like a capture that never had one. Anything under MIN_BYTES is
therefore treated as a throttle, not as a miss, and retried.

**Run it from a runner rather than a laptop if you can.** Unlike Civiqs, the
Archive does not refuse datacenter IPs, and a runner's connection gets through
the throttling far better than a home one -- roughly a third of captures came
back as stubs from a residential link.

Output is `sources/confboard/<release date>.json`, one file per release, dated
by the page's own "Latest Press Release Updated:" line rather than by the
snapshot timestamp. Existing files are never overwritten: a first print is
recorded once and does not change afterwards, which is the whole point.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa.adapters import confboard                              # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "sources", "confboard")
PAGE = "https://www.conference-board.org/topics/consumer-confidence"
CDX = ("http://web.archive.org/cdx/search/cdx?url=conference-board.org/topics/"
       "consumer-confidence&output=json&fl=timestamp&filter=statuscode:200"
       "&collapse=timestamp:6")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

# Below this the Archive handed back a throttle stub, not a capture. Real
# captures of this page run 150 KB to 900 KB; the stub is about 1 KB.
MIN_BYTES = 20000
TRIES = 5
PAUSE = 6


def get(url, timeout=120, tries=TRIES):
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            body = urllib.request.urlopen(req, timeout=timeout).read()
            if len(body) >= MIN_BYTES or "cdx" in url:
                return body
            last = RuntimeError(f"{len(body)} byte stub (throttled)")
        except Exception as e:                                  # noqa: BLE001
            last = e
        time.sleep(PAUSE * (attempt + 1))
    raise last


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--execute", action="store_true",
                    help="actually fetch; without it this only prints the plan")
    ap.add_argument("--since", default="2021",
                    help="earliest snapshot year (default 2021; the 2020 page "
                         "predates the (1985=100) headline and cannot be read)")
    args = ap.parse_args()

    stamps = [r[0] for r in json.loads(get(CDX))[1:] if r[0][:4] >= args.since]
    print(f"{len(stamps)} monthly snapshots from {args.since} onward "
          f"({stamps[0][:6]} .. {stamps[-1][:6]})")
    if not args.execute:
        print("\nnothing fetched. re-run with --execute")
        print("(free and keyless; the Archive throttles hard, so expect this "
              "to take a while and to retry)")
        return

    os.makedirs(OUT, exist_ok=True)
    wrote, already, failed = 0, 0, []
    for i, ts in enumerate(stamps, 1):
        try:
            html = get(f"https://web.archive.org/web/{ts}id_/{PAGE}").decode(
                "utf-8", "replace")
            got = confboard.parse(html, year=int(ts[:4]))
        except Exception as e:                                  # noqa: BLE001
            failed.append((ts, f"{type(e).__name__}: {str(e)[:90]}"))
            print(f"  [{i:2d}/{len(stamps)}] {ts[:8]} -- {type(e).__name__}")
            continue
        got["snapshot"] = ts
        got["source"] = f"https://web.archive.org/web/{ts}id_/{PAGE}"
        day = got.get("released_on") or f"{got['month'][:7]}-28"
        path = os.path.join(OUT, day + ".json")
        if os.path.exists(path):
            already += 1
        else:
            with open(path, "w") as f:
                json.dump(got, f, indent=1, sort_keys=True)
            wrote += 1
        print(f"  [{i:2d}/{len(stamps)}] {ts[:8]} -> {got['month']} "
              f"CCI={got['value']:6.1f} released={got['released_on']}")
        time.sleep(2)

    print(f"\nwrote {wrote}, already had {already}, failed {len(failed)}")
    if failed:
        print("failures (the Archive throttles; re-running resumes):")
        for ts, why in failed[:12]:
            print(f"  {ts[:8]}  {why}")
    print(f"\n{OUT}")
    print("Each file is a first print and is never rewritten. Commit them.")


if __name__ == "__main__":
    main()
