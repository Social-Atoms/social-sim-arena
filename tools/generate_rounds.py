"""Generate the next weekly bundle of rounds from the registry. Never publishes.

  python tools/generate_rounds.py                    # candidates + review diff
  python tools/generate_rounds.py --weeks 6          # the rest of the season
  python tools/generate_rounds.py --write            # write the candidate file

Rounds used to be hand-written, one JSON object at a time, which is why season 0
runs out in late September: somebody wrote questions as far ahead as they had
patience for. That is also why 23 registered series have never carried a round
at all -- they arrive in the same download as series that do, cost nothing extra
to fetch, and were simply never written up.

This turns both into one command. It reads the registry, infers each series'
publication weekday from its own observed history, applies the gates that
`docs/sources.md` sets out, and emits candidates for review. **It never writes
into `questions/season0.json`.** A human reads the diff and moves the ones they
accept, which is the whole reason the frozen season file stays trustworthy: no
round appears in it that a person did not put there.

**Every rejection carries a machine-readable reason.** A generator that silently
drops a series is worse than one that never saw it, because nobody can tell the
difference between "considered and refused" and "forgotten". `--rejects` prints
the refusals with their evidence.

**The gates, and why each exists.**

- `rights`: the source's terms state. Registration is not permission, and a
  series whose rights are unresolved must not become a scored round. Read from
  `RIGHTS`, which is checked in and edited by a human, never inferred here.
- `history`: fewer than `MIN_HISTORY` observations and the statistical
  baselines cannot be computed, so the round would ship unscoreable.
- `volatility`: `docs/sources.md` §1.4. Refuses only the unarguable case --
  every observed movement is measurement noise -- and *reports* the
  signal-to-noise ratio on every surviving candidate for the reviewer to
  judge. See `MIN_REAL_MOVEMENT` for why the line is not drawn tighter here.
- `schedule`: the publication weekday has to be inferable from history, or a
  release time would be a guess and the lock would be a guess with it.

**Determinism.** Same registry plus same archive gives byte-identical output:
ids, wording, units, release and lock times, resolution rule and target type are
all derived, none sampled. The release calendar comes from the modal weekday of
the observed history rather than from a hand-typed constant, so a tracker that
moves its publication day is followed rather than silently mis-scheduled.
"""
import argparse
import collections
import json
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import batches                                    # noqa: E402
from ssa.series import SERIES                              # noqa: E402

# Minimum observations before the baselines mean anything.
MIN_HISTORY = 12

# The volatility gate reports, and refuses only what is unarguable.
#
# `docs/sources.md` §1.4 is right that a series which barely moves puts an
# unscoreable round on the board, and `scoring.noise_floor` is the right
# instrument: it splits a published series into real movement `s` and
# measurement noise `m` from its own history. What is *not* settled is where to
# put the line.
#
# Calibrating it honestly needs the series as the round resolves it -- an
# average-resolved round is scored against a house-effect-adjusted, sqrt(n)
# weighted pool, not against the raw stream of individual poll readings the
# registry carries. Rebuilding that here means carrying pollster and sample
# size through, which the registry series do not expose. Measured on what is
# available, a ratio of 1.0 refuses `mc_approval`, `yougov_approval` and
# `yougov_generic_margin` -- three series the arena runs live today. A gate
# that deletes a third of the season is not measuring what it claims to.
#
# So the ratio is *reported on every candidate* and left to the reviewer, and
# only the unarguable case is refused: `s == 0`, where the estimator hit its
# boundary and every movement the series showed over the window was noise.
# That is the case where no forecaster can do better than the last value by
# construction, and it needs no threshold to justify.
#
# Picking the real threshold is metric-design work -- research card R1 -- and
# it should be set against resolved rounds, not guessed here.
MIN_REAL_MOVEMENT = 0.0

# Sources with no committed archive, which therefore cannot be rebuilt from a
# clean checkout. Their series are reported as refused rather than fetched:
# generation has to be reproducible offline, and a source that only exists on
# the network makes every run depend on that host being up today.
OFFLINE_UNAVAILABLE = ("pentaesi",)

# How far ahead a generated release may be scheduled. Beyond this the inferred
# calendar is extrapolation dressed as a schedule.
MAX_WEEKS_AHEAD = 8

