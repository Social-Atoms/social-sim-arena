"""Pull the Economist/YouGov crosstab workbook and archive it as today's vintage.

    python tools/pull_yougov_xtab.py             # what a pull would do; no request
    python tools/pull_yougov_xtab.py --execute   # fetch, archive, parse, report

**Why this is a command and not a cron line.** YouGov's public-data licence
bars "bots, crawlers, or automated scripts to extract or copy the Licensed
Data" without written permission, and the arena's approval of this source
(`ssa/inventory.py`, `yougov_xtab`) rests on every pull being a maintainer's
deliberate act. The written-permission request is with YouGov's legal team.
Until it is answered, this is the act: one run a week, by a person.

**When to run it.** The crosstab round is weekly, one round per wave. A wave
dated Monday is in the workbook by about Thursday, and the round that asks
about it (`yougov-xtab-<year>-w<NN>`) waits by name until a committed vintage
carries it -- `profile_round.resolution` refuses to score it against last
week's wave. So: run this on or after Thursday, commit the new file under
`sources/yougov_xtab/`, and the next refresh resolves the round. The courier
(`tools/local_source_archive.py`) prints the archive's age every day so a
missed week is visible in its log.

The archive is write-once per day: a second run on the same day returns the
morning's file untouched.
"""
import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa.adapters import yougov_xtab  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--execute", action="store_true",
                    help="actually fetch; without it, only report")
    args = ap.parse_args(argv)

    days = yougov_xtab.archived_days()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if days:
        newest = yougov_xtab.newest_archived()
        with open(newest, "rb") as fh:
            waves = yougov_xtab.parse(fh.read())
        print(f"newest vintage: {days[-1]} ({len(waves)} waves, newest wave "
              f"{waves[-1]['date']})")
    else:
        print("no vintage archived yet")
    if today in days:
        print(f"a vintage for {today} already exists; nothing to do")
        return 0
    if not args.execute:
        print(f"would fetch {yougov_xtab.URL.format(tracker='donald-trump-approval')} "
              f"and archive it as sources/yougov_xtab/{today}.xlsx\n"
              "re-run with --execute to do it")
        return 0
    waves = yougov_xtab.pull()
    print(f"archived sources/yougov_xtab/{today}.xlsx: {len(waves)} waves, "
          f"newest wave {waves[-1]['date']}")
    print("now: git add sources/yougov_xtab && git commit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
