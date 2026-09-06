"""Generate the next weekly bundle of rounds from the registry. Never publishes.

  python tools/generate_rounds.py                    # candidates + review diff
  python tools/generate_rounds.py --through 2026-12-01  # the rest of season 0
  python tools/generate_rounds.py --write            # write the candidate file

Rounds used to be hand-written, one JSON object at a time, which is why season 0
runs out in late September: somebody wrote questions as far ahead as they had
patience for. That is also why registered series can be present without a safe
question template or forward release calendar.

This turns both into one command. It reads the registry, applies the gates that
`docs/sources.md` sets out, and emits candidates for review. A source date is
used as a release date only when its adapter explicitly says that is what the
date means. **It never writes into `questions/season0.json`.** A human reads the
diff and moves the ones they accept, which is the whole reason the frozen
season file stays trustworthy: no round appears in it that a person did not
put there.

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
- `schedule`: the source must carry an explicit forward release contract. A
  poll field midpoint is an observation label, not a publication date, even
  when its weekday happens to be regular.

**Determinism.** Same registry plus same archive gives byte-identical output:
ids, wording, units, release and lock times, resolution rule and target type are
all derived, none sampled. Civiqs uses its registry-declared Friday sampling
contract, whose adapter defines each date as the value displayed that day.
Wikipedia rankings roll the reviewed week contract forward. SCE rounds roll the
reviewed month forward on release days copied by hand from the NY Fed calendar.
Silver Bulletin dates are poll field midpoints, so a tracker is scheduled only
from a publication calendar the registry records and the archived sheet
confirms.
"""
import argparse
import collections
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import batches                                    # noqa: E402
from ssa import inventory                                  # noqa: E402
from ssa import profile_round                             # noqa: E402
from ssa import ranking_round                              # noqa: E402
from ssa.adapters import silverbulletin as sb              # noqa: E402
from ssa.series import PUBLICATION, SERIES                 # noqa: E402

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

# How far ahead a generated release may be scheduled. Beyond this the declared
# calendar is still an increasingly distant projection.  ``--through`` is the
# explicit operator override for a reviewed season horizon; it is date-bounded
# and still writes candidates only, never approved rounds.
MAX_WEEKS_AHEAD = 8

# Rights state per source, read from the source inventory.
#
# `approved` means we may retrieve it the way we do and publish derived values.
# Anything not listed there is treated as unresolved and generates nothing, so
# adding an adapter cannot quietly add rounds.
#
# **This used to be a dict typed out here, and that was the bug.** The rights
# decision was written down twice -- once as narrative in `docs/sources.md`,
# once as this dict -- with nothing to keep them in step. They had already
# drifted: `trends_basket` was missing here, so the gate read it as
# `unresolved` and refused it, while three hand-written Trends basket rounds
# ran live in `questions/season0.json`. `ssa/inventory.py` is now the one
# table, it carries the evidence next to each state, and this is a view of it.
RIGHTS = inventory.rights_table()

# Resolution wording per source family. A generated round states how it will be
# settled in the same words the hand-written rounds use, because the resolution
# rule is part of the question and an entrant is graded on it.
RESOLVE = {
    "sb_approval": "adjusted average at the release date",
    "sb_generic": "adjusted average at the release date",
    "civiqs": "dashboard Friday value, from the daily archive",
}

# Explicit release contracts, not weekday patterns inferred from target data.
# Civiqs' adapter documents its date as the value displayed on that day and
# every registered Civiqs series asks it to sample Fridays (weekday 4).  The
# season's current reviewed Civiqs rounds use 14:00Z; changing that time is a
# reviewed calendar change, not something history can infer.
CIVIQS_RELEASE_HOUR_UTC = 14
FIELD_DATE_SOURCES = frozenset({"sb_approval", "sb_generic"})

# A recorded calendar is checked against the newest archived sheet before it
# schedules anything: over the last CHECKED_WAVES entries, each entry off the
# declared weekday is a slip, and so is each gap between entries not within
# MAX_GAP_DAYS; more than MAX_SLIPS refuse the tracker. One slip is tolerated
# because a Monday holiday moves an Economist/YouGov entry to Wednesday, which
# also stretches that gap to eight days: MAX_GAP_DAYS is 8, not 7, so the
# holiday costs one slip rather than two. More than one is a calendar that
# moved, or an item the pollster asks only some weeks.
# Two Monday holidays four weeks apart refuse the tracker for the weeks both
# sit in the window, and the refusal says so.
CHECKED_WAVES = 8
MAX_SLIPS = 1
MAX_GAP_DAYS = 8

# Series this generator deliberately stops producing rounds for, and why.
#
# A retired template is not a failed gate. The source is fine, the rights are
# fine, the history is fine -- somebody decided the *question* was the wrong
# one to keep asking. Reporting that as `unsupported_family` would send the
# next reader off to write the missing template, which is the opposite of what
# is wanted, so it gets its own reason with the decision attached.
RETIRED_TEMPLATES = {
    "wiki_views_trump":
        "issue #48 retires single-page weekly view totals in favour of the "
        "top-10 ranking round. One article's weekly total is a level question "
        "whose movement is dominated by whether that person was in the news, "
        "and the ranking round asks the same attention question over a defined "
        "universe with a top-weighted loss. The two hand-written rounds stay; "
        "no more are generated.",
    "wiki_views_taylor_swift":
        "issue #48 retires single-page weekly view totals in favour of the "
        "top-10 ranking round. See `wiki_views_trump`.",
}