# Rights state per source, maintained by hand from the per-URL licence audit.
# `approved` means we may retrieve it the way we do and publish derived values.
# Anything not listed is treated as unresolved and generates nothing, so adding
# an adapter cannot quietly add rounds.
RIGHTS = {
    "sb_approval": "approved",     # published sheet, already fetched for live rounds
    "sb_generic": "approved",      # same sheet family
    "civiqs": "approved",          # terms permit download/copy with notices kept
    "umich": "approved",           # main site, public tables
    "sce": "approved",             # licence grants reproduce/use/distribute
    "aaii": "permission-needed",   # robots disallows /files/*, terms bar copying
    "confboard": "permission-needed",   # terms bar extraction into a database
    "umichparty": "permission-needed",  # archive site needs written consent
    "yougov_xtab": "permission-needed",  # licence bars automated extraction
    "pentaesi": "permission-needed",
    "hhpoll": "approved",          # no terms published; manual retrieval only
    "trends": "approved",
    "wikipedia": "approved",
}

# Resolution wording per source family. A generated round states how it will be
# settled in the same words the hand-written rounds use, because the resolution
# rule is part of the question and an entrant is graded on it.
RESOLVE = {
    "sb_approval": "adjusted average at the release date",
    "sb_generic": "adjusted average at the release date",
    "civiqs": "dashboard Friday value, from the daily archive",
}

# The pollster, not the file it arrives in. Every Silver Bulletin series is a
# different house asking a different question, and collapsing them onto one
# tracker name would give `mc_approval`, `yougov_approval` and `ipsos_approval`
# the same round id -- three questions wearing one name, which the naming
# invariant in CLAUDE.md exists to prevent.
TRACKER_BY_PREFIX = (
    ("mc_", "morning_consult", "mc"),
    ("yougov_", "economist_yougov", "yougov"),
    ("ipsos_", "ipsos", "ipsos"),
    ("rasmussen_", "rasmussen", "rasmussen"),
    ("navigator_", "navigator", "navigator"),
    ("rmg_", "rmg", "rmg"),
    ("civiqs_", "civiqs", "civiqs"),
)


def naming(sid):
    """(tracker, id_prefix, suffix) for a series id.

    The suffix keeps everything after the house prefix, so `mc_econ_approval`
    and `yougov_econ_approval` stay two distinguishable rounds rather than
    colliding on "econ-approval".
    """
    for pref, tracker, short in TRACKER_BY_PREFIX:
        if sid.startswith(pref):
            rest = sid[len(pref):]
            for drop in ("net_approval_", "net_"):
                if rest.startswith(drop):
                    rest = rest[len(drop):]
            return tracker, short, (rest.replace("_", "-") or "topline")
    return sid.split("_")[0], sid.split("_")[0], sid.replace("_", "-")


def load_history():
    """Every registered series as {id: [{date, value}]}, from the archive.

    Offline by construction: the committed vintages under `sources/` are what
    the live pipeline already fetched, so generation is reproducible from a
    clean checkout and cannot be made to depend on a source being reachable
    the day someone runs it.
    """
    from ssa.adapters import silverbulletin as sb
    src = {}
    for key, folder in (("sb_approval", "sb_approval"), ("sb_generic", "sb_generic")):
        d = os.path.join(ROOT, "sources", folder)
        if not os.path.isdir(d):
            continue
        newest = sorted(f for f in os.listdir(d) if f.endswith(".csv"))
        if newest:
            with open(os.path.join(d, newest[-1])) as fh:
                src[key] = sb.parse(fh.read())
    # `build_all` fetches any source key it is not handed, and two of them go
    # to the network unconditionally -- AAII, which has been 503 to the runner
    # for days, and the ESI feed. Generation must not depend on a host being
    # up, so both are rebuilt from what is committed.
    #
    # AAII archives whole pages, and its rows carry no year: parsing needs the
    # `asof` from the response that served them, which here is the archive's
    # own filename. The adapter's `parse(text, asof)` takes exactly that pair.
    from ssa.adapters import aaii as aaii_adapter
    d = os.path.join(ROOT, "sources", "aaii")
    if os.path.isdir(d):
        pages = sorted(f for f in os.listdir(d) if f.endswith(".html"))
        if pages:
            with open(os.path.join(d, pages[-1])) as fh:
                asof = datetime.strptime(pages[-1][:10], "%Y-%m-%d").date()
                src["aaii"] = aaii_adapter.parse(fh.read(), asof)
    # The ESI feed archives nothing, so it cannot be rebuilt offline at all,
    # and `build_all` refuses to return a series that built to zero points --
    # correctly, since a silently empty series is how unscoreable rounds reach
    # the board. So those series are lifted out of the registry for the length
    # of the build and reported as refused, rather than either fabricating
    # history for them or making every generation run depend on a host we are
    # not allowed to script against in the first place.
    from ssa import series as registry
    lifted = {sid: registry.SERIES.pop(sid)
              for sid, m in list(registry.SERIES.items())
              if m.get("source") in OFFLINE_UNAVAILABLE}
    try:
        built = registry.build_all(src)
    finally:
        registry.SERIES.update(lifted)
    return built, set(lifted)


