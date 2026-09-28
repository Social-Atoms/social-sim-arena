"""One-time correction of Season 0 scores left behind by fixed bugs (#178, #187).

    python tools/correct_season0_178.py            # what it would change; writes nothing
    python tools/correct_season0_178.py --write    # apply

**Corrected (11).** Each value is re-derived here from the same source the
round names, never typed in:

- Civiqs: from the committed archive under `civiqs/`, with no network.
- Trends: from the committed archive under `trends/`, with no network.
- Morning Consult: from the committed Silver Bulletin vintage under `sources/`, with no network.
- Wikipedia: from the Wikimedia pageviews API. Those counts are final once a
  week closes, and the repository keeps no daily archive of them.

The superseded resolution is kept inside a `corrected` block, following the
`umich-2026-08-prelim` precedent. `resolved.json` still never loses an entry.

**Voided (13).** A voided round leaves `questions/season0.json` for
`questions/legacy/`, with `withdrawn_at` and `withdrawn_reason`. It is the
same mechanism as the w40-w45 adult-base withdrawal in #175. Nothing reads a
round that is not in the season, so it stops being scored. Its forecasts, lock
snapshot and any resolution stay on disk, and the resolution gains a `voided`
block that says why.

**As-scored board.** Before anything changes, the leaderboard as published is
copied to `resolutions/as-scored-<date>.json`, so the effect of the correction
can be shown rather than asserted.

Idempotent: a second `--write` changes nothing.
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import series as S  # noqa: E402
from ssa.adapters import civiqs, silverbulletin as sb, trends, wikipedia  # noqa: E402

QUESTIONS = os.path.join(ROOT, "questions", "season0.json")
LEGACY = os.path.join(ROOT, "questions", "legacy")
RESOLVED = os.path.join(ROOT, "resolutions", "resolved.json")
DATA = os.path.join(ROOT, "site", "data.json")

WRONG_WEEK = (
    "the round locked eight days before the week it asks about, so 'the first "
    "point after the frozen history' was the week before that week. The "
    "question names the week; this is that week's value. Tracked in #178 (A) "
    "and, for the resolver rule, #177.")
RESTATEMENT = (
    "Silver Bulletin restated the 2026-08-15 Morning Consult wave from 42.9 to "
    "42.87, a rounding change, and the resolver counted it as a new release. "
    "{rid} was scored on that restatement instead of its own week's wave. "
    "This is that wave. Tracked in #178 (A) and #177.")
RESTATEMENT_KNOCK = (
    "Knock-on from mc-2026-w35-approval: once that round had taken the "
    "restatement, this round took the wave that belonged to w35. This is this "
    "week's own wave, 8/28-31. Tracked in #178 (A).")
CIVIQS_PRELOCK = (
    "the Civiqs archive landed after the lock, so the frozen history ended "
    "2026-09-04 and the resolver took 2026-09-11, a reading public before the "
    "2026-09-16 lock. The round asks for the Friday 2026-09-18 reading. "
    "Guarded since #175 (DAILY_SOURCES). Tracked in #178 (A).")


def _at(points, day):
    hit = [p for p in points if p["date"] == day]
    if not hit:
        raise ValueError(f"no observation dated {day}")
    return hit[0]["value"]


def civiqs_value(sid, day):
    c = S.SERIES[sid]["civiqs"]
    return _at(civiqs.as_displayed(
        c["name"], c.get("filters"), choice=c.get("choice"),
        net=c.get("net", False), weekday=c.get("weekday"), fetch=False), day)


def trends_value(sid, day):
    c = S.SERIES[sid]["trends"]
    return _at(trends.as_archived(c["query"], c.get("geo", trends.GEO),
                                  fetch=False), day)


def wiki_value(sid, day):
    return _at(wikipedia.weekly_series(S.SERIES[sid]["wikipedia"]["article"]), day)


def sb_value(sid, day, vintage=None):
    paths = sorted(glob.glob(os.path.join(ROOT, "sources", "sb_approval", "*.csv")))
    path = vintage or paths[-1]
    with open(path) as fh:
        rows = sb.parse(fh.read())
    spec = S.SERIES[sid]
    return _at(sb.to_series(sb.approval_polls(rows=rows, **spec["filters"]),
                            spec["value"]), day)


# round -> (series, observed date, derive, why)
CORRECTIONS = {
    "wiki-2026-08-30-trump": ("wiki_views_trump", "2026-08-30", wiki_value, WRONG_WEEK),
    "wiki-2026-08-30-taylor-swift": ("wiki_views_taylor_swift", "2026-08-30", wiki_value, WRONG_WEEK),
    "trends-2026-08-29-iphone": ("trends_iphone", "2026-08-29", trends_value, WRONG_WEEK),
    "trends-2026-08-29-tesla": ("trends_tesla", "2026-08-29", trends_value, WRONG_WEEK),
    "mc-2026-w35-approval": ("mc_approval", "2026-08-22", sb_value,
                             RESTATEMENT.format(rid="mc-2026-w35-approval")),
    "mc-2026-w36-approval": ("mc_approval", "2026-08-29", sb_value, RESTATEMENT_KNOCK),
    "civiqs-2026-w38-angry-share": ("civiqs_angry_share", "2026-09-18", civiqs_value, CIVIQS_PRELOCK),
    "civiqs-2026-w38-econ-direction": ("civiqs_net_econ_direction", "2026-09-18", civiqs_value, CIVIQS_PRELOCK),
    "civiqs-2026-w38-econ-now": ("civiqs_net_econ_now", "2026-09-18", civiqs_value, CIVIQS_PRELOCK),
    "civiqs-2026-w38-family-finances": ("civiqs_net_family_finances", "2026-09-18", civiqs_value, CIVIQS_PRELOCK),
    "civiqs-2026-w38-inflation-concern": ("civiqs_net_inflation_concern", "2026-09-18", civiqs_value, CIVIQS_PRELOCK),
}

ADULT_BASE = (
    "Economist/YouGov moved its whole wave from an adult base to a "
    "registered-voter one from the 9/4-8 wave. `{series}` is the adult base and "
    "has not published since 2026-08-29, so this round can never resolve. The "
    "RV reading is a different population, about three points higher, so "
    "scoring against it would answer a question nobody asked. Voided for every "
    "entrant; see #187.")
GENERIC_LV = (
    "the question asks for the registered-voter generic ballot, but "
    "Economist/YouGov moved it to likely voters from the 9/4-8 wave (n about "
    "1,400 to about 1,000; margin 6 to 11-14). The resolved value is a "
    "likely-voter number, and no registered-voter reading exists to correct "
    "it to. Voided for every entrant; see #178 (A, rows 12-14).")
RV_PRELOCK = (
    "the frozen history ended 2026-08-29 because the 09-06 and 09-12 RV waves "
    "were published under a label the lock snapshot's filter did not read. The "
    "first point after the freeze was therefore public before the 2026-09-20 "
    "lock, and no answer to this round was unknown when entrants filed. "
    "Voided for every entrant; see #178 (D1).")
MISMATCHED = (
    "entrants and the persistence null were shown different histories. "
    "Forecasts were filed up to seven days before the lock, or the source "
    "revised a value between filing and lock, so the last value in a model's "
    "prompt ({saw}) is not the one persistence was built on ({null}). A skill "
    "score measured against a null that saw different data is not a "
    "comparison. Current code files inside the 24h window. Voided for every "
    "entrant; see #178 (B).")

VOIDS = {
    "yougov-2026-w37-approval": ADULT_BASE.format(series="yougov_approval"),
    "yougov-2026-w38-approval": ADULT_BASE.format(series="yougov_approval"),
    "yougov-2026-w39-approval": ADULT_BASE.format(series="yougov_approval"),
    "yougov-2026-w39-strong-approval": ADULT_BASE.format(series="yougov_strong_approval"),
    "yougov-2026-w37-generic": GENERIC_LV,
    "yougov-2026-w38-generic": GENERIC_LV,
    "yougov-2026-w39-generic": GENERIC_LV,
    "yougov-2026-w39-rv-approval": RV_PRELOCK,
    "aaii-2026-08-27": MISMATCHED.format(saw="-3.2", null="-4.4"),
    "yougov-2026-w35-approval": MISMATCHED.format(saw="33", null="35"),
    "yougov-2026-w35-generic": MISMATCHED.format(saw="6", null="7"),
    "civiqs-2026-w35-approval": MISMATCHED.format(saw="-23.7 / -24.2", null="-22.6"),
    "civiqs-rep-2026-w35": MISMATCHED.format(saw="66.8", null="68.3"),
}


def _load(path):
    with open(path) as fh:
        return json.load(fh)


def _dump(path, obj, indent=2, sort_keys=False):
    with open(path, "w") as fh:
        json.dump(obj, fh, indent=indent, sort_keys=sort_keys, ensure_ascii=False)
        fh.write("\n")


def plan(now):
    """(resolved', season', legacy files, report lines). Pure apart from reads."""
    resolved = _load(RESOLVED)
    season = _load(QUESTIONS)
    at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    lines, legacy = [], {}

    for rid, (sid, day, derive, why) in CORRECTIONS.items():
        old = resolved.get(rid)
        if old is None:
            raise ValueError(f"{rid}: not in resolved.json; nothing to correct")
        value = derive(sid, day)
        if old.get("corrected") and old["value"] == value and old["observed_date"] == day:
            lines.append(f"  = {rid}: already {value} @ {day}")
            continue
        if old.get("series") != sid:
            raise ValueError(f"{rid}: resolved on {old.get('series')}, expected {sid}")
        resolved[rid] = {
            "corrected": {
                "at": at,
                "replaces": {k: old[k] for k in ("observed_date", "resolved_at", "value")},
                "why": why,
            },
            "method": ("the observation the round's question names, re-derived "
                       "by tools/correct_season0_178.py from the round's own source"),
            "observed_date": day,
            "resolved_at": at,
            "series": sid,
            "value": value,
        }
        lines.append(f"  ~ {rid}: {old['value']} @ {old['observed_date']} -> {value} @ {day}")

    by_id = {r["round_id"]: r for r in season["rounds"]}
    for rid, why in VOIDS.items():
        path = os.path.join(LEGACY, rid + ".json")
        if rid not in by_id:
            if not os.path.exists(path):
                raise ValueError(f"{rid}: in neither the season nor questions/legacy/")
            lines.append(f"  = {rid}: already voided")
            continue
        legacy[path] = dict(by_id[rid], withdrawn_at=at, withdrawn_reason=why)
        if rid in resolved and "voided" not in resolved[rid]:
            resolved[rid] = dict(resolved[rid], voided={"at": at, "why": why})
        lines.append(f"  x {rid}: voided{' (resolution kept, marked voided)' if rid in resolved else ''}")
    season = dict(season, rounds=[r for r in season["rounds"] if r["round_id"] not in VOIDS])
    return resolved, season, legacy, lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    resolved, season, legacy, lines = plan(now)
    print("\n".join(lines))
    if not args.write:
        print("dry run; re-run with --write to apply")
        return 0

    snap = os.path.join(ROOT, "resolutions", f"as-scored-{now.date()}.json")
    if legacy and not os.path.exists(snap):
        data = _load(DATA)
        _dump(snap, {
            "note": ("The leaderboard as published before tools/correct_season0_178.py "
                     "corrected 11 resolutions and voided 13 rounds (#178, #187). "
                     "Kept so the effect of the correction can be shown."),
            "data_generated_at": data.get("generated_at"),
            "leaderboard": data.get("leaderboard"),
            "profile": data.get("profile"),
            "ranking": data.get("ranking"),
        }, indent=1)
        print(f"wrote {os.path.relpath(snap, ROOT)}")
    os.makedirs(LEGACY, exist_ok=True)
    for path, doc in legacy.items():
        _dump(path, doc, sort_keys=True)
    _dump(QUESTIONS, season)
    _dump(RESOLVED, resolved, sort_keys=True)
    print(f"wrote {len(legacy)} legacy round(s), questions/season0.json, resolutions/resolved.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