# Families this generator deliberately has no template for, and why.
#
# Like RETIRED_TEMPLATES these are decisions, not gaps. Reporting them as
# `unsupported_family` would send the next reader off to write a template that
# was considered and declined.
DECLINED_FAMILIES = {
    "hhpoll":
        "Harvard-Harris announces no release calendar and has no derivable "
        "URL; a wave enters the archive when a maintainer fetches its PDF, so "
        "no forward release can be scheduled. The hand-written rounds resolve "
        "on the next wave published after their lock; write any further one "
        "the same way.",
    "trends_basket":
        "the five basket cells are asked jointly by the trends-basket profile "
        "round, which trends_candidates rolls forward; a scalar twin of a "
        "cell is allowed but not generated.",
    "trends":
        "single-query index rounds ran for one reviewed week (2026-08-29), "
        "and one week is not a contract to extend; the basket share round "
        "asks the same queries on a scale that cancels the sampling draw a "
        "raw index carries.",
    "yougov_xtab":
        "the sixteen crosstab cells are asked jointly by the yougov-xtab "
        "profile round, which xtab_candidates rolls forward one wave a week; "
        "a scalar twin of a cell would be the same number under a second "
        "scoring rule, and ten of the sixteen cells carry no weekly signal "
        "on their own.",
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


def sb_rows(source):
    """The newest archived Silver Bulletin sheet, parsed."""
    directory = os.path.join(ROOT, "sources", source)
    newest = sorted(f for f in os.listdir(directory) if f.endswith(".csv"))[-1]
    with open(os.path.join(directory, newest)) as fh:
        return sb.parse(fh.read())


def entry_days(meta):
    """The day each of the series' waves entered the sheet, in wave order."""
    polls = (sb.approval_polls if meta["source"] == "sb_approval"
             else sb.generic_ballot_polls)
    recs = polls(rows=sb_rows(meta["source"]), **meta["filters"])
    by_wave = {(r["start_date"], r["end_date"]): r["created"] for r in recs}
    return [by_wave[k] for k in sorted(by_wave)]


def load_history():
    """Every registered series as {id: [{date, value}]}, from the archive.

    Offline by construction: the committed vintages under `sources/` are what
    the live pipeline already fetched, so generation is reproducible from a
    clean checkout and cannot be made to depend on a source being reachable
    the day someone runs it.
    """
    from ssa.adapters import civiqs as civiqs_adapter
    src = {}
    # Only a family the archive can schedule needs history: Civiqs from its
    # dated snapshots, Silver Bulletin from the newest archived sheet.
    # Everything else is refused before a history or network call is
    # considered. This is the load-bearing offline rule: the previous
    # version called ``build_all`` over the entire registry, which
    # fetched Conference Board, SCE, Civiqs, Trends and Wikipedia even though
    # none of those source families could safely produce a scalar candidate. A
    # supposedly deterministic command could therefore hang for 45 seconds or
    # mutate today's archives.
    #
    # Civiqs has one committed archive per registered series.  Build every
    # override explicitly with ``fetch=False`` so ``build_all`` has no missing
    # key it could interpret as permission to call the live dashboard.
    from ssa import series as registry
    all_series = dict(registry.SERIES)
    supported = {sid: meta for sid, meta in all_series.items()
                 if meta.get("source") in ("civiqs", "sb_approval", "sb_generic")}
    src["civiqs"] = {}
    for sid, meta in sorted(supported.items()):
        if meta.get("source") != "civiqs":
            continue
        cfg = meta["civiqs"]
        src["civiqs"][sid] = civiqs_adapter.as_displayed(
            cfg["name"], cfg.get("filters"), choice=cfg.get("choice"),
            net=cfg.get("net", False), weekday=cfg.get("weekday"), fetch=False)
    src["sb_approval"] = sb_rows("sb_approval")
    src["sb_generic"] = sb_rows("sb_generic")

    for sid in tuple(registry.SERIES):
        if sid not in supported:
            registry.SERIES.pop(sid)
    try:
        built = registry.build_all(src)
    finally:
        # Restore both membership and insertion order.  Registry order is not
        # part of generation (all output is sorted), but a read-only command
        # must not leave a process-global registry rearranged for its caller.
        registry.SERIES.clear()
        registry.SERIES.update(all_series)
    return built


def schedule_contract(sid, meta, hist):
    """(``{weekday, hour}``, reason) from explicit source date semantics; a
    Silver Bulletin calendar also carries its reviewed ``resolve`` wording.

    A regular pattern is not evidence that a date is a release date. Silver
    Bulletin deliberately labels its series with poll field midpoints; using
    their modal weekday previously generated Saturday Morning Consult releases
    while the reviewed calendar said Wednesday. A tracker is scheduled only
    from the calendar the registry records, and only while the newest
    archived sheet agrees with it.
    """
    source = meta.get("source")
    if source in FIELD_DATE_SOURCES:
        calendar = PUBLICATION.get(meta.get("tracker"))
        if not calendar:
            return None, (
                "Silver Bulletin series dates are poll field midpoints, not "
                "publication dates; no publication calendar is recorded for "
                f"{meta.get('tracker')}")
        # The round releases the day after the wave enters: any later and
        # its lock, 48 hours earlier, falls on or after the entry.
        if (calendar["release"] - calendar["entry"]) % 7 != 1:
            raise ValueError(
                f"{meta['tracker']} would release on weekday "
                f"{calendar['release']} but its wave enters on "
                f"{calendar['entry']}; the lock must precede the entry")
        days = entry_days(meta)[-CHECKED_WAVES:]
        if len(days) < CHECKED_WAVES:
            return None, (f"only {len(days)} waves in the sheet; a calendar "
                          f"is checked against the last {CHECKED_WAVES}")
        off = sum(d.weekday() != calendar["entry"] for d in days)
        gaps = sum(not 0 < (d - p).days <= MAX_GAP_DAYS
                   for p, d in zip(days, days[1:]))
        if off + gaps > MAX_SLIPS:
            return None, (
                f"{off + gaps} slips in the last {CHECKED_WAVES} waves: {off} "
                f"entered off the declared weekday, {gaps} gaps not within "
                f"{MAX_GAP_DAYS} days")
        return {"weekday": calendar["release"], "hour": calendar["hour"],
                "resolve": calendar["resolve"]}, None
    if source != "civiqs":
        return None, "no explicit forward release calendar is integrated"
    weekday = (meta.get("civiqs") or {}).get("weekday")
    if not isinstance(weekday, int) or not 0 <= weekday <= 6:
        return None, "Civiqs registry row has no valid sampled weekday"
    mismatched = [p.get("date") for p in hist[-26:]
                  if datetime.strptime(p["date"], "%Y-%m-%d").weekday()
                  != weekday]
    if mismatched:
        return None, (
            f"Civiqs archive violates its registry weekday {weekday}; "
            f"first mismatch {mismatched[0]}")
    return {"weekday": weekday, "hour": CIVIQS_RELEASE_HOUR_UTC}, None


def weekly_movement(hist, days=7, window=60):
    """Mean absolute change over `days`, in the series' own units.

    Reported next to the signal-to-noise ratio because that ratio is toothless
    on a modelled source. `scoring.noise_floor` reads a smoothed series as
    having no measurement noise at all -- three Civiqs cells come back with an
    infinite ratio -- so a cell that moves 0.29 points a week passes a gate
    meant to catch exactly that. Until the threshold is set properly (that is
    metric-design work, and it should be set against resolved rounds rather
    than guessed here), the honest move is to put the number a reviewer would
    actually judge on in front of them.
    """
    v = [p["value"] for p in hist[-window:]]
    if len(v) <= days:
        return 0.0
    return sum(abs(v[i] - v[i - days])
               for i in range(days, len(v))) / (len(v) - days)


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
    # Two verdicts open this gate, and the difference is about publishing the
    # retrieved bodies rather than about scheduling: see
    # `inventory.GENERATING_RIGHTS`. Compared against the inventory's own tuple
    # so adding a third verdict cannot silently leave this line behind.
    if rights not in inventory.GENERATING_RIGHTS:
        return False, {"gate": "rights", "state": rights,
                       "source": meta.get("source")}
    if sid in RETIRED_TEMPLATES:
        return False, {"gate": "retired_template",
                       "source": meta.get("source"),
                       "detail": RETIRED_TEMPLATES[sid]}
    if meta.get("source") in DECLINED_FAMILIES:
        return False, {"gate": "declined_family",
                       "source": meta.get("source"),
                       "detail": DECLINED_FAMILIES[meta["source"]]}
    if meta.get("source") not in RESOLVE:
        return False, {"gate": "unsupported_family",
                       "source": meta.get("source"),
                       "detail": "this generator has no template for the family; "
                                 "the source may still be fine by hand"}
    _, schedule_reason = schedule_contract(sid, meta, hist)
    if schedule_reason:
        return False, {"gate": "schedule", "source": meta.get("source"),
                       "detail": schedule_reason}
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
    return True, None


def round_id(sid, release):
    """`mc-2026-w38-econ-approval` -- the convention the season file already uses."""
    _, short, suffix = naming(sid)
    year, week, _ = release.isocalendar()
    return f"{short}-{year}-w{week:02d}-{suffix}"


def publishable(lock, now):
    """Whether a round locking at `lock` can still reach entrants.

    A round belongs to the batch whose deadline is the last Monday 12:00Z
    before its lock, and every entrant answers that batch by that one moment.
    Once the deadline has passed there is no longer a way for anyone to file
    against the round, so generating it produces a question that would be
    listed, never answered, and then scored against a null nobody competed
    with. `docs/submission-window.md` states the rule; this is where the
    generator obeys it.

    Note this is strictly tighter than "the release is in the future". The old
    check let through rounds locking two days out whose deadline was already
    hours in the past -- a whole batch of them on any run made after Monday
    noon.
    """
    return batches.deadline_for(lock) > now


def candidates(sid, meta, hist, weeks, now, through=None):
    """The next releases, count-bounded or explicitly date-bounded."""
    calendar, reason = schedule_contract(sid, meta, hist)
    if reason:
        raise ValueError(f"{sid}: no safe release schedule: {reason}")
    wd = calendar["weekday"]
    last = datetime.strptime(hist[-1]["date"], "%Y-%m-%d").replace(
        tzinfo=timezone.utc)
    hour = calendar["hour"]
    out = []
    step = 7
    nxt = last + timedelta(days=step)
    while nxt.weekday() != wd:
        nxt += timedelta(days=1)
    while through is not None or len(out) < weeks:
        release = nxt.replace(hour=hour, minute=0, second=0, microsecond=0)
        nxt += timedelta(days=step)
        if through is not None and release.date() > through:
            break
        if release <= now:
            continue
        if through is None and (release - now).days > MAX_WEEKS_AHEAD * 7:
            break
        lock = release - timedelta(hours=48)
        # A round whose batch predates the cutover cannot be published. Its
        # deadline is its own lock, `bundle` refuses to build a batch with no
        # common deadline, and its publication date has already passed -- so
        # only the in-house harness could ever answer it. Generating one wastes
        # a reviewer's attention on a round that can never reach a
        # participant, which is the failure this whole tool exists to stop.
        if not batches.governed_by_batch(lock.strftime("%Y-%m-%dT%H:%M:%SZ")):
            continue
        if not publishable(lock, now):
            continue
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
            "resolve": calendar.get("resolve", RESOLVE[meta["source"]]),
            "target_type": "continuous_normal",
        })
    return out


