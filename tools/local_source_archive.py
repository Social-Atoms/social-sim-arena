"""Archive the residential-only sources, from a residential connection.

Two upstreams cannot be fetched from GitHub Actions: Civiqs 403s every
datacenter IP regardless of headers, and Google Trends throttles them hard.
Both adapters already treat the committed archive as the source of truth --
`civiqs/` and `trends/` hold dated snapshots, and a build serves the day's
archive when one exists. So the fix is not a proxy but a courier: run the
fetches from a home connection once a day, commit what arrived, push. The
six-hourly refresh on Actions then reads the archive and never touches
either host.

Run it from any residential machine with the repo cloned:

    cd social-sim-arena && git pull --quiet && python tools/local_source_archive.py

The script is idempotent within a day (adapters fetch at most once per key
per day) and commits only when something new arrived. It pushes to the branch
it is on; set SSA_ARCHIVE_NO_PUSH=1 to commit locally and leave the remote
alone.

**The daily pair, as actually scheduled.** Two crontab lines: this courier,
and the forecast run that spends money. Both were written the obvious short
way first and both failed silently that way, so the two prefixes below are
load-bearing rather than decorative:

  * `PATH` -- cron's PATH is /usr/bin:/bin and omits /usr/local/bin, where
    Homebrew puts `pdftotext`. Without it `series.build_all` raises on the
    Michigan party PDF and the whole refresh dies before buying a single
    forecast. It failed this way for two days and wrote nothing but a
    traceback, because the failure is at series-build time, before any log
    line a reader would look for.
  * the proxy variables -- an interactive shell picks them up from the user's
    profile and cron does not, so `git pull` fails ("HTTP2 framing layer"),
    the `&&` chain skips the real work, and the log fills with git noise that
    looks nothing like the actual problem.

    PATH=/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin
    # (and, behind a proxy, export https_proxy/http_proxy/all_proxy in the line)

    17 9 * * *   cd $REPO && git pull -q && python tools/local_source_archive.py >> ~/.ssa-archive.log 2>&1
    47 10 * * *  cd $REPO && SSA_MODELS=... SSA_ELICITATION=only:web,web+superfc \
                   SSA_OPENROUTER=kimi SSA_MAX_SPEND=6 python -m ssa.refresh >> ~/.ssa-predict.log 2>&1

Provider keys live in `.env` beside the checkout (never committed); the model
roster and the spend ceiling are environment variables, so the schedule is the
only thing that has to be edited on the machine. Check the logs for a line
reading "forecast files filed: N" -- git output alone does not mean the run
did anything.
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 2)[0])

from ssa import series as series_registry                       # noqa: E402
from ssa.adapters import civiqs as civiqs_adapter               # noqa: E402
from ssa.adapters import trends as trends_adapter               # noqa: E402

# Seconds between distinct Civiqs page fetches. The dashboard is a free
# service being asked for ~22 pages; spacing is the whole cost of staying
# welcome there.
CIVIQS_SPACING = 6


def archive_civiqs():
    """One fetch per distinct (tracker, filters) page, oldest-style politeness."""
    seen, ok, failed = set(), 0, []
    for sid, spec in series_registry.SERIES.items():
        if spec.get("source") != "civiqs":
            continue
        cfg = spec["civiqs"]
        key = civiqs_adapter.archive_key(cfg["name"], cfg.get("filters"))
        if key in seen:
            continue
        seen.add(key)
        try:
            civiqs_adapter.as_displayed(
                cfg["name"], cfg.get("filters"), choice=cfg.get("choice"),
                net=cfg.get("net", False), weekday=cfg.get("weekday"))
            ok += 1
        except Exception as e:                                  # noqa: BLE001
            failed.append(f"{sid}: {type(e).__name__}: {str(e)[:120]}")
        time.sleep(CIVIQS_SPACING)
    return ok, failed


def archive_trends():
    seen, ok, failed = set(), 0, []
    for sid, spec in series_registry.SERIES.items():
        if spec.get("source") != "trends":
            continue
        cfg = spec["trends"]
        key = (cfg["query"], cfg.get("geo", trends_adapter.GEO))
        if key in seen:
            continue
        seen.add(key)
        try:
            trends_adapter.as_archived(*key)
            ok += 1
        except Exception as e:                                  # noqa: BLE001
            failed.append(f"{sid}: {type(e).__name__}: {str(e)[:120]}")
    return ok, failed


def basket_of(round_def):
    """The five queries a Google Trends round is scored against, or None.

    Two round shapes name a basket and they name it differently. A ranking
    round carries the queries inline (`ranking.items`); a share round carries
    `cells`, one series id per brand, and the basket lives in the series
    registry under `trends_basket.basket`. Reading only the first shape is how
    this courier quietly stopped archiving on the day the basket rounds were
    converted from rankings to shares -- it reported "0 baskets" for two days
    with a zero exit status, because "no round names a basket" and "every
    basket round changed shape" look identical from here.
    """
    if round_def.get("tracker") != "google_trends":
        return None
    inline = (round_def.get("ranking") or {}).get("items")
    if inline:
        return tuple(inline), (round_def.get("ranking") or {}).get(
            "geo", trends_adapter.GEO)
    for cell in round_def.get("cells") or []:
        cfg = (series_registry.SERIES.get(cell) or {}).get("trends_basket")
        if cfg:
            return tuple(cfg["basket"]), cfg.get("geo", trends_adapter.GEO)
    return None


def archive_trends_baskets():
    """One comparison fetch per distinct basket named by a Trends round.

    Baskets come from the season file, not the series registry: the basket IS
    part of the round's frozen contract, and archiving exactly what the rounds
    name keeps this courier from drifting away from the questions it exists to
    resolve.
    """
    season_path = os.path.join(__file__.rsplit("/", 2)[0],
                               "questions", "season0.json")
    with open(season_path) as f:
        season = json.load(f)
    seen, ok, failed = set(), 0, []
    for r in season["rounds"]:
        key = basket_of(r)
        if key is None:
            continue
        if key in seen:
            continue
        seen.add(key)
        try:
            trends_adapter.basket_snapshot(list(key[0]), key[1])
            ok += 1
        except Exception as e:                                  # noqa: BLE001
            failed.append(f"{r['round_id']}: {type(e).__name__}: {str(e)[:120]}")
    # A Trends round on the board with no basket behind it resolves against
    # nothing, so say so here rather than at resolution time in September.
    wanted = sum(1 for r in season["rounds"] if basket_of(r))
    if wanted and not seen:
        failed.append("google_trends rounds exist but none named a basket")
    return ok, failed


def main():
    c_ok, c_fail = archive_civiqs()
    t_ok, t_fail = archive_trends()
    b_ok, b_fail = archive_trends_baskets()
    for line in c_fail + t_fail + b_fail:
        print("FAIL", line, file=sys.stderr)
    print(f"archived: civiqs {c_ok} pages, trends {t_ok} queries, "
          f"{b_ok} baskets; {len(c_fail) + len(t_fail) + len(b_fail)} failures")

    root = __file__.rsplit("/", 2)[0]

    def git(*args, check=True):
        return subprocess.run(["git", "-C", root, *args],
                              check=check, capture_output=True, text=True)

    git("add", "-A", "civiqs", "trends")
    if git("diff", "--cached", "--quiet", check=False).returncode == 0:
        print("nothing new to commit")
        return 0 if not (c_fail or t_fail or b_fail) else 1
    git("-c", "user.name=ssa-bot", "-c", "user.email=actions@github.com",
        "commit", "-m", "source archive (residential courier)\n\n"
        "Co-Authored-By: assassin808 "
        "<93385065+assassin808@users.noreply.github.com>")
    # SSA_ARCHIVE_NO_PUSH keeps the archive local: the commit still happens,
    # nothing touches the remote. For the periods when the maintainers want
    # GitHub left alone; one ordinary push later carries everything up.
    if os.environ.get("SSA_ARCHIVE_NO_PUSH"):
        print("committed locally (push disabled by SSA_ARCHIVE_NO_PUSH)")
        return 0 if not (c_fail or t_fail or b_fail) else 1
    git("-c", "rebase.autoStash=true", "pull", "--rebase")
    git("push")
    print("committed and pushed")
    return 0 if not (c_fail or t_fail or b_fail) else 1


if __name__ == "__main__":
    sys.exit(main())
