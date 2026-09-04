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
Wikipedia rankings roll the reviewed week contract forward. Silver Bulletin
dates are poll field midpoints, so those families are refused until a real
forward publication calendar is integrated.
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
    from ssa.adapters import civiqs as civiqs_adapter
    src = {}
    # Only a family with both a generation template and a proved forward
    # schedule needs history. Everything else is refused before a history or
    # network call is considered. This is the load-bearing offline rule: the
    # previous version called ``build_all`` over the entire registry, which
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
                 if meta.get("source") == "civiqs"}
    src["civiqs"] = {}
    for sid, meta in sorted(supported.items()):
        if meta.get("source") != "civiqs":
            continue
        cfg = meta["civiqs"]
        src["civiqs"][sid] = civiqs_adapter.as_displayed(
            cfg["name"], cfg.get("filters"), choice=cfg.get("choice"),
            net=cfg.get("net", False), weekday=cfg.get("weekday"), fetch=False)

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
    """(``{weekday, hour}``, reason) from explicit source date semantics.

    A regular pattern is not evidence that a date is a release date. Silver
    Bulletin deliberately labels its series with poll field midpoints; using
    their modal weekday previously generated Saturday Morning Consult releases
    while the reviewed calendar said Wednesday. Refuse that family until a
    publisher calendar exists rather than manufacturing a precise timestamp.
    """
    source = meta.get("source")
    if source in FIELD_DATE_SOURCES:
        return None, (
            "Silver Bulletin series dates are poll field midpoints, not "
            "publication dates; no forward publisher calendar is integrated")
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
            "resolve": RESOLVE[meta["source"]],
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
                  f"h={batches.horizon_days(r['lock_at']):.1f}d "
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