# --- the weekly Wikipedia top-10 ranking round -------------------------------
#
# The one generated family that does not come out of the series registry, and
# it could not: the registry holds streams of (date, value) scalars, and this
# round's observation is an ordered list of ten article titles drawn from six
# million. `ssa/ranking_round.py` owns the contract; this only rolls the
# calendar forward.
#
# **The template is the newest reviewed round, not a constant typed here.**
# Every field a human settled -- the RBO parameter, the exclusion rule id, the
# project and access slice, how long before the week the round locks and how
# long after it resolves -- is read off the round they approved. A reviewer who
# changes the contract carries that change forward by promoting one round, and
# there is no second copy of the contract to drift out of step.
#
# What *is* a constant is the question wording, and it is checked against the
# reviewed round on every run: `WIKI_QUESTION` is rebuilt for the template
# round's own week and must reproduce that round's question exactly, or
# generation raises. A generated round that reads differently from the
# reviewed one is a different question wearing the same name -- and entrants
# are graded on the wording, not on the round id.

WIKI_SERIES = "wiki_top10_en"
WIKI_KIND = "wiki_top10"

# The wording spells the length out in words ("the ten article titles"), so it
# is not a substitution away from working at another length. A round with a
# different `length` needs its own reviewed sentence, and generation refuses
# rather than interpolating one.
WIKI_LENGTH = 10

WIKI_QUESTION = (
    "The ordered top-10 articles on the English Wikipedia by pageviews for the "
    "week {week}. The seven daily top-1000 lists published by Wikimedia are "
    "summed per article and ranked; Main_Page and every non-article namespace "
    "(Special:, Wikipedia:, Portal:, Help:, File:, Template:, Category:, "
    "Draft:, User:, Talk: and their _talk: variants) are excluded. Give the "
    "ten article titles in order, rank 1 first.")


# --- the Google Trends brand basket ----------------------------------------
#
# A weekly five-cell profile round. Templated from the reviewed rounds for the
# same reason the wiki family is: the season already runs three of these, so
# generation extends a reviewed contract rather than inventing one. It matters
# more here than anywhere else in October, because every other candidate that
# month is a Civiqs scalar -- without this the month has one answer shape.

TRENDS_KIND = "trends_basket"
TRENDS_CELLS = ("trends_share_tesla", "trends_share_iphone",
                "trends_share_samsung", "trends_share_netflix",
                "trends_share_disney")

# The invariant half of the reviewed wording. The reviewed rounds append one
# more sentence -- "September is Apple's announcement window, the largest
# regular swing in this basket" -- which is a claim about September and is
# false in October. `trends_template` proves this constant reproduces the
# reviewed rounds *with* that note supplied, and generated rounds carry no note
# at all: a template may repeat a question a human approved, and may not invent
# a seasonal hint nobody reviewed.
TRENDS_QUESTION = (
    "Google Trends, United States: the share of weekly search interest taken "
    "by each of Tesla, iPhone, Samsung, Netflix and Disney for the week "
    "{week}. All five are measured in one comparison request, so their weekly "
    "indices sit on a single shared scale; forecast each brand's percentage of "
    "the five-brand total. The five shares add to 100.")

TRENDS_REVIEWED_NOTE = (
    " September is Apple's announcement window, the largest regular swing in "
    "this basket.")


