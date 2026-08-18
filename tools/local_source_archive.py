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

and schedule it daily, e.g. crontab:

    17 9 * * *  cd $HOME/social-sim-arena && git pull -q && python tools/local_source_archive.py >> ~/.ssa-archive.log 2>&1

The script is idempotent within a day (adapters fetch at most once per key
per day) and commits only when something new arrived. It pushes to the
branch it is on -- keep the clone on main, which is what the refresh reads.
"""
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


def main():
    c_ok, c_fail = archive_civiqs()
    t_ok, t_fail = archive_trends()
    for line in c_fail + t_fail:
        print("FAIL", line, file=sys.stderr)
    print(f"archived: civiqs {c_ok} pages, trends {t_ok} queries; "
          f"{len(c_fail) + len(t_fail)} failures")

    root = __file__.rsplit("/", 2)[0]

    def git(*args, check=True):
        return subprocess.run(["git", "-C", root, *args],
                              check=check, capture_output=True, text=True)

    git("add", "-A", "civiqs", "trends")
    if git("diff", "--cached", "--quiet", check=False).returncode == 0:
        print("nothing new to commit")
        return 0 if not (c_fail or t_fail) else 1
    git("-c", "user.name=ssa-bot", "-c", "user.email=actions@github.com",
        "commit", "-m", "source archive (residential courier)\n\n"
        "Co-Authored-By: assassin808 "
        "<93385065+assassin808@users.noreply.github.com>")
    git("-c", "rebase.autoStash=true", "pull", "--rebase")
    git("push")
    print("committed and pushed")
    return 0 if not (c_fail or t_fail) else 1


if __name__ == "__main__":
    sys.exit(main())