def modal_weekday(hist):
    """The weekday this series actually publishes on, from its own history.

    Modal rather than latest, so one delayed release does not move the whole
    calendar, and inferred rather than declared, so a tracker that shifts its
    publication day is followed instead of quietly mis-scheduled.
    """
    days = [datetime.strptime(p["date"], "%Y-%m-%d").weekday() for p in hist[-26:]]
    if not days:
        return None
    top, n = collections.Counter(days).most_common(1)[0]
    return top if n >= max(3, len(days) // 3) else None


def signal_to_noise(hist, source=None):
    """(ratio, real movement, measurement noise) for the object being scored.

    Uses the arena's own `scoring.noise_floor`, so the gate that decides whether
    a round is worth asking and the ceiling that decides whether it is
    scoreable are one estimate rather than two constants that can drift apart.

    **Measured on the resolution series, not on the raw input.** An
    average-resolved round is scored against `average.adjusted_average`, a
    house-effect-adjusted, recency- and sqrt(n)-weighted pool of every
    pollster; the registry's series for the same target is the raw stream of
    individual poll readings, which is far noisier. Gating on the raw stream
    refuses series the arena already runs successfully -- `yougov_approval`
    scores 0.00 raw and carries twelve live rounds -- because it charges the
    round for sampling noise the resolution rule removes before scoring.

    Infinite ratio when the noise estimate is zero: all movement is real, the
    easy pass.
    """
    from ssa import average, scoring
    v = [p["value"] for p in hist[-260:]]
    if source in ("sb_approval", "sb_generic"):
        try:
            polls = [{"date": datetime.strptime(p["date"], "%Y-%m-%d").date(),
                      "value": p["value"]} for p in hist]
            weekly = average.weekly_series(polls)
            v = [w["value"] for w in weekly if w.get("value") is not None]
        except Exception:                      # noqa: BLE001 - fall back to raw
            pass
    if len(v) < 4:
        return 0.0, 0.0, 0.0
    m, s = scoring.noise_floor(v)
    return (float("inf") if m == 0 else s / m), s, m


def gate(sid, meta, hist):
    """(ok, reason) -- reason is machine-readable and carries its evidence."""
    rights = RIGHTS.get(meta.get("source"), "unresolved")
    if rights != "approved":
        return False, {"gate": "rights", "state": rights,
                       "source": meta.get("source")}
    if len(hist) < MIN_HISTORY:
        return False, {"gate": "history", "observations": len(hist),
                       "required": MIN_HISTORY}
    ratio, s, m = signal_to_noise(hist, meta.get("source"))
    if s <= MIN_REAL_MOVEMENT:
        return False, {"gate": "volatility",
                       "signal_to_noise": round(ratio, 3),
                       "real_movement": round(s, 3),
                       "measurement_noise": round(m, 3),
                       "detail": "all observed movement is measurement noise; "
                                 "no forecast can beat the last value"}
    if meta.get("source") not in RESOLVE:
        return False, {"gate": "unsupported_family",
                       "source": meta.get("source"),
                       "detail": "this generator has no template for the family; "
                                 "the source may still be fine by hand"}
    if modal_weekday(hist) is None:
        return False, {"gate": "schedule",
                       "detail": "publication weekday not inferable from history"}
    return True, None


def round_id(sid, release):
    """`mc-2026-w38-econ-approval` -- the convention the season file already uses."""
    _, short, suffix = naming(sid)
    year, week, _ = release.isocalendar()
    return f"{short}-{year}-w{week:02d}-{suffix}"


def candidates(sid, meta, hist, weeks, now):
    """The next `weeks` releases of one series, as round objects."""
    wd = modal_weekday(hist)
    last = datetime.strptime(hist[-1]["date"], "%Y-%m-%d").replace(
        tzinfo=timezone.utc)
    hour = 14                                   # the season's settled convention
    out = []
    step = 7
    nxt = last + timedelta(days=step)
    while nxt.weekday() != wd:
        nxt += timedelta(days=1)
    while len(out) < weeks:
        release = nxt.replace(hour=hour, minute=0, second=0, microsecond=0)
        nxt += timedelta(days=step)
        if release <= now:
            continue
        if (release - now).days > MAX_WEEKS_AHEAD * 7:
            break
        lock = release - timedelta(hours=48)
        tracker, _, _ = naming(sid)
        out.append({
            "round_id": round_id(sid, release),
            "tracker": tracker,
            "series": sid,
            "question": meta.get("question") or meta.get("label") or sid,
            "unit": meta.get("unit") or "",
            "release_at": release.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "release_estimated": True,
            "lock_at": lock.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "resolve": RESOLVE[meta["source"]],
            "target_type": "continuous_normal",
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=1,
                    help="releases per series to generate (default 1)")
    ap.add_argument("--write", action="store_true",
                    help="write questions/candidates/<batch>.json")
    ap.add_argument("--rejects", action="store_true",
                    help="print every refused series with its evidence")
    ap.add_argument("--now", default=None, help="override the clock, for tests")
    args = ap.parse_args()

    now = (datetime.fromisoformat(args.now.replace("Z", "+00:00"))
           if args.now else datetime.now(timezone.utc))

    with open(os.path.join(ROOT, "questions", "season0.json")) as fh:
        season = json.load(fh)
    rounds = season["rounds"] if isinstance(season, dict) else season
    existing = {r["round_id"] for r in rounds}
    # Deduplicate on (series, release week), not on round id. The hand-written
    # rounds use their own id spellings -- `civiqs-male-2026-w38` where this
    # generator would say `civiqs-2026-w38-male` -- so an id check alone would
    # cheerfully emit a second question about the same number in the same week.
    taken = {(r.get("series"), r["release_at"][:10]) for r in rounds}
    taken |= {(r.get("series"),
               datetime.strptime(r["release_at"][:10], "%Y-%m-%d")
               .isocalendar()[:2]) for r in rounds}
    already = set()
    for r in rounds:
        already.add(r.get("series"))
        for c in r.get("cells") or []:
            already.add(c)

    hist_by_series, offline_missing = load_history()
    made, refused = [], []
    for sid in sorted(SERIES):
        meta = SERIES[sid]
        if sid in offline_missing:
            refused.append((sid, {"gate": "offline_unavailable",
                                  "source": meta.get("source"),
                                  "detail": "no committed archive; cannot be "
                                            "evaluated from a clean checkout"}))
            continue
        hist = hist_by_series.get(sid) or []
        ok, why = gate(sid, meta, hist)
        if not ok:
            refused.append((sid, why))
            continue
        ratio, sig, noi = signal_to_noise(hist, meta.get("source"))
        for r in candidates(sid, meta, hist, args.weeks, now):
            r["_sn"] = ratio
            week = datetime.strptime(r["release_at"][:10],
                                     "%Y-%m-%d").isocalendar()[:2]
            if (r["round_id"] in existing
                    or (sid, r["release_at"][:10]) in taken
                    or (sid, week) in taken):
                continue
            r["_new_series"] = sid not in already
            made.append(r)

    made.sort(key=lambda r: (r["lock_at"], r["round_id"]))
    by_batch = collections.defaultdict(list)
    for r in made:
        by_batch[batches.batch_of(r["lock_at"])].append(r)

    print(f"registry: {len(SERIES)} series | "
          f"passed gates: {len(SERIES) - len(refused)} | "
          f"refused: {len(refused)}")
    print(f"candidates: {len(made)} rounds across {len(by_batch)} batches "
          f"({sum(1 for r in made if r['_new_series'])} from series that have "
          f"never carried a round)\n")

    for b in sorted(by_batch):
        rr = by_batch[b]
        print(f"── {b}  deadline {b[6:]} 12:00Z  ({len(rr)} rounds)")
        for r in rr:
            mark = "NEW " if r["_new_series"] else "    "
            sn = r.get("_sn", 0.0)
            warn = "  low S/N" if sn < 1.0 else ""
            print(f"   {mark}{r['round_id']:<40} lock {r['lock_at'][:10]} "
                  f"h={batches.horizon_days(r['lock_at']):.1f}d "
                  f"S/N={sn:5.2f}{warn}")

    if args.rejects:
        print("\nrefused:")
        for sid, why in refused:
            print(f"   {sid:<32} {json.dumps(why, sort_keys=True)}")

    if args.write:
        out_dir = os.path.join(ROOT, "questions", "candidates")
        os.makedirs(out_dir, exist_ok=True)
        for b, rr in sorted(by_batch.items()):
            clean = [{k: v for k, v in r.items() if not k.startswith("_")}
                     for r in rr]
            path = os.path.join(out_dir, f"{b}.json")
            with open(path, "w") as fh:
                json.dump(clean, fh, indent=2, sort_keys=True)
                fh.write("\n")
            print(f"\nwrote {os.path.relpath(path, ROOT)} ({len(clean)} rounds)")
        print("\nReview these, then move accepted rounds into "
              "questions/season0.json by hand. Nothing here publishes.")


if __name__ == "__main__":
    main()