def trends_template(rounds):
    """(newest reviewed basket round, lock offset, release offset).

    Offsets are measured from the measured week's own boundaries, as with the
    wiki family: this round also locks before its week begins, so a spacing
    expressed from `release_at` would be meaningless the moment the week moved.

    Raises when the reviewed rounds disagree, when the basket changes, or when
    `TRENDS_QUESTION` no longer rebuilds the wording a human approved. Each of
    those would otherwise generate a question nobody reviewed under a name that
    says it was.
    """
    got = sorted((r for r in rounds
                  if r.get("cells") and tuple(r["cells"]) == TRENDS_CELLS),
                 key=lambda r: r["release_at"])
    if not got:
        raise ValueError(
            "no reviewed Google Trends basket round to template from; this "
            "generator extends an existing contract rather than inventing one")
    offsets = set()
    for r in got:
        end = datetime.strptime(r["release_at"][:10], "%Y-%m-%d").replace(
            tzinfo=timezone.utc)
        start = end - timedelta(days=6)
        lock = datetime.fromisoformat(r["lock_at"].replace("Z", "+00:00"))
        rel = datetime.fromisoformat(r["release_at"].replace("Z", "+00:00"))
        offsets.add((lock - start, rel - end))
        # The invariant sentence has to reproduce exactly; a reviewed round
        # may then append editorial context for its own week, and a
        # rolled-forward one appends nothing. Checking for equality with the
        # note attached would make the family unable to extend itself: the
        # moment a generated week is promoted it becomes a reviewed round with
        # no note, and the equality would fail against the very rounds this
        # produced. Checking the prefix keeps the property that matters --
        # nothing is generated whose question a human did not approve -- and
        # drops only the claim that every week carries the same aside.
        rebuilt = TRENDS_QUESTION.format(
            week=week_phrase(start.date(), end.date()))
        if not r["question"].startswith(rebuilt):
            raise ValueError(
                f"TRENDS_QUESTION no longer reproduces {r['round_id']}'s "
                "wording; the reviewed question changed and the template did "
                "not")
        trailing = r["question"][len(rebuilt):]
        if trailing and not trailing.startswith(" "):
            raise ValueError(
                f"{r['round_id']} continues the reviewed sentence without a "
                "break; the template would generate a question that reads as "
                "a fragment of it")
    if len(offsets) != 1:
        raise ValueError(
            f"the reviewed Trends basket rounds use {len(offsets)} different "
            f"lock/release spacings: {sorted(map(str, offsets))}")
    lock_off, rel_off = offsets.pop()
    return got[-1], lock_off, rel_off


def trends_candidates(rounds, weeks, now, through=None):
    """Future basket rounds, count-bounded or explicitly date-bounded."""
    tpl, lock_off, rel_off = trends_template(rounds)
    taken = {r["round_id"] for r in rounds}
    end = datetime.strptime(tpl["release_at"][:10], "%Y-%m-%d").date()
    out = []
    start = end - timedelta(days=6) + timedelta(days=7)
    while through is not None or len(out) < weeks:
        wk_end = start + timedelta(days=6)
        lock = datetime(start.year, start.month, start.day,
                        tzinfo=timezone.utc) + lock_off
        release = datetime(wk_end.year, wk_end.month, wk_end.day,
                           tzinfo=timezone.utc) + rel_off
        wk_start, start = start, start + timedelta(days=7)
        if through is not None and release.date() > through:
            break
        if lock <= now or not publishable(lock, now):
            continue
        if through is None and (release - now).days > MAX_WEEKS_AHEAD * 7:
            break
        r = {
            "round_id": f"trends-basket-{wk_end.isoformat()}",
            "tracker": tpl["tracker"],
            "profile_noun": tpl.get("profile_noun", "brand"),
            "series": tpl["series"],
            "cells": list(TRENDS_CELLS),
            "question": TRENDS_QUESTION.format(
                week=week_phrase(wk_start, wk_end)),
            "unit": tpl["unit"],
            "release_at": release.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "release_estimated": tpl.get("release_estimated", False),
            "lock_at": lock.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "resolve": re.sub(r"\d{4}-\d{2}-\d{2}", wk_end.isoformat(),
                              tpl["resolve"]),
            "target_type": tpl["target_type"],
        }
        # Validate through the module that will have to score it, at generation
        # time rather than at listing time -- the same reason the wiki family
        # calls `ranking_round.spec_for` here.
        profile_round.cells_for(r)
        if r["round_id"] in taken:
            continue
        out.append(r)
    return out


# --- the Civiqs sixteen-cell demographic profile ------------------------------
#
# The headline round type, and until this existed it had three instances: three
# hand-written weeks in September and then nothing. The energy score over a
# sixteen-dimensional vector is the thing this benchmark does that a scalar
# board cannot, and it was scheduled to stop.
#
# Simpler to template than the Trends basket: the three reviewed rounds share
# their cells, their unit and their resolve rule byte for byte, and their
# questions differ only in the Friday they name.

CIVIQS_PROFILE_SERIES = "civiqs_net_approval"
CIVIQS_PROFILE_ID = "civiqs-profile-{year}-w{week:02d}"

CIVIQS_PROFILE_QUESTION = (
    "Civiqs daily tracker, Trump net approval among registered voters, full "
    "16-cell demographic profile, {day} dashboard values")

CIVIQS_PROFILE_CELLS = 16


def _friday_phrase(d):
    """`Friday Sep 11`, the way the reviewed rounds write it."""
    return f"{d.strftime('%A %b')} {d.day}"


def civiqs_profile_template(rounds):
    """(newest reviewed profile round, lock offset from the release).

    Raises when the reviewed rounds disagree on their cells, their spacing or
    their wording. Sixteen cells is not a substitution away from working at
    another width -- the question says "16-cell" in words -- so a different
    vector needs its own reviewed sentence rather than an interpolated one.
    """
    # Every profile round on this series, whatever its width. Filtering to
    # sixteen here would silently exclude a round somebody had narrowed, and
    # keep templating the old width from its older siblings -- the family would
    # drift without anything raising. Selected by series, then required to
    # agree.
    got = sorted((r for r in rounds
                  if r.get("cells") and r.get("series") == CIVIQS_PROFILE_SERIES),
                 key=lambda r: r["release_at"])
    if not got:
        raise ValueError(
            "no reviewed Civiqs 16-cell profile round to template from; this "
            "generator extends an existing contract rather than inventing one")
    shapes = {(tuple(r["cells"]), r["unit"], r["resolve"]) for r in got}
    if len(shapes) != 1:
        raise ValueError(
            f"the reviewed Civiqs profile rounds disagree on cells, unit or "
            f"resolve rule across {len(shapes)} variants")
    if len(got[-1]["cells"]) != CIVIQS_PROFILE_CELLS:
        raise ValueError(
            f"{got[-1]['round_id']} has {len(got[-1]['cells'])} cells, but the "
            f"reviewed wording says {CIVIQS_PROFILE_CELLS} in words; a "
            "different vector needs its own reviewed sentence, not an "
            "interpolated one")
    offsets = set()
    for r in got:
        rel = datetime.fromisoformat(r["release_at"].replace("Z", "+00:00"))
        lock = datetime.fromisoformat(r["lock_at"].replace("Z", "+00:00"))
        offsets.add(lock - rel)
        rebuilt = CIVIQS_PROFILE_QUESTION.format(
            day=_friday_phrase(rel.date()))
        if rebuilt != r["question"]:
            raise ValueError(
                f"CIVIQS_PROFILE_QUESTION no longer reproduces "
                f"{r['round_id']}'s wording; the reviewed question changed and "
                "the template did not")
    if len(offsets) != 1:
        raise ValueError(
            f"the reviewed Civiqs profile rounds use {len(offsets)} different "
            f"lock spacings: {sorted(map(str, offsets))}")
    return got[-1], offsets.pop()


