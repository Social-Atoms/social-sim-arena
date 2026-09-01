"""Archive the source snapshots that must survive outside a runner.

Two upstreams cannot be fetched from GitHub Actions: Civiqs 403s every
datacenter IP regardless of headers, and Google Trends throttles them hard.
Wikipedia's daily top lists are reachable there, but they are the resolution
record for ranking rounds and therefore have the same durability requirement:
the exact daily lists must be committed before a runner disappears. The three
adapters treat the committed archive as the source of truth -- `civiqs/`,
`trends/`, and `wikitop/` hold dated snapshots. So the fix is a courier: run
the fetches from a home connection once a day, commit what arrived, push. The
six-hourly refresh on Actions can then read the archive without depending on a
live upstream at resolution time.

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
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, __file__.rsplit("/", 2)[0])

from ssa import ranking_round, series as series_registry        # noqa: E402
from ssa.adapters import civiqs as civiqs_adapter               # noqa: E402
from ssa.adapters import trends as trends_adapter               # noqa: E402
from ssa.adapters import wikipedia as wikipedia_adapter         # noqa: E402

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


def wikipedia_days(round_def):
    """Settled daily top lists needed by one Wikipedia ranking round.

    The source history shown to an entrant is six weeks plus the target week.
    Derive the exact same seven week ends as ``ranking_round.observations`` so
    the courier cannot archive a subtly different horizon. Future and still
    settling dates are filtered by ``archive_wikipedia`` against a pinned UTC
    clock; they are expected absences, not failures.
    """
    if round_def.get("tracker") != "wikipedia" \
            or not ranking_round.is_ranking(round_def):
        return None
    spec = ranking_round.spec_for(round_def)
    if spec["kind"] != "wiki_top10":
        return None
    end = date.fromisoformat(spec["week_end"])
    week_ends = [end - timedelta(days=7 * i)
                 for i in range(ranking_round.HISTORY_WEEKS, -1, -1)]
    days = {d for week_end in week_ends
            for d in wikipedia_adapter.week_days(week_end)}
    return spec, days


def archive_wikipedia(now=None, season=None):
    """Archive every settled Wikipedia day a live ranking round can consume.

    Existing files are served without a request by ``top_snapshot``. A day
    younger than Wikimedia's finality lag is deliberately skipped: filing a
    partial count write-once would be worse than waiting for tomorrow's run.
    """
    now = now or datetime.now(timezone.utc)
    if season is None:
        season_path = os.path.join(__file__.rsplit("/", 2)[0],
                                   "questions", "season0.json")
        with open(season_path) as f:
            season = json.load(f)

    wanted = {}
    failures = []
    for r in season["rounds"]:
        try:
            found = wikipedia_days(r)
        except Exception as e:                                  # noqa: BLE001
            failures.append(
                f"{r.get('round_id', '?')}: {type(e).__name__}: {str(e)[:120]}")
            continue
        if found is None:
            continue
        spec, days = found
        key = (spec["project"], spec["access"])
        wanted.setdefault(key, set()).update(days)

    final_through = now.date() - timedelta(
        days=wikipedia_adapter.TOP_FINAL_LAG_DAYS)
    ok = 0
    for (project, access), days in sorted(wanted.items()):
        for day in sorted(d for d in days if d <= final_through):
            try:
                articles = wikipedia_adapter.top_snapshot(
                    day, project, access, fetch=True, now=now)
                if articles is None:
                    raise RuntimeError("settled day returned no snapshot")
                ok += 1
            except Exception as e:                              # noqa: BLE001
                failures.append(
                    f"{project}/{access}/{day.isoformat()}: "
                    f"{type(e).__name__}: {str(e)[:120]}")
    return ok, failures


def main():
    c_ok, c_fail = archive_civiqs()
    t_ok, t_fail = archive_trends()
    b_ok, b_fail = archive_trends_baskets()
    w_ok, w_fail = archive_wikipedia()
    for line in c_fail + t_fail + b_fail + w_fail:
        print("FAIL", line, file=sys.stderr)
    print(f"archived: civiqs {c_ok} pages, trends {t_ok} queries, "
          f"{b_ok} baskets, wikipedia {w_ok} days; "
          f"{len(c_fail) + len(t_fail) + len(b_fail) + len(w_fail)} failures")

    root = __file__.rsplit("/", 2)[0]

    def git(*args, check=True):
        return subprocess.run(["git", "-C", root, *args],
                              check=check, capture_output=True, text=True)

    git("add", "-A", "civiqs", "trends", "wikitop")
    if git("diff", "--cached", "--quiet", check=False).returncode == 0:
        print("nothing new to commit")
        return 0 if not (c_fail or t_fail or b_fail or w_fail) else 1
    git("-c", "user.name=ssa-bot", "-c", "user.email=actions@github.com",
        "commit", "-m", "source archive (residential courier)\n\n"
        "Co-Authored-By: assassin808 "
        "<93385065+assassin808@users.noreply.github.com>")
    # SSA_ARCHIVE_NO_PUSH keeps the archive local: the commit still happens,
    # nothing touches the remote. For the periods when the maintainers want
    # GitHub left alone; one ordinary push later carries everything up.
    if os.environ.get("SSA_ARCHIVE_NO_PUSH"):
        print("committed locally (push disabled by SSA_ARCHIVE_NO_PUSH)")
        return 0 if not (c_fail or t_fail or b_fail or w_fail) else 1
    git("-c", "rebase.autoStash=true", "pull", "--rebase")
    git("push")
    print("committed and pushed")
    return 0 if not (c_fail or t_fail or b_fail or w_fail) else 1


if __name__ == "__main__":
    sys.exit(main())