def civiqs_profile_candidates(rounds, weeks, now, through=None):
    """Future 16-cell profile rounds, count-bounded or date-bounded."""
    tpl, lock_off = civiqs_profile_template(rounds)
    taken = {r["round_id"] for r in rounds}
    rel = datetime.fromisoformat(tpl["release_at"].replace("Z", "+00:00"))
    out = []
    while through is not None or len(out) < weeks:
        rel = rel + timedelta(days=7)
        lock = rel + lock_off
        if through is not None and rel.date() > through:
            break
        if lock <= now or not publishable(lock, now):
            continue
        if through is None and (rel - now).days > MAX_WEEKS_AHEAD * 7:
            break
        year, week = rel.date().isocalendar()[:2]
        r = {
            "round_id": CIVIQS_PROFILE_ID.format(year=year, week=week),
            "tracker": tpl["tracker"],
            "series": tpl["series"],
            "cells": list(tpl["cells"]),
            "question": CIVIQS_PROFILE_QUESTION.format(
                day=_friday_phrase(rel.date())),
            "unit": tpl["unit"],
            "release_at": rel.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "release_estimated": tpl.get("release_estimated", False),
            "lock_at": lock.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "resolve": tpl["resolve"],
            "target_type": tpl["target_type"],
        }
        # Validate through the scorer at generation time, as the other two
        # families do.
        profile_round.cells_for(r)
        if r["round_id"] in taken:
            continue
        out.append(r)
    return out


# --- the Economist/YouGov crosstab profile ----------------------------------
#
# The second population round: the survey's own sixteen crosstab cells rather
# than a model's. One round per wave, rolled forward from the newest reviewed
# round the way the Civiqs profile is -- same cells, same unit, same resolve
# rule byte for byte, and a question that differs only in the week it names.
# The cadence is YouGov's: a wave every week, dated by its field end (a Monday
# in 68 of 83 waves) and in the workbook within days. The round mirrors the
# tracker's topline rounds (`yougov-<year>-w<NN>-approval`) in release and
# lock because it is the same survey; the topline round asks for the wave's
# headline and this one asks for its structure.
#
# The scalar loop refuses the sixteen cell series by name (DECLINED_FAMILIES):
# the profile is the round that wants them.

XTAB_SERIES = "yougov_xtab_approve_dem"     # the anchor the reviewed rounds name
XTAB_ID = "yougov-xtab-{year}-w{week:02d}"

XTAB_QUESTION = (
    "Economist/YouGov weekly tracker: Donald Trump's job approval among US "
    "registered voters, broken into the survey's own sixteen crosstab cells, "
    "for the wave released in the week of {day}. Forecast the percentage "
    "approving in each subgroup as the tracker workbook reports it for that "
    "wave. These are measured cells of one survey, not a model's subgroup "
    "estimates: they move with the national mood and they also move apart, "
    "and the whole profile is scored.")

XTAB_CELLS = 16


def _monday_phrase(d):
    """`Monday Sep 21` -- the Monday of the release's ISO week, which is the
    field-end date YouGov gives the wave in 68 of 83 cases."""
    monday = d - timedelta(days=d.weekday())
    return f"{monday.strftime('%A %b')} {monday.day}"


def xtab_template(rounds):
    """(newest reviewed crosstab round, lock offset from the release).

    Raises when the reviewed rounds disagree on their cells, their spacing or
    their wording, for the reasons `civiqs_profile_template` gives.
    """
    got = sorted((r for r in rounds
                  if r.get("cells") and r.get("series") == XTAB_SERIES),
                 key=lambda r: r["release_at"])
    if not got:
        raise ValueError(
            "no reviewed Economist/YouGov crosstab round to template from; "
            "this generator extends an existing contract rather than "
            "inventing one")
    shapes = {(tuple(r["cells"]), r["unit"], r["resolve"]) for r in got}
    if len(shapes) != 1:
        raise ValueError(
            f"the reviewed crosstab rounds disagree on cells, unit or resolve "
            f"rule across {len(shapes)} variants")
    if len(got[-1]["cells"]) != XTAB_CELLS:
        raise ValueError(
            f"{got[-1]['round_id']} has {len(got[-1]['cells'])} cells, but the "
            f"reviewed wording says sixteen in words; a different vector needs "
            "its own reviewed sentence, not an interpolated one")
    offsets = set()
    for r in got:
        rel = datetime.fromisoformat(r["release_at"].replace("Z", "+00:00"))
        lock = datetime.fromisoformat(r["lock_at"].replace("Z", "+00:00"))
        offsets.add(lock - rel)
        rebuilt = XTAB_QUESTION.format(day=_monday_phrase(rel.date()))
        if rebuilt != r["question"]:
            raise ValueError(
                f"XTAB_QUESTION no longer reproduces {r['round_id']}'s "
                "wording; the reviewed question changed and the template did "
                "not")
    if len(offsets) != 1:
        raise ValueError(
            f"the reviewed crosstab rounds use {len(offsets)} different lock "
            f"spacings: {sorted(map(str, offsets))}")
    return got[-1], offsets.pop()


def xtab_candidates(rounds, weeks, now, through=None):
    """Future sixteen-cell crosstab rounds, one per wave."""
    tpl, lock_off = xtab_template(rounds)
    taken = {r["round_id"] for r in rounds}
    rel = datetime.fromisoformat(tpl["release_at"].replace("Z", "+00:00"))
    out = []
    while through is not None or len(out) < weeks:
        rel = rel + timedelta(days=7)
        lock = rel + lock_off
        if through is not None and rel.date() > through:
            break
        if lock <= now or not publishable(lock, now):
            continue
        if through is None and (rel - now).days > MAX_WEEKS_AHEAD * 7:
            break
        year, week = rel.date().isocalendar()[:2]
        r = {
            "round_id": XTAB_ID.format(year=year, week=week),
            "tracker": tpl["tracker"],
            "profile_noun": tpl.get("profile_noun", "subgroup"),
            "series": tpl["series"],
            "cells": list(tpl["cells"]),
            "question": XTAB_QUESTION.format(day=_monday_phrase(rel.date())),
            "unit": tpl["unit"],
            "release_at": rel.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "release_estimated": tpl.get("release_estimated", True),
            "lock_at": lock.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "resolve": tpl["resolve"],
            "target_type": tpl["target_type"],
        }
        profile_round.cells_for(r)
        if r["round_id"] in taken:
            continue
        out.append(r)
    return out


# --- the NY Fed Survey of Consumer Expectations ------------------------------
#
# Two horizons a month, rolled forward from the newest reviewed round the way
# the basket and ranking families are. The one thing nothing here may derive
# is the release day: the workbook dates its rows by survey month, and the day
# is preannounced on the NY Fed calendar rather than fixed to a weekday. So it
# is a hand-entered table, checked against every reviewed round, and the
# family stops at the first month nobody has entered.

SCE_HORIZONS = {"sce_inflation_1y": ("infl1y", "one-year"),
                "sce_inflation_3y": ("infl3y", "three-year")}
SCE_ID = re.compile(r"^sce-(\d{4}-\d{2})-infl[13]y$")

# Release day per survey month, copied by hand from the NY Fed's own calendar
# file: the CSV behind newyorkfed.org/microeconomics/calendar.html, which runs
# to the end of the calendar year. The rows to copy:
#
#   curl -s https://www.newyorkfed.org/medialibrary/research/interactives/data/cmdCalendar/cmdCalendar.csv \
#     | grep 'Survey of Consumer Expectations: Press release'
#
# A row's date is the release day; the survey month is the month before it.
SCE_RELEASES = {
    "2026-08": "2026-09-08",
    "2026-09": "2026-10-07",
    "2026-10": "2026-11-09",
    "2026-11": "2026-12-07",
}

# The invariant sentence of the reviewed wording. Each reviewed round appends
# its own aside (the release date, and for August the July reading); a
# generated round appends nothing, so the check is a prefix match, as in
# `trends_template`.
SCE_QUESTION = (
    "NY Fed Survey of Consumer Expectations, produced by the Federal Reserve "
    "Bank of New York from its rotating panel of about 1,300 US household "
    "heads: the median {horizon}-ahead expected inflation rate for the "
    "{month} survey month.")


def _sce_month(rid):
    """`2026-09`, the survey month an SCE round id names."""
    m = SCE_ID.match(rid)
    if not m:
        raise ValueError(f"{rid} does not name an SCE survey month")
    return m.group(1)


def _month_phrase(month):
    """`September 2026`, the way the reviewed rounds write it."""
    return datetime.strptime(month, "%Y-%m").strftime("%B %Y")


def _next_month(month):
    d = datetime.strptime(month, "%Y-%m")
    return f"{d.year + d.month // 12}-{d.month % 12 + 1:02d}"


def sce_template(rounds):
    """(newest reviewed SCE round, lock offset, release offset).

    The lock is measured from the release and the release from midnight on
    the release day, because the day is what the calendar fixes. Raises when
    the reviewed rounds disagree on that spacing, when `SCE_RELEASES`
    contradicts a reviewed release date, or when `SCE_QUESTION` no longer
    rebuilds the wording a human approved.
    """
    got = sorted((r for r in rounds if r.get("series") in SCE_HORIZONS),
                 key=lambda r: r["release_at"])
    if not got:
        raise ValueError(
            "no reviewed SCE round to template from; this generator extends "
            "an existing contract rather than inventing one")
    offsets = set()
    for r in got:
        month = _sce_month(r["round_id"])
        if SCE_RELEASES.get(month) != r["release_at"][:10]:
            raise ValueError(
                f"SCE_RELEASES disagrees with {r['round_id']}: "
                f"{SCE_RELEASES.get(month)} against {r['release_at'][:10]}")
        rel = datetime.fromisoformat(r["release_at"].replace("Z", "+00:00"))
        lock = datetime.fromisoformat(r["lock_at"].replace("Z", "+00:00"))
        day = rel.replace(hour=0, minute=0, second=0)
        offsets.add((lock - rel, rel - day))
        rebuilt = SCE_QUESTION.format(horizon=SCE_HORIZONS[r["series"]][1],
                                      month=_month_phrase(month))
        if not r["question"].startswith(rebuilt):
            raise ValueError(
                f"SCE_QUESTION no longer reproduces {r['round_id']}'s "
                "wording; the reviewed question changed and the template did "
                "not")
        if _month_phrase(month) not in r["resolve"]:
            raise ValueError(
                f"{r['round_id']}'s resolve rule does not name its survey "
                "month; that name is the one thing the template rewrites")
    if len(offsets) != 1:
        raise ValueError(
            f"the reviewed SCE rounds use {len(offsets)} different "
            f"lock/release spacings: {sorted(map(str, offsets))}")
    lock_off, rel_off = offsets.pop()
    return got[-1], lock_off, rel_off


def sce_candidates(rounds, weeks, now, through=None):
    """(future SCE rounds, the first survey month the calendar does not cover).

    `weeks` counts releases per series, so a month is two rounds. The month
    the calendar lacks is returned for `main` to report, so running out of
    calendar is a refusal and not silence. Beyond MAX_WEEKS_AHEAD the walk
    stops quietly, as the other families do.
    """
    tpl, lock_off, rel_off = sce_template(rounds)
    last = _sce_month(tpl["round_id"])
    month = _next_month(last)
    out = []
    while through is not None or len(out) < 2 * weeks:
        day = SCE_RELEASES.get(month)
        if day is None:
            return out, month
        if day[:7] <= month:
            raise ValueError(
                f"SCE_RELEASES[{month!r}] is {day}, which does not follow the "
                "survey month; a calendar row is dated by its release day")
        release = datetime.strptime(day, "%Y-%m-%d").replace(
            tzinfo=timezone.utc) + rel_off
        lock = release + lock_off
        this, month = month, _next_month(month)
        if through is not None and release.date() > through:
            break
        if lock <= now or not publishable(lock, now):
            continue
        if through is None and (release - now).days > MAX_WEEKS_AHEAD * 7:
            break
        for sid, (suffix, horizon) in SCE_HORIZONS.items():
            out.append({
                "round_id": f"sce-{this}-{suffix}",
                "tracker": tpl["tracker"],
                "series": sid,
                "question": SCE_QUESTION.format(
                    horizon=horizon, month=_month_phrase(this)),
                "unit": tpl["unit"],
                "release_at": release.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "release_estimated": tpl.get("release_estimated", False),
                "lock_at": lock.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "resolve": tpl["resolve"].replace(_month_phrase(last),
                                                  _month_phrase(this)),
                "target_type": tpl["target_type"],
            })
    return out, None


def week_phrase(start, end):
    """`Mon Aug 31 - Sun Sep 6, 2026`, the way the reviewed rounds write it.

    The year is stated once at the end while the week stays inside one, and
    twice when it does not. A week that straddles New Year would otherwise
    read `Mon Dec 28 - Sun Jan 3, 2027` and quietly claim the Monday was in
    2027, which is the kind of wrong that only shows up one week a year.
    """
    if start.year == end.year:
        return (f"{start.strftime('%a %b')} {start.day} - "
                f"{end.strftime('%a %b')} {end.day}, {end.year}")
    return (f"{start.strftime('%a %b')} {start.day}, {start.year} - "
            f"{end.strftime('%a %b')} {end.day}, {end.year}")


def wiki_template(rounds):
    """(newest reviewed wiki top-10 round, lock offset, release offset).

    Offsets are measured from the ranking week's own boundaries -- lock from
    midnight on `week_start`, release from midnight on `week_end` -- because
    that is what the timing actually means here. Unlike a scalar round, this
    one locks *before its week begins*: the answer is accumulated over the
    seven days after the lock, so `release - 48h` would be a lock in the
    middle of the window with three days of the answer already public.

    Raises if the reviewed rounds disagree on those offsets. Two spacings in
    the same family means one of them is a typo, and picking the newest would
    propagate it silently.
    """
    got = [r for r in rounds
           if ranking_round.is_ranking(r)
           and (r.get("ranking") or {}).get("kind") == WIKI_KIND]
    if not got:
        raise ValueError(
            "no reviewed wiki top-10 round to template from; this generator "
            "extends an existing contract rather than inventing one")
    offsets = set()
    for r in got:
        spec = r["ranking"]
        start = datetime.strptime(spec["week_start"], "%Y-%m-%d").replace(
            tzinfo=timezone.utc)
        end = datetime.strptime(spec["week_end"], "%Y-%m-%d").replace(
            tzinfo=timezone.utc)
        lock = datetime.fromisoformat(r["lock_at"].replace("Z", "+00:00"))
        rel = datetime.fromisoformat(r["release_at"].replace("Z", "+00:00"))
        offsets.add((lock - start, rel - end))
    if len(offsets) != 1:
        raise ValueError(
            f"the reviewed wiki top-10 rounds use {len(offsets)} different "
            f"lock/release spacings: {sorted(map(str, offsets))}")
    newest = max(got, key=lambda r: r["ranking"]["week_end"])
    lock_off, rel_off = offsets.pop()
    return newest, lock_off, rel_off


def wiki_candidates(rounds, weeks, now, through=None):
    """Future top-10 rounds, count-bounded or explicitly date-bounded."""
    tpl, lock_off, rel_off = wiki_template(rounds)
    spec = tpl["ranking"]
    if spec.get("length") != WIKI_LENGTH:
        raise ValueError(
            f"{tpl['round_id']} ranks {spec.get('length')} items, but the "
            f"reviewed wording is written for {WIKI_LENGTH}; a different "
            "length needs its own reviewed sentence, not an interpolated one")

    # The wording check. Rebuild the template round's own question from the
    # constant above; if it does not come back identical, the constant and the
    # reviewed round have drifted and every generated round would ask
    # something a human never approved.
    t_start = datetime.strptime(spec["week_start"], "%Y-%m-%d").date()
    t_end = datetime.strptime(spec["week_end"], "%Y-%m-%d").date()
    rebuilt = WIKI_QUESTION.format(week=week_phrase(t_start, t_end))
    if rebuilt != tpl["question"]:
        raise ValueError(
            f"WIKI_QUESTION no longer reproduces {tpl['round_id']}'s wording; "
            "the reviewed question changed and the template did not")

    taken = {r["round_id"] for r in rounds}
    out = []
    start = t_start + timedelta(days=7)
    while through is not None or len(out) < weeks:
        end = start + timedelta(days=6)
        lock = datetime(start.year, start.month, start.day,
                        tzinfo=timezone.utc) + lock_off
        release = datetime(end.year, end.month, end.day,
                           tzinfo=timezone.utc) + rel_off
        wk_start, start = start, start + timedelta(days=7)
        if through is not None and release.date() > through:
            break
        if lock <= now or not publishable(lock, now):
            continue
        if through is None and (release - now).days > MAX_WEEKS_AHEAD * 7:
            break
        r = {
            "round_id": f"wiki-top10-{end.isoformat()}",
            "tracker": tpl["tracker"],
            "series": tpl.get("series") or WIKI_SERIES,
            "question": WIKI_QUESTION.format(week=week_phrase(wk_start, end)),
            "unit": tpl["unit"],
            "release_at": release.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "release_estimated": tpl.get("release_estimated", False),
            "lock_at": lock.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "resolve": tpl["resolve"],
            "target_type": tpl["target_type"],
            "ranking": dict(spec, week_start=wk_start.isoformat(),
                            week_end=end.isoformat()),
        }
        # Validate through the module that will have to score it, at generation
        # time rather than at listing time. A ranking round whose spec does not
        # parse collects forecasts and then cannot be scored, which
        # `ranking_round.spec_for` exists to make impossible.
        ranking_round.spec_for(r)
        if r["round_id"] in taken:
            continue
        out.append(r)
    return out


def main():
    ap = argparse.ArgumentParser()
    horizon = ap.add_mutually_exclusive_group()
    horizon.add_argument("--weeks", type=int,
                         help="releases per series to generate (default 1)")
    horizon.add_argument("--through",
                         help="generate candidates through this release date "
                              "(YYYY-MM-DD), beyond the eight-week preview")
    ap.add_argument("--write", action="store_true",
                    help="write questions/candidates/<batch>.json")
    ap.add_argument("--rejects", action="store_true",
                    help="print every refused series with its evidence")
    ap.add_argument("--now", default=None, help="override the clock, for tests")
    args = ap.parse_args()

    now = (datetime.fromisoformat(args.now.replace("Z", "+00:00"))
           if args.now else datetime.now(timezone.utc))
    weeks = args.weeks if args.weeks is not None else 1
    if weeks < 1:
        ap.error("--weeks must be at least 1")
    try:
        through = (datetime.strptime(args.through, "%Y-%m-%d").date()
                   if args.through else None)
    except ValueError:
        ap.error("--through must be an ISO date (YYYY-MM-DD)")

    with open(os.path.join(ROOT, "questions", "season0.json")) as fh:
        season = json.load(fh)
    rounds = season["rounds"] if isinstance(season, dict) else season
    existing = {r["round_id"] for r in rounds}
    # Deduplicate on (series, release week), not on round id. The hand-written
    # rounds use their own id spellings -- `civiqs-male-2026-w38` where this
    # generator would say `civiqs-2026-w38-male` -- so an id check alone would
    # cheerfully emit a second question about the same number in the same week.
    # Every series a round already asks about in a given week, including the
    # cells of a profile round. Keying on `series` alone missed those: a
    # sixteen-cell profile names its cells in `cells` and carries an unrelated
    # id in `series`, so ten of the sixteen came back as standalone scalar
    # rounds in the same week, asking for the same number twice under two
    # scoring rules. The profile is the round that wants them -- the energy
    # score is where a floor-bound cell still carries information -- and a
    # scalar twin beside it is not a second question, it is the same one.
    def _claims(r):
        """What this round already asks *on the scalar board*.

        A round carrying `cells` is a profile round: its answer is a vector,
        `refresh.build_profile_leaderboard` scores it with the energy score,
        and that board is kept apart from the scalar one on purpose -- a CRPS
        is in points, an energy score is a distance in sixteen-dimensional
        points-space, and no weighting of the two answers a question anyone
        asked. So a profile cell does not occupy the scalar board's slot for
        its series, and a scalar round on the same series in the same week is
        not a duplicate. It is the season's most direct comparison: the same
        number elicited jointly and marginally, scored on two boards where a
        tenth of skill means the same thing.

        This rule was written the other way round first, and it deleted that
        comparison from ten of `civiqs-profile-2026-w38`'s sixteen cells.
        """
        if not r.get("cells"):
            yield r.get("series")
    taken = {(sid, r["release_at"][:10]) for r in rounds for sid in _claims(r)}
    taken |= {(sid, datetime.strptime(r["release_at"][:10], "%Y-%m-%d")
               .isocalendar()[:2])
              for r in rounds for sid in _claims(r)}
    already = set()
    for r in rounds:
        already.add(r.get("series"))
        for c in r.get("cells") or []:
            already.add(c)

    hist_by_series = load_history()
    made, refused = [], []
    for sid in sorted(SERIES):
        if sid in SCE_HORIZONS:
            continue    # rolled forward below; the row is not the template
        meta = SERIES[sid]
        hist = hist_by_series.get(sid) or []
        ok, why = gate(sid, meta, hist)
        if not ok:
            refused.append((sid, why))
            continue
        ratio, sig, noi = signal_to_noise(hist, meta.get("source"))
        move = weekly_movement(hist)
        for r in candidates(sid, meta, hist, weeks, now, through=through):
            r["_sn"] = ratio
            r["_move"] = move
            week = datetime.strptime(r["release_at"][:10],
                                     "%Y-%m-%d").isocalendar()[:2]
            if (r["round_id"] in existing
                    or (sid, r["release_at"][:10]) in taken
                    or (sid, week) in taken):
                continue
            r["_new_series"] = sid not in already
            made.append(r)

    # The Civiqs 16-cell profile: the headline round type, which had three
    # hand-written weeks and no way to continue.
    for r in civiqs_profile_candidates(rounds, weeks, now, through=through):
        r["_sn"] = float("nan")     # a 16-vector has no scalar S/N
        r["_move"] = float("nan")
        r["_new_series"] = False
        made.append(r)

    # The Economist/YouGov crosstab profile: the survey's own sixteen cells,
    # one round per wave.
    for r in xtab_candidates(rounds, weeks, now, through=through):
        r["_sn"] = float("nan")     # a 16-vector has no scalar S/N
        r["_move"] = float("nan")
        r["_new_series"] = False
        made.append(r)

    # The Trends basket: a profile round with no registry row to iterate over,
    # for the same reason the ranking family has none -- its answer is a vector
    # over five series rather than one of them.
    for r in trends_candidates(rounds, weeks, now, through=through):
        r["_sn"] = float("nan")     # a share vector has no scalar S/N
        r["_move"] = float("nan")
        r["_new_series"] = False
        made.append(r)

    # The ranking family, which has no registry row to iterate over.
    for r in wiki_candidates(rounds, weeks, now, through=through):
        r["_sn"] = float("nan")     # a permutation has no signal-to-noise ratio
        r["_new_series"] = False    # three reviewed rounds already ran on it
        made.append(r)

    # SCE: two horizons a month, on the day the NY Fed preannounced. Its rows
    # skip the gate loop, so the rights verdict is read here. The calendar
    # is typed in by hand, so the first month it does not cover is reported
    # rather than silently ending the family.
    rights = RIGHTS.get("sce", "unresolved")
    if rights not in inventory.GENERATING_RIGHTS:
        sce, why = [], {"gate": "rights", "state": rights, "source": "sce"}
    else:
        sce, unentered = sce_candidates(rounds, weeks, now, through=through)
        why = unentered and {
            "gate": "calendar", "source": "sce",
            "detail": f"no release day entered for the {unentered} survey "
                      "month; copy it from the NY Fed calendar into "
                      "SCE_RELEASES"}
    for r in sce:
        r["_sn"] = r["_move"] = float("nan")    # no history is loaded for it
        r["_new_series"] = False
        made.append(r)
    if why:
        refused.extend((sid, why) for sid in SCE_HORIZONS)
    refused.sort(key=lambda x: x[0])

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
        # A batch is published a week before its deadline. Past that moment a
        # round can still legally join it -- the deadline is what governs
        # validity -- but entrants have already read the bundle, so anything
        # added now arrives after the thing they were told to answer. Say so
        # rather than letting a reviewer promote it and wonder why nobody
        # filed.
        lead = batches.published_at(rr[0]["lock_at"])
        late = "  (publication lead passed)" if lead <= now else ""
        print(f"── {b}  deadline {b[6:]} 12:00Z  ({len(rr)} rounds){late}")
        for r in rr:
            mark = "NEW " if r["_new_series"] else "    "
            sn = r.get("_sn", 0.0)
            # A ranking round's answer is a permutation, so `noise_floor` has
            # nothing to measure and the column is blank rather than zero --
            # zero is what the volatility gate refuses, and this is not that.
            cell = "  -- " if sn != sn else f"{sn:5.2f}"
            mv = r.get("_move", 0.0)
            # The movement figure, not the ratio, is what a reviewer can judge:
            # noise_floor reads a modelled series as noiseless, so three Civiqs
            # cells come back at infinity while moving a fifth of a point a
            # week. Flag on the number that means something.
            warn = "  barely moves" if mv < 0.5 else ""
            print(f"   {mark}{r['round_id']:<40} lock {r['lock_at'][:10]} "
                  f"h={batches.horizon_days(r['lock_at'], r['release_at']):.1f}d "
                  f"S/N={cell} wk={mv:5.2f}{warn}")

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
        stale = sorted(set(os.listdir(out_dir))
                       - {f"{b}.json" for b in by_batch})
        if stale:
            # Superseded candidate files are not removed, because one may be
            # half-reviewed and deleting a reviewer's working copy is worse
            # than leaving it. But they are named: a stale file looks exactly
            # like a fresh one, and a batch that no longer generates is
            # usually a batch that turned out to be unpublishable.
            print("\nnot written by this run, and possibly stale:")
            for f in stale:
                print(f"   questions/candidates/{f}")
        print("\nReview these, then move accepted rounds into "
              "questions/season0.json by hand. Nothing here publishes.")


if __name__ == "__main__":
    main()
