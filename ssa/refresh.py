"""Build site/data.json from live sources.

Run:  python -m ssa.refresh
Cron: .github/workflows/refresh.yml runs this daily and commits the result.

Everything the entry page shows comes from this file: live tracker values
(Silver Bulletin poll CSVs, Michigan's own table with FRED as fallback,
VoteHub for the Congress and Supreme Court trackers), round status computed against the clock, and baseline
forecasts (persistence, trend) computed from the real series.
"""
import concurrent.futures
import threading
import json
import os
from datetime import date, datetime, timezone

from .adapters import aaii, silverbulletin, umich
from . import health
from . import provenance
from . import stamps
from . import average, backtest, baselines, envfile, harness, scoring, sharecard
from . import profile_round
from . import ranking_round
from . import series as series_registry

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUESTIONS = os.path.join(ROOT, "questions", "season0.json")
RESOLVED = os.path.join(ROOT, "resolutions", "resolved.json")
FORECASTS = os.path.join(ROOT, "forecasts")
ENTRANTS = os.path.join(ROOT, "entrants")
OUT = os.path.join(ROOT, "site", "data.json")
LOCKS = os.path.join(ROOT, "locks")

# How much history a lock snapshot keeps. The nulls need persistence (1 point),
# trend (8), climatology (24) and ewma (all, but at alpha 0.4 a point 60 back
# contributes ~1e-13). Sixty is bounded and lossless in practice.
LOCK_SNAPSHOT_POINTS = 60

UMICH_NEXT_RELEASE = "2026-08-14T14:00:00Z"  # preannounced; cron updates after each release


def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def poll_history(polls, key="value"):
    """One point per field date. When a pollster posts several variants for the
    same date (adults and registered voters), prefer the adults line."""
    by_date = {}
    for p in polls:
        d = p["date"].isoformat()
        if d not in by_date or p.get("population") == "a":
            by_date[d] = p[key]
    return [{"date": d, "value": by_date[d]} for d in sorted(by_date)]


def build_series(approval, generic, umich, sources=None):
    """All target series used by rounds, as [{date, value}] oldest first.

    The registered trackers come from ssa/series.py, which is the single place
    a series and its filters are declared. `generic_ballot_margin` is derived
    here instead: it is not a published tracker but this pipeline's own weekly
    adjusted average, which the midterm special resolves against.
    """
    out = dict(series_registry.build_all(sources))
    anchor = generic[-1]["date"] if generic else date.today()
    # weekly adjusted-average history for the midterm margin special
    margin_hist = []
    for weeks_back in range(12, -1, -1):
        asof = anchor.fromordinal(anchor.toordinal() - 7 * weeks_back)
        val, _ = average.adjusted_average(generic, asof)
        if val is not None:
            margin_hist.append({"date": asof.isoformat(), "value": round(val, 2)})
    out["generic_ballot_margin"] = margin_hist
    return out


def build_trackers(approval, generic, series, next_umich_release=None):
    if not approval or not generic:
        raise RuntimeError("upstream returned no polls; refusing to build trackers from empty data")
    # Anchor each average at its source's real freshness, not the wall clock.
    # Even a same-day source is behind the field dates it reports, so every
    # number is labelled with the date it is actually as of.
    asof_app = approval[-1]["date"]
    asof_gen = generic[-1]["date"]
    app_avg, app_n = average.adjusted_average(approval, asof_app)
    gen_avg, gen_n = average.adjusted_average(generic, asof_gen)
    app_prev, _ = average.adjusted_average(approval, asof_app.fromordinal(asof_app.toordinal() - 30))
    gen_prev, _ = average.adjusted_average(generic, asof_gen.fromordinal(asof_gen.toordinal() - 30))

    def latest(s):
        return s[-1] if s else None

    t = {}
    t["trump_approval_avg"] = {
        "label": "Trump approval, adjusted average",
        "unit": "% approve",
        "value": round(app_avg, 1),
        "asof": asof_app.isoformat(),
        "delta_30d": round(app_avg - app_prev, 1) if app_prev is not None else None,
        "n_polls_window": app_n,
        "source": "Silver Bulletin poll database, house-effect adjusted here, 21-day window",
    }
    t["generic_ballot_avg"] = {
        "label": "2026 generic ballot, adjusted average",
        "unit": "margin, Dem minus Rep",
        "value": round(gen_avg, 1),
        "asof": asof_gen.isoformat(),
        "delta_30d": round(gen_avg - gen_prev, 1) if gen_prev is not None else None,
        "n_polls_window": gen_n,
        "source": "Silver Bulletin poll database, house-effect adjusted here, 21-day window",
    }
    yg = latest(series["yougov_approval"])
    if yg:
        t["yougov_approval"] = {
            "label": "Economist/YouGov, latest wave",
            "unit": "% approve",
            "value": yg["value"],
            "asof": yg["date"],
            "source": "Silver Bulletin poll database (poll-level)",
        }
    mc = latest(series["mc_approval"])
    if mc:
        t["mc_approval"] = {
            "label": "Morning Consult, latest wave",
            "unit": "% approve",
            "value": mc["value"],
            "asof": mc["date"],
            "source": "Silver Bulletin poll database (poll-level)",
        }
    um = latest(series["umich_sentiment"])
    if um:
        t["umich_sentiment"] = {
            "label": "Michigan consumer sentiment",
            "unit": "index",
            "value": um["value"],
            "asof": um["date"],
            "next_release": next_umich_release or UMICH_NEXT_RELEASE,
            "source": series_registry.MICHIGAN_SOURCE,
        }
    return t


def next_release_for(season, tracker, now):
    """Next scheduled release for a tracker, from the season file itself."""
    upcoming = [r["release_at"] for r in season["rounds"]
                if r["tracker"] == tracker and parse_iso(r["release_at"]) > now]
    return min(upcoming) if upcoming else None


def round_status(r, resolved, now):
    if r["round_id"] in resolved:
        return "resolved"
    if now < parse_iso(r["lock_at"]):
        return "open"
    if now < parse_iso(r["release_at"]):
        return "locked"
    return "awaiting_resolution"


def lock_snapshot_path(round_id):
    return os.path.join(LOCKS, round_id + ".json")


def read_lock_snapshot(round_id):
    path = lock_snapshot_path(round_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


# The elicitation conditions -- persona sampling, the forecasting protocol, the
# fixed news digest -- are off unless SSA_ELICITATION names them. They are
# opt-in rather than on by default because the persona arm alone is one call per
# simulated respondent per round, roughly two hundred times a normal entrant,
# and a refresh that quietly starts spending that is exactly the surprise this
# repository has already paid for once. Turning them on is one variable, in the
# workflow or the shell, and tools/estimate_arms.py prints the bill first.
#
# The switch takes a list, not a flag, because the arms differ in cost by two
# orders of magnitude: measured over a full season the news arm is about $8 and
# the persona arm about $39, and `1` used to buy both plus the protocol arm at
# once. Anyone who wanted only the cheap one had no way to say so, which is a
# bad shape for a switch whose entire job is to stop an unintended bill.
#
#   SSA_ELICITATION=news                  just the fixed news corpus
#   SSA_ELICITATION=news,superfc          two of them
#   SSA_ELICITATION=1 / all               every arm, as before
#   SSA_ELICITATION=only:web,web+superfc  the named arms and NOTHING else --
#                                         the base roster (zeroshot and
#                                         recent10, direct) stays home too
#
# Unset means none, which stays the default: nothing about merging this starts
# spending anything.
def elicitation_only(value=None):
    """True when SSA_ELICITATION says the named arms replace the base roster
    instead of joining it."""
    raw = (os.environ.get("SSA_ELICITATION") if value is None else value) or ""
    return raw.strip().startswith("only:")


def elicitation_variants(value=None):
    """Which elicitation arms this run files, from SSA_ELICITATION.

    Raises on an unknown name rather than silently filing nothing: a typo in a
    workflow variable is otherwise invisible until someone notices a leaderboard
    row that never appeared.
    """
    raw = (os.environ.get("SSA_ELICITATION") if value is None else value) or ""
    raw = raw.strip()
    if raw.startswith("only:"):
        raw = raw[len("only:"):].strip()
    if not raw or raw in ("0", "off", "false"):
        return ()
    if raw in ("1", "all"):
        return tuple(harness.ELICITATION_VARIANTS)
    want = tuple(v.strip() for v in raw.split(",") if v.strip())
    for v in want:
        # harness.cell raises on an unknown name and accepts a combination
        # like `news+superfc`, which is the whole point of the two axes.
        harness.cell(v)
    return want


def season_roster():
    """(entrant_id, model, variant) for every condition this run will file."""
    roster = [] if elicitation_only() else list(harness.season_entrants())
    want = elicitation_variants()
    if want:
        roster += list(harness.elicitation_entrants(variants=want))
    return roster


def update_lock_snapshot(r, hist, now):
    """Record the history a round would freeze, while it is still open.

    Filtering by `date < lock_at` does not actually freeze anything for a
    monthly series, because the point's date is its month label, not its
    publication day: Michigan's August value is dated 2026-08-01 and published
    on the 14th, so a round locking on the 12th would absorb the very answer it
    is scored against the moment it appeared. The dates cannot distinguish
    "existed at lock" from "labelled before lock"; only observation time can.

    So while a round is open every refresh overwrites its snapshot, and after
    `lock_at` nothing touches it again. The last write before the lock is the
    freeze, and it is a committed artifact rather than something recomputed
    from data that has since changed underneath it.

    Empty history is never written over a snapshot that has some. A series
    missing from the map produces `hist == []`, which is a caller with an
    incomplete map -- not a tracker whose history disappeared -- and writing it
    destroys the one record of what the round's nulls saw. It has happened:
    a build that omitted `generic_ballot_margin` blanked that round's snapshot
    in one pass.
    """
    if now >= parse_iso(r["lock_at"]):
        return False                      # frozen; never rewritten
    if not hist and (read_lock_snapshot(r["round_id"]) or {}).get("history"):
        return False                      # never trade a real freeze for nothing
    os.makedirs(LOCKS, exist_ok=True)
    body = {
        "round_id": r["round_id"],
        "series": r["series"],
        "lock_at": r["lock_at"],
        "observed_at": iso(now),
        "history": hist[-LOCK_SNAPSHOT_POINTS:],
    }
    with open(lock_snapshot_path(r["round_id"]), "w") as f:
        json.dump(body, f, indent=2)
        f.write("\n")
    return True


def build_rounds(season, series, resolved, now, ranking_obs=None):
    """Returns (rounds, history_by_round). The history is the strictly pre-lock
    slice each round's baselines were computed from; the model harness
    conditions on exactly the same data, so entrants and nulls see one series.

    `ranking_obs` is {round_id: the source's own history of ordered lists} for
    ranking rounds, which read a different kind of record than a scalar series
    and cannot be looked up in `series`. Passed in rather than fetched here so
    that this function stays free of network calls: `main` gathers it once, with
    fetching on, and a test hands over a fixture.
    """
    out = []
    hist_by_round = {}
    for r in season["rounds"]:
        row = {k: r[k] for k in ("round_id", "tracker", "series", "question", "unit",
                                  "release_at", "release_estimated", "lock_at", "resolve")}
        # The submission questionnaire renders a type-specific answer control.
        # Keep the type and any type-specific answer metadata in the public
        # payload rather than forcing the browser to re-read season0.json or
        # infer a contract from the unit/question wording. Older definitions
        # predate target_type and are numeric distributions.
        row["target_type"] = r.get("target_type", "continuous_normal")
        for k in ("cells", "options"):
            if k in r:
                row[k] = list(r[k])
        row["status"] = round_status(r, resolved, now)
        if ranking_round.is_ranking(r):
            # None of the scalar branch below, and no lock snapshot. A ranking
            # round's target is a list, so `series` holds no entry for it and
            # the branch would write a snapshot whose `history` is `[]` on every
            # refresh -- a file claiming a freeze that records nothing. The
            # freeze that does apply is the date filter in
            # `ranking_round.frozen_history`, exact here for the reason
            # `attach_ranking` gives.
            attach_ranking(row, r, (ranking_obs or {}).get(r["round_id"]))
            hist_by_round[r["round_id"]] = []
            if r["round_id"] in resolved:
                row["resolution"] = resolved[r["round_id"]]
            out.append(row)
            continue
        # Baselines are frozen at lock time: only history strictly before the
        # lock date counts. Otherwise, once a release lands in the series, the
        # persistence null would contain the outcome it is scored against.
        lock_date = r["lock_at"][:10]
        live = [p for p in (series.get(r["series"]) or []) if p["date"] < lock_date]
        if now < parse_iso(r["lock_at"]):
            # Still open: use live history and keep the snapshot current.
            hist = live
            update_lock_snapshot(r, live, now)
        else:
            # Locked: the frozen snapshot is the truth. Falling back to the
            # date filter is only for rounds that locked before snapshots
            # existed, and it carries the flaw described in update_lock_snapshot.
            snap = read_lock_snapshot(r["round_id"])
            hist = snap["history"] if snap else live
            row["history_source"] = "lock snapshot" if snap else "date filter (pre-snapshot round)"
        hist_by_round[r["round_id"]] = hist
        if len(hist) >= 3:
            target = r["release_at"][:10]
            row["baselines"] = baselines.all_baselines(hist, target)
            row["scoreable"] = True
        else:
            row["baselines"] = None
            # Named rather than merely empty. Skill is defined as a ratio
            # against persistence, so a round with no series has no denominator
            # and can never produce the benchmark's headline number -- however
            # many forecasts it collects, and even if a human resolves it by
            # hand. Saying so in the payload keeps the pages from advertising a
            # question the arena cannot grade, and keeps the count of scoreable
            # rounds honest in the paper.
            row["scoreable"] = False
            row["baseline_note"] = (
                "no machine-readable series for this tracker: forecasts are "
                "collected and hashed, but cannot be scored, because skill is "
                "measured against a persistence baseline this round has none of")
        # A profile round is answered as a vector, so a scalar null cannot be
        # its denominator: `baselines` is cleared and the per-cell persistence
        # under `profile` replaces it. Clearing it is also what keeps the
        # scalar paths off this round -- `build_leaderboard` and the scalar
        # baseline filing both key on `baselines` being present.
        if profile_round.is_profile(r):
            attach_profile(row, r, series)
        if r["round_id"] in resolved:
            row["resolution"] = resolved[r["round_id"]]
        out.append(row)
    return out, hist_by_round


def attach_profile(row, r, series):
    """Attach the profile block: the round's cells and their frozen nulls.

    **Why the date filter is enough here, with no lock snapshot.** Snapshots
    exist because a monthly series' point is dated by its month label and
    published weeks later, so `date < lock_at` cannot tell "existed at lock"
    from "labelled before lock". The profile cells are the Civiqs daily
    dashboard, archived every day under the date it was read: label and
    observation are the same day, and the filter is exact. If a profile round
    is ever pointed at a monthly tracker, it needs per-cell snapshots first.
    """
    cells = profile_round.cells_for(r)
    hist = profile_round.frozen_history(r, series, cells)
    block = {
        "cells": list(cells),
        "labels": profile_round.labels_for(cells),
        "history_points": {c: len(hist[c]) for c in cells},
    }
    row["baselines"] = None
    try:
        block["baselines"] = {"persistence":
                              profile_round.persistence_profile(hist, cells)}
        row["scoreable"] = True
        row.pop("baseline_note", None)
    except ValueError as e:
        # Named rather than empty, for the reason the scalar branch gives: a
        # round with no denominator can never produce a skill number, however
        # many forecasts it collects.
        block["baselines"] = None
        row["scoreable"] = False
        row["baseline_note"] = str(e)
    row["profile"] = block


def attach_ranking(row, r, obs):
    """Attach the ranking block: the round's spec, its frozen history, its null.

    **Why the date filter is enough here, with no lock snapshot.** Snapshots
    exist because a monthly series' point is dated by its month label and
    published weeks later, so `date < lock_at` cannot tell "existed at lock"
    from "labelled before lock". Neither ranking source has that gap. A
    Wikipedia week is dated by the Sunday it ends and its seven daily counts are
    final within about two days, computed once from the request logs and never
    revised. A Trends week is dated by its Saturday and takes the value the
    earliest archived snapshot containing it showed, which is fixed the first
    time it is seen. In both cases the label and the observation are the same
    week, and the filter is exact.

    A round whose sources cannot answer yet is named rather than dropped: it
    keeps collecting forecasts and says in `baseline_note` why it has no skill
    denominator, which is the treatment the scalar and profile branches give the
    same situation.
    """
    block = {}
    row["baselines"] = None
    try:
        spec = ranking_round.spec_for(r)
        block.update({k: spec[k] for k in
                      ("kind", "length", "loss", "week_start", "week_end")})
        for k in ("items", "rbo_p", "exclusions", "geo"):
            if k in spec:
                block[k] = spec[k]
        hist = ranking_round.frozen_history(r, obs)
        block["history_weeks"] = len(hist)
        block["baselines"] = {
            "persistence": ranking_round.persistence_list(hist, spec)}
        row["scoreable"] = True
        row.pop("baseline_note", None)
    except (ValueError, RuntimeError) as e:
        block.setdefault("baselines", None)
        row["scoreable"] = False
        row["baseline_note"] = str(e)
    row["ranking"] = block


# Stop re-filing this long before lock_at. A refresh writes to the working
# tree, but the commit only lands minutes later; without the margin a run that
# starts just before lock could push a file that the merge-time lock audit
# then (correctly) rejects as late.
LOCK_MARGIN_SECONDS = 30 * 60

# One number, one forecast, bought at one fixed vantage point.
#
# Every entrant's forecast for a round is bought once, inside a window every
# round shares: between SSA_FILE_WINDOW_DAYS and SSA_BUY_BY_DAYS before its
# lock (3 to 2 days by default). A forecast stamped inside the window
# (`harness.filed_stamp`) is final -- data arriving afterwards does not reopen
# it -- so every entrant answers the same question from the same distance and
# a round costs exactly one call per entrant per condition, ever.
#
# The day-wide window spans ~4 six-hourly runs, and after it closes the runs
# that remain up to the lock margin are failure insurance only: they buy a
# forecast that is still missing and never rewrite one that exists. Drafts
# from before a round's window (the era that bought from listing day) carry
# no stamp and are replaced once, inside the window, where the input hash
# makes the replacement free if nothing actually changed.
#
# Baselines are exempt: they are free and the site shows them from listing.
# Web retrieval is scoped to the same window by construction, since the query
# turn cannot run before the window opens. FILE_WINDOW_SECONDS lives in
# harness because `_retrieve` and `filed_in_window` need it too.
FILE_WINDOW_SECONDS = harness.FILE_WINDOW_SECONDS
BUY_BY_SECONDS = float(os.environ.get("SSA_BUY_BY_DAYS") or "2") * 86400


def model_jobs_due(r, now):
    """True while the round's buy window (plus its insurance tail) is open."""
    left = (parse_iso(r["lock_at"]) - now).total_seconds()
    return LOCK_MARGIN_SECONDS <= left <= FILE_WINDOW_SECONDS


def job_still_due(r, path, now):
    """Whether this one entrant-forecast still needs buying.

    Three cases, in order: nothing on disk is bought whenever the round is
    due (that is the insurance tail working); a file stamped inside the
    window is final and never reopened; an unstamped file is a pre-window
    draft, replaced only while the window proper is open -- once the buy-by
    boundary passes, the draft is the insurance and it stands.
    """
    prev = read_forecast(path)
    if prev is None:
        return True
    if harness.filed_in_window(prev.get("notes"), r["lock_at"]):
        return False
    left = (parse_iso(r["lock_at"]) - now).total_seconds()
    return left >= BUY_BY_SECONDS

# Concurrent provider calls when filing forecasts. Each job is one call to one
# provider, and the eleven entered models spread across five providers, so this
# is a handful of concurrent requests per vendor rather than a burst at one.
FILING_WORKERS = int(os.environ.get("SSA_FILING_WORKERS", "20"))

# What one refresh may spend before it refuses to run.
#
# The cache makes a normal refresh nearly free: over the seven days to
# 2026-08-17 there were 28 scheduled runs, and each entrant's forecast changed
# three or four times -- the cost of a new observation landing, not of the
# clock ticking. A full legitimate sweep, every open round times every entrant
# all missing at once, is a few dollars.
#
# So a run that prices much above that is not doing more work, it is failing to
# reuse. That happens when the prompt bytes change or the endpoint moves, and
# both are one merge away: `call_identity` and the prompt are the cache key, so
# editing either correctly invalidates every stored hash -- and on a six-hourly
# cron the bill repeats every six hours until a human looks. Nothing in this
# pipeline would have said so; the site would keep rendering and the forecasts
# would keep being right.
#
# The ceiling is deliberately well above any real sweep. It is a runaway brake,
# not a budget.
# `or` rather than a dict default: a workflow that passes an unset variable
# delivers the empty string, which is set-but-false, and float("") is a crash.
MAX_SPEND = float(os.environ.get("SSA_MAX_SPEND") or "10")

# Measured, not guessed: 278 in / 1,276 out per call, from the 2,058 calls in
# backtest/runs/ that carry a usage report. model_backtest's own estimator
# assumes 400/500, which understates the output side by two and a half times --
# at maximum reasoning effort the thinking *is* the output.
# Recalibrated 2026-08-18 from the first fill run's committed receipts
# (replies/): 1,029 calls averaged 4,641 output tokens against the 1,276 this
# constant previously assumed -- the reasoning-heavy entrants (deepseek-pro
# 15k, qwen 8k, kimi 3.9k) tripled the fleet mean, so an "estimated $10"
# ceiling was actually authorising ~$36. Until the estimator reads per-model
# averages out of replies/, this stays pinned to the measured fleet mean.
EST_IN_TOKENS, EST_OUT_TOKENS = 300, 4650


def price_jobs(jobs, hist_by_round, read_forecast, news_for, prof_hist=None,
               rank_hist=None):
    """(jobs that would really call, estimated USD).

    Recomputes each job's input hash and compares it to what is already filed,
    which is exactly what `harness.forecast` will do a moment later -- so the
    number printed is the number about to be spent, not a guess about it.
    Anything this cannot price without a network round-trip is counted as
    billable, because the safe error is to over-report the bill.
    """
    from . import model_backtest
    billable, usd = [], 0.0
    for r, entrant, path in jobs:
        try:
            model, ctx, eli = harness.resolve(entrant)
        except KeyError:
            continue
        previous = read_forecast(path)
        notes = (previous or {}).get("notes") or ""
        try:
            if eli == "persona":
                raise ValueError("panel priced per respondent below")
            news = news_for(r) if ctx == "news" else None
            if profile_round.is_profile(r):
                # Same builder the filing pass uses, so a profile round that is
                # already answered prices as free rather than being counted
                # billable by the fallback below -- which would let a fully
                # cached headline round eat the whole spend ceiling.
                prompt = harness.build_profile_prompt(
                    r, (prof_hist or {}).get(r["round_id"]), ctx, eli, news=news)
            elif ranking_round.is_ranking(r):
                # Same builder the filing pass uses, for the same reason: an
                # already-answered ranking round must price as free rather than
                # falling through to the billable default below.
                prompt = harness.build_ranking_prompt(
                    r, (rank_hist or {}).get(r["round_id"]), ctx, eli, news=news)
            else:
                prompt = harness.build_prompt(
                    r, hist_by_round.get(r["round_id"]), ctx, eli, news=news)
            if f"in={harness.prompt_hash(entrant, prompt)}" in notes \
                    and not notes.startswith("MOCK"):
                continue                      # cached: free
        except Exception:                     # noqa: BLE001 - price it, do not skip it
            pass
        cin, cout = model_backtest.PRICING.get(model, (2.0, 10.0))
        calls = 1
        if eli == "persona":
            from . import personas
            calls = len(personas.panel())
        cost = calls * ((EST_IN_TOKENS / 1e6) * cin + (EST_OUT_TOKENS / 1e6) * cout)
        billable.append((r, entrant, path, cost))
        usd += cost
    return billable, usd


def affordable(billable, ceiling):
    """Split priced jobs into (buy, withhold) under a per-run ceiling.

    Jobs whose locks come soonest are bought first: a withheld forecast is
    only harmless while its round is still open, so the tail that waits for
    the next run must always be the tail with the most time left. Returns
    (jobs to run, jobs to withhold, dollars committed).

    This replaces an all-or-nothing gate that deadlocked: a backlog larger
    than one ceiling was withheld in full, six hours later the same backlog
    was estimated again and withheld again, and nothing ever drained.
    """
    buy, withhold, spent = [], [], 0.0
    for job in sorted(billable, key=lambda j: j[0]["lock_at"]):
        cost = job[3]
        if spent + cost > ceiling:
            withhold.append(job)
        else:
            spent += cost
            buy.append(job)
    return buy, withhold, spent


def nulls_for(r):
    """The round's reference forecasts, whatever shape the round takes.

    Scalar rounds keep theirs in `baselines`, profile rounds in
    `profile.baselines`, ranking rounds in `ranking.baselines` -- one accessor
    so the filing loop does not have to know, and so a round type added later
    cannot be silently skipped by a truthiness test on the wrong key.
    """
    if r.get("profile"):
        return (r["profile"] or {}).get("baselines") or {}
    if r.get("ranking"):
        return (r["ranking"] or {}).get("baselines") or {}
    return r.get("baselines") or {}


def profile_history_for(r, series):
    """{cell: frozen pre-lock history} for a profile round, else None."""
    if not profile_round.is_profile(r):
        return None
    return profile_round.frozen_history(r, series)


def ranking_observations(season, fetch=True):
    """{round_id: the source's history of ordered lists} for every ranking round.

    The one place a ranking round touches its sources, and the only place that
    fetches. Wikipedia's daily top lists are free, keyless and reachable from
    anywhere, so a refresh fills the archive as it goes; Google Trends is not
    reachable from a datacenter address at all, so its fetch fails, says so, and
    `basket_weeks` serves the committed archive.

    A round whose sources cannot answer at all is recorded as an empty history
    rather than raising. `attach_ranking` turns that into a named, unscoreable
    round, which is the same treatment a scalar round with no series gets --
    and the alternative is one unreachable source stopping the whole refresh,
    with every other round's forecasts unfiled and its lock still coming.
    """
    out = {}
    for r in (season or {}).get("rounds", []):
        if not ranking_round.is_ranking(r):
            continue
        try:
            out[r["round_id"]] = ranking_round.observations(r, fetch=fetch)
        except Exception as e:                     # noqa: BLE001 - reported
            print(f"  ranking {r['round_id']}: no observations "
                  f"({type(e).__name__}: {e})")
            out[r["round_id"]] = []
    return out


def read_forecast(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except ValueError:
        return None


def file_baseline_forecasts(rounds, hist_by_round, now, series=None,
                            ranking_obs=None):
    """Write every entrant's forecast for each open round.

    Returns (files_written, failures). Failures are messages, never mocks: a
    placeholder filed on error is a green workflow hiding a wrong model name.
    """
    """The hosted always-on agents: while a round is open, the refresh cron
    keeps each baseline's and each frontier model's forecast file current;
    the last commit before lock_at is the one that counts. Model forecasts
    are real API output when a key is configured and clearly-labeled
    deterministic MOCKs otherwise (see ssa/harness.py)."""
    written = 0
    failures = []
    jobs = []
    for r in rounds:
        if r["status"] != "open" or not nulls_for(r):
            continue
        if (parse_iso(r["lock_at"]) - now).total_seconds() < LOCK_MARGIN_SECONDS:
            continue
        rdir = os.path.join(FORECASTS, r["round_id"])
        os.makedirs(rdir, exist_ok=True)
        for name, fc in nulls_for(r).items():
            path = os.path.join(rdir, name + ".json")
            if r.get("profile"):
                # The null for a vector round is a vector: every cell where it
                # sat at the last release. Filed in the submission format so it
                # is scored by exactly the code an entrant's file goes through.
                answer = {"profile": {c: {"mean": v["mean"], "sd": v["sd"]}
                                      for c, v in fc.items()}}
                method = name
            elif r.get("ranking"):
                # And the null for a ranking round is a list: last completed
                # week's, in last week's order. Same reason for filing it in the
                # submission format -- it goes through the entrant code path, so
                # a null that could not be submitted is a null that is not being
                # scored the way entrants are.
                answer = {"ranking": list(fc["items"])}
                method = fc.get("method", name)
            else:
                answer = {"topline": {"mean": fc["mean"], "sd": fc["sd"]}}
                method = fc.get("method", name)
            # Key order is deliberate and matches what has been on disk all
            # season: these files are rewritten by every refresh, and reordering
            # them would rewrite four hundred committed forecasts to say the
            # same thing.
            body = {
                "round_id": r["round_id"],
                "entrant": name,
                **answer,
                "notes": "auto-filed baseline (" + method + "), refreshed until lock",
            }
            with open(path, "w") as f:
                json.dump(body, f, indent=2)
                f.write("\n")
            written += 1
        # Model forecasts wait for the round's own buy window, and each one is
        # bought exactly once (see the block above BUY_BY_SECONDS).
        if not model_jobs_due(r, now):
            continue
        # Every model runs both conditions and they are filed as separate
        # entrants: same weights, different information, so their scores answer
        # different questions and belong on different leaderboard rows.
        for entrant, _model, _ctx, _eli in season_roster():
            path = os.path.join(rdir, entrant + ".json")
            if not job_still_due(r, path, now):
                continue
            jobs.append((r, entrant, path))

    # One provider call per job, and at max reasoning effort a single call can
    # take a minute. Sequentially that is hours for a full season; the calls are
    # independent, so they run concurrently. Results are written by the worker
    # that produced them, and `failures` is appended under the GIL, which is
    # sufficient for list.append.
    # One digest per round, fetched once and handed to every news entrant, so
    # the condition is literally the same corpus rather than one fetch per
    # model that could drift between them. Built lazily: a season with no news
    # entrant never touches Wikipedia.
    news_cache, news_lock = {}, threading.Lock()

    # The frozen per-cell history each profile round's entrants and nulls both
    # read. Built once per round rather than per job: it is the same sixteen
    # slices for every entrant, and the pricing pass needs the identical object
    # to rebuild the identical prompt hash.
    prof_hist = {r["round_id"]: profile_history_for(r, series or {})
                 for r in rounds if profile_round.is_profile(r)}

    # The same object for ranking rounds: the strictly pre-lock weeks the null
    # was taken from, so an entrant sees exactly the history persistence saw.
    rank_hist = {r["round_id"]:
                 ranking_round.frozen_history(r, (ranking_obs or {}).get(r["round_id"]))
                 for r in rounds if ranking_round.is_ranking(r)}

    def news_for(r):
        rid = r["round_id"]
        with news_lock:
            if rid not in news_cache:
                from .adapters import newsdigest
                # for_round reads the committed archive when it is there, so a
                # CI run uses the corpus prepared and reviewed locally rather
                # than re-fetching and hoping the pages still read the same.
                news_cache[rid] = newsdigest.for_round(rid, r["lock_at"])
            return news_cache[rid]

    def run_job(job):
        r, entrant, path = job
        try:
            _, context, _elicitation = harness.resolve(entrant)
            news = news_for(r) if context == "news" else None
            if context == "news" and not (news or {}).get("text") \
                    and not (news or {}).get("window_closed"):
                # A round locking far out has a news window mostly in the
                # future; the digest grows a day at a time and this job
                # starts succeeding as the lock approaches. Not a failure:
                # nothing is wrong and nothing was spent -- an empty digest
                # on a CLOSED window still falls through and fails loudly.
                return 0
            body = harness.forecast(
                entrant, r,
                history=hist_by_round.get(r["round_id"]),
                previous=read_forecast(path),
                news=news,
                profile_history=prof_hist.get(r["round_id"]),
                ranking_history=rank_hist.get(r["round_id"]))
        except Exception as e:                     # noqa: BLE001 - collected
            # Collected rather than raised. Failing at the first bad provider
            # would strand every other entrant's forecast unwritten, and rounds
            # lock on a hard deadline. The successes land; main() reports every
            # failure and exits non-zero, so a run is loudly broken without
            # being silently incomplete.
            failures.append(f"{r['round_id']}/{entrant}: {e}")
            return 0
        with open(path, "w") as f:
            json.dump(body, f, indent=2)
            f.write("\n")
        return 1

    if jobs:
        billable, usd = price_jobs(jobs, hist_by_round, read_forecast,
                                   news_for, prof_hist, rank_hist)
        print(f"\nfiling: {len(jobs)} entrant-round(s), {len(jobs) - len(billable)} "
              f"already answered, {len(billable)} to call, est ${usd:.2f}")
        withheld = set()
        if usd > MAX_SPEND:
            # Buy the ceiling's worth, nearest locks first, and let the tail
            # wait for the next run -- the backlog drains one ceiling per
            # six-hourly run instead of deadlocking. Not SystemExit: the
            # series were already fetched and the site should still be
            # rebuilt. The failure channel exits non-zero at the end, so a
            # partially-filled run is loud without being destructive.
            buy, tail, spent = affordable(billable, MAX_SPEND)
            withheld = {(j[0]["round_id"], j[1]) for j in tail}
            for rid, entrant in sorted(withheld)[:12]:
                failures.append(
                    f"{rid}/{entrant}: withheld by the spend ceiling")
            failures.append(
                f"estimated ${usd:.2f} exceeds the ${MAX_SPEND:.2f} ceiling: "
                f"bought ${spent:.2f} ({len(buy)} entrant-rounds, nearest "
                f"locks first) and withheld {len(tail)}, which the next runs "
                "drain one ceiling at a time. Set SSA_MAX_SPEND for one run "
                "if the backlog must clear now.")
        run_list = [j for j in jobs
                    if (j[0]["round_id"], j[1]) not in withheld]
        with concurrent.futures.ThreadPoolExecutor(max_workers=FILING_WORKERS) as ex:
            written += sum(ex.map(run_job, run_list))
    return written, failures


def stamp_locked_rounds(rounds):
    """One manifest per locked round, stamped once and upgraded thereafter.

    The proof that a forecast predates the answer currently rests on a git
    history we control, which proves nothing to a skeptic. OpenTimestamps moves
    it onto a chain nobody here controls; see ssa/stamps.py for why the unit is
    a per-round manifest rather than each forecast.

    Never fatal. Four public calendars being briefly unreachable must not cost a
    run that has forecasts to file, and the next refresh retries -- but an
    unstamped round says so rather than passing silently.
    """
    out = []
    for r in rounds:
        if r.get("status") == "open":
            continue
        try:
            st = stamps.ensure(r["round_id"], r["lock_at"])
        except Exception as e:                     # noqa: BLE001 - reported
            print(f"  stamp {r['round_id']}: {type(e).__name__}: {e}")
            continue
        out.append(st)
        mark = "btc" if st.get("bitcoin_attested") else \
               ("calendar" if st.get("proof") else "UNSTAMPED")
        print(f"  stamp {r['round_id']:34s} {st['action']:9s} {mark}")
    if out and not stamps.have_client():
        print("  (no ots client on PATH; manifests written, proofs pending)")
    return out


def count_forecasts(rounds):
    """Attach filed forecasts to each round: count + per-entrant toplines
    (the page overlays them on the target charts)."""
    for r in rounds:
        rdir = os.path.join(FORECASTS, r["round_id"])
        fcs = {}
        if os.path.isdir(rdir):
            for fn in sorted(os.listdir(rdir)):
                if fn.endswith(".json"):
                    try:
                        with open(os.path.join(rdir, fn)) as f:
                            fc = json.load(f)
                        if isinstance(fc.get("topline"), dict) and "mean" in fc["topline"]:
                            fcs[fc["entrant"]] = {"mean": fc["topline"]["mean"],
                                                  "sd": fc["topline"].get("sd", 2.0)}
                    except (ValueError, KeyError):
                        continue
        r["n_forecasts"] = len(fcs)
        r["forecasts"] = fcs


def load_entrants():
    out = []
    if not os.path.isdir(ENTRANTS):
        return out
    for fn in sorted(os.listdir(ENTRANTS)):
        if fn.endswith(".json"):
            with open(os.path.join(ENTRANTS, fn)) as f:
                out.append(json.load(f))
    return out


def build_leaderboard(rounds, resolved):
    """Real scores only. Empty until rounds resolve."""
    entries = {}
    for r in rounds:
        res = resolved.get(r["round_id"])
        if not res or not r.get("baselines"):
            continue
        outcome = res["value"]
        per_crps = scoring.crps_normal(r["baselines"]["persistence"]["mean"],
                                       r["baselines"]["persistence"]["sd"], outcome)
        rdir = os.path.join(FORECASTS, r["round_id"])
        if not os.path.isdir(rdir):
            continue
        round_fcs = []
        for fn in sorted(os.listdir(rdir)):
            if not fn.endswith(".json"):
                continue
            with open(os.path.join(rdir, fn)) as f:
                fc = json.load(f)
            round_fcs.append(fc)
            c = scoring.crps_forecast(fc["topline"], outcome)
            e = entries.setdefault(fc["entrant"], {"crps": [], "skill": []})
            e["crps"].append(c)
            e["skill"].append(scoring.skill(c, per_crps))
        # crowd: equal-weight mixture of every submission in the round
        if len(round_fcs) >= 2:
            xs = scoring.pool_samples(round_fcs)
            c = scoring.crps_samples(xs, outcome)
            e = entries.setdefault("crowd", {"crps": [], "skill": []})
            e["crps"].append(c)
            e["skill"].append(scoring.skill(c, per_crps))
    board = []
    for name, e in entries.items():
        board.append({
            "entrant": name,
            "rounds": len(e["crps"]),
            "mean_crps": round(sum(e["crps"]) / len(e["crps"]), 3),
            "mean_skill": round(sum(e["skill"]) / len(e["skill"]), 3),
        })
    board.sort(key=lambda x: -x["mean_skill"])
    return board


def profile_outcome(r, resolved, series):
    """(outcome vector, detail) for a profile round past its release.

    A resolution written into `resolutions/resolved.json` wins, for the reason
    `ssa/resolve.py` gives: once written, a resolution is the scoring authority
    and is never recomputed underneath the scores it already fixed. Absent one,
    the vector is read from the cells' own archived series as of the release
    date -- no hand-typed numbers, and reproducible by anyone with the repo.

    Raises rather than returning a partial vector.
    """
    cells = profile_round.cells_for(r)
    res = (resolved or {}).get(r["round_id"])
    if res and res.get("values"):
        return profile_round.outcome_vector(res, cells), dict(res, source="resolved.json")
    detail = profile_round.resolution(r, series or {}, cells)
    return list(detail["vector"]), detail


def build_profile_leaderboard(rounds, resolved, series):
    """The profile board: energy score and skill, per entrant per profile round.

    Kept apart from `build_leaderboard` rather than folded into it, because the
    two are not the same measurement and averaging them would be meaningless: a
    CRPS is in points and an energy score is a distance in sixteen-dimensional
    points-space, and no weighting of the two answers a question anyone asked.
    Skill is the exception and the reason both boards are readable together --
    `scoring.profile_skill` is the scalar skill expression verbatim, so a tenth
    of skill means the same thing on either board.

    `matched` is the table to cite, for the reason `ssa/model_backtest.py`
    gives: it holds only entrants who answered *every* scored profile round, so
    a model that sat out the hard weeks cannot flatter itself with an average
    over the easy ones.
    """
    per_round, entries, skipped = [], {}, []
    scored_rounds = 0
    for r in rounds:
        if not profile_round.is_profile(r):
            continue
        nulls = (r.get("profile") or {}).get("baselines") or {}
        if not nulls.get("persistence"):
            skipped.append((r["round_id"], r.get("baseline_note")
                            or "no per-cell persistence null"))
            continue
        if now_utc() < parse_iso(r["release_at"]):
            continue                      # not due; not a problem
        cells = profile_round.cells_for(r)
        try:
            outcome, detail = profile_outcome(r, resolved, series)
        except ValueError as e:
            skipped.append((r["round_id"], str(e)))
            continue
        per_cells = profile_round.persistence_cells(nulls["persistence"], cells)
        per_energy = profile_round.score_cells(per_cells, outcome)["energy"]
        rdir = os.path.join(FORECASTS, r["round_id"])
        if not os.path.isdir(rdir):
            skipped.append((r["round_id"], "no forecasts filed"))
            continue
        rows = []
        for fn in sorted(os.listdir(rdir)):
            if not fn.endswith(".json"):
                continue
            with open(os.path.join(rdir, fn)) as f:
                fc = json.load(f)
            try:
                sc = profile_round.score_submission(fc, outcome, cells)
            except (ValueError, KeyError) as e:
                # A malformed or partial profile is excluded and named, never
                # repaired: see harness.parse_profile for why filling a cell in
                # would flatter exactly the entrant this round exists to catch.
                skipped.append((f"{r['round_id']}/{fn[:-5]}", str(e)))
                continue
            row = {
                "entrant": fc["entrant"],
                "energy": round(sc["energy"], 4),
                "skill": round(scoring.profile_skill(sc["energy"], per_energy), 4),
                "level": round(sc["level"], 4),
                "structure": round(sc["structure"], 4),
                "mean_cell_crps": round(sc["mean_cell_crps"], 4),
            }
            rows.append(row)
            e = entries.setdefault(fc["entrant"],
                                   {"energy": [], "skill": [], "level": [],
                                    "structure": [], "rounds": []})
            for k in ("energy", "skill", "level", "structure"):
                e[k].append(row[k])
            e["rounds"].append(r["round_id"])
        if not rows:
            skipped.append((r["round_id"], "no scoreable profile submissions"))
            continue
        scored_rounds += 1
        rows.sort(key=lambda x: -x["skill"])
        per_round.append({
            "round_id": r["round_id"],
            "release_at": r["release_at"],
            "cells": list(cells),
            "n_cells": len(cells),
            "persistence_energy": round(per_energy, 4),
            "outcome": {c: v for c, v in zip(cells, outcome)},
            "resolution": {k: detail.get(k) for k in
                           ("method", "release_date", "observed_dates", "source")
                           if detail.get(k) is not None},
            "entries": rows,
        })

    def board_from(names):
        out = []
        for name in names:
            e = entries[name]
            n = len(e["energy"])
            out.append({
                "entrant": name,
                "rounds": n,
                "mean_energy": round(sum(e["energy"]) / n, 4),
                "mean_skill": round(sum(e["skill"]) / n, 4),
                "mean_level": round(sum(e["level"]) / n, 4),
                "mean_structure": round(sum(e["structure"]) / n, 4),
            })
        out.sort(key=lambda x: -x["mean_skill"])
        return out

    matched_names = [n for n, e in entries.items()
                     if len(e["rounds"]) == scored_rounds] if scored_rounds else []
    return {
        "scored_rounds": scored_rounds,
        "rounds": per_round,
        "board": board_from(list(entries)),
        "matched": board_from(matched_names),
        "matched_rounds": scored_rounds,
        "skipped": [list(s) for s in skipped],
        "note": ("energy score (multivariate CRPS) over the round's cell "
                 "vector; skill = 1 - ES(entrant) / ES(per-cell persistence). "
                 "`matched` holds only entrants who answered every scored "
                 "profile round."),
    }


def ranking_outcome(r, resolved, spec):
    """(truth list, detail) for a ranking round past its release.

    A resolution written into `resolutions/resolved.json` wins, for the reason
    `ssa/resolve.py` gives: once written, a resolution is the scoring authority
    and is never recomputed underneath the scores it already fixed. Absent one,
    the list is recomputed from the committed archives by the round's own rule
    -- no hand-typed answers, and reproducible by anyone with the repository and
    no network at all.
    """
    res = (resolved or {}).get(r["round_id"])
    if res and res.get("items"):
        return (ranking_round.outcome_items(res, spec),
                dict(res, source="resolved.json"))
    detail = ranking_round.resolution(r, spec=spec)
    return list(detail["items"]), detail


def build_ranking_leaderboard(rounds, resolved, ranking_obs=None):
    """The ranking board: list loss and skill, per entrant per ranking round.

    A third section rather than rows on either board above, for the reason the
    profile board is its own: the numbers are not the same measurement. A CRPS
    is in the series' unit, an energy score is a distance in cell-space, and a
    ranking loss is a dimensionless [0, 1] disagreement between two orders.
    Averaging them would produce a number no question has. `skill` is again the
    exception and the reason the three read together -- it is
    `scoring.skill` in all three places, so a tenth of skill is a tenth of the
    null's loss removed wherever it appears.

    `matched` is the table to cite, for the reason `ssa/model_backtest.py`
    gives: it holds only entrants who answered *every* scored ranking round.
    """
    per_round, entries, skipped = [], {}, []
    scored_rounds = 0
    for r in rounds:
        if not ranking_round.is_ranking(r):
            continue
        nulls = (r.get("ranking") or {}).get("baselines") or {}
        if not nulls.get("persistence"):
            skipped.append((r["round_id"], r.get("baseline_note")
                            or "no persistence null"))
            continue
        if now_utc() < parse_iso(r["release_at"]):
            continue                      # not due; not a problem
        try:
            spec = ranking_round.spec_for(r)
            outcome, detail = ranking_outcome(r, resolved, spec)
        except (ValueError, RuntimeError) as e:
            skipped.append((r["round_id"], str(e)))
            continue
        try:
            per = ranking_round.score_list(
                ranking_round.normalize(nulls["persistence"]["items"], spec,
                                        where="persistence"),
                outcome, spec)
        except (ValueError, RuntimeError) as e:
            # The null itself failing is not a round to skip quietly: without a
            # denominator there is no skill number, and reporting losses with no
            # skill beside them invites them to be read as one.
            skipped.append((r["round_id"], f"persistence null: {e}"))
            continue
        rdir = os.path.join(FORECASTS, r["round_id"])
        if not os.path.isdir(rdir):
            skipped.append((r["round_id"], "no forecasts filed"))
            continue
        rows = []
        for fn in sorted(os.listdir(rdir)):
            if not fn.endswith(".json"):
                continue
            with open(os.path.join(rdir, fn)) as f:
                fc = json.load(f)
            try:
                sc = ranking_round.score_submission(fc, outcome, spec)
            except (ValueError, KeyError) as e:
                # Excluded and named, never repaired: see harness.parse_ranking
                # for why padding a short list would score an entrant on a pick
                # it never made.
                skipped.append((f"{r['round_id']}/{fn[:-5]}", str(e)))
                continue
            row = {
                "entrant": fc["entrant"],
                "loss": round(sc["loss"], 4),
                "skill": round(ranking_round.ranking_skill(sc["loss"],
                                                           per["loss"]), 4),
                "exact_positions": sc["exact_positions"],
            }
            for k in ("overlap", "discordant_pairs", "pairs"):
                if k in sc:
                    row[k] = sc[k]
            rows.append(row)
            e = entries.setdefault(fc["entrant"],
                                   {"loss": [], "skill": [], "rounds": []})
            e["loss"].append(row["loss"])
            e["skill"].append(row["skill"])
            e["rounds"].append(r["round_id"])
        if not rows:
            skipped.append((r["round_id"], "no scoreable ranking submissions"))
            continue
        scored_rounds += 1
        rows.sort(key=lambda x: -x["skill"])
        per_round.append({
            "round_id": r["round_id"],
            "release_at": r["release_at"],
            "kind": spec["kind"],
            "loss_rule": spec["loss"],
            "length": spec["length"],
            "persistence_loss": round(per["loss"], 4),
            "persistence_items": list(nulls["persistence"]["items"]),
            "outcome": list(outcome),
            "resolution": {k: detail.get(k) for k in
                           ("method", "week_start", "week_end", "source")
                           if detail.get(k) is not None},
            "entries": rows,
        })

    def board_from(names):
        out = []
        for name in names:
            e = entries[name]
            n = len(e["loss"])
            out.append({
                "entrant": name,
                "rounds": n,
                "mean_loss": round(sum(e["loss"]) / n, 4),
                "mean_skill": round(sum(e["skill"]) / n, 4),
            })
        out.sort(key=lambda x: -x["mean_skill"])
        return out

    matched_names = [n for n, e in entries.items()
                     if len(e["rounds"]) == scored_rounds] if scored_rounds else []
    return {
        "scored_rounds": scored_rounds,
        "rounds": per_round,
        "board": board_from(list(entries)),
        "matched": board_from(matched_names),
        "matched_rounds": scored_rounds,
        "skipped": [list(s) for s in skipped],
        "note": ("a point-scored ordered list: rank-biased overlap (p fixed in "
                 "the round) for an open-set top-N, normalized Kendall tau "
                 "distance for a fixed basket. skill = 1 - loss(entrant) / "
                 "loss(last week's list). `matched` holds only entrants who "
                 "answered every scored ranking round. When persistence is "
                 "perfect the denominator is zero and the convention "
                 "(`scoring.skill`) scores every entrant 0 for that round; "
                 "`persistence_loss` is published so that is visible."),
    }


def michigan_history():
    """Michigan sentiment. Delegates to the registry, which has no fallback.

    There used to be a fallback here, to FRED, so the arena would not go dark
    if the university's plain CSV moved. It went dark in a worse way instead:
    FRED carries the series a month behind at Michigan's request, so on the one
    run where the official table was briefly unreachable the fallback answered
    with a history ending a month early and nothing downstream could tell. That
    run wrote the lock snapshot for `umich-2026-08-prelim`, whose baselines were
    then anchored a month stale and whose resolution silently became July's
    final rather than August's preliminary. See ssa/series.michigan_history.
    """
    return series_registry.michigan_history()


def load_model_backtest():
    """Real LLM backtest results, when a run has been committed.

    Written by tools/run_model_backtest.py. Absent until someone with API keys
    runs it, which is why the placeholder path below still exists.
    """
    path = os.path.join(ROOT, "backtest", "model_backtest.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        mb = json.load(f)
    return {
        "window": mb.get("window"),
        "start": mb.get("start"),
        "releases": mb.get("matched_releases"),
        "entrants": mb.get("entrants"),
        "board": mb.get("matched"),
        "trajectory": mb.get("trajectory"),
        "per_series": mb.get("per_series"),
        "per_series_trajectory": mb.get("per_series_trajectory"),
        "failures": mb.get("failures"),
        "cutoffs": mb.get("cutoffs"),
    }



def main():
    # Local runs read keys from .env; in CI they arrive as Actions secrets
    # and no .env exists, so already-set variables always win.
    envfile.load()
    now = now_utc()
    # One fetch per upstream file, shared by the series registry and by the
    # averages below, so the site's headline numbers and its series can never
    # be built from different snapshots of the same source.
    # Each upstream body is archived as it arrived, before anything parses it.
    # Twenty-one of the registered series come out of one published Google
    # Sheet that is revised in place, so without a dated vintage a resolution
    # computed from it today cannot be rechecked tomorrow. See ssa/provenance.py.
    sb_app_raw = silverbulletin.fetch_text(silverbulletin.APPROVAL_URL)
    sb_gen_raw = silverbulletin.fetch_text(silverbulletin.GENERIC_URL)
    prov = {
        "sb_approval": provenance.record(
            "sb_approval", silverbulletin.APPROVAL_URL, sb_app_raw,
            note="Silver Bulletin poll database, published as a Google Sheet"),
        "sb_generic": provenance.record(
            "sb_generic", silverbulletin.GENERIC_URL, sb_gen_raw,
            note="Silver Bulletin generic-ballot database, published as a Google Sheet"),
    }
    sources = {
        "sb_approval": silverbulletin.parse(sb_app_raw),
        "sb_generic": silverbulletin.parse(sb_gen_raw),
        "umich": series_registry.michigan_history(),
    }
    prov["umich"] = provenance.record(
        "umich", series_registry.MICHIGAN_URL,
        series_registry.MICHIGAN_RAW or "",
        note=series_registry.MICHIGAN_SOURCE)
    # AAII serves a ~22-week rolling window with no deeper machine-readable
    # history, so the committed vintages *are* the long history: each week the
    # window slides and the archive keeps the week that fell off. The body is
    # parsed with the asof from the response that carried it (the page's dates
    # have no year), and both go into `sources` so the registry never fetches
    # a second, different snapshot of the same page.
    aaii_raw, aaii_asof = aaii.fetch_text()
    prov["aaii"] = provenance.record(
        "aaii", aaii.URL, aaii_raw, ext="html",
        note=("AAII sentiment survey results page, a ~22-week rolling window "
              f"parsed against the response's own date {aaii_asof}; the full "
              "1987-present .xls is OLE2 and unreadable without a dependency"))
    sources["aaii"] = aaii.parse(aaii_raw, aaii_asof)
    for name, block in sorted(prov.items()):
        print(f"  {name:12s} {block['bytes']:>9,}B  sha {block['sha256'][:12]}")
    # Every series now comes from a source that is days behind rather than
    # weeks. VoteHub is gone: it was 41 days stale at the source and the only
    # two trackers it still supplied, Congress and the Supreme Court, backed no
    # round -- 16 backtest points is not worth a second, staler provenance.
    approval = silverbulletin.approval_polls(rows=sources["sb_approval"])
    generic = silverbulletin.generic_ballot_polls(rows=sources["sb_generic"])
    umich = sources["umich"]

    with open(QUESTIONS) as f:
        season = json.load(f)

    series = build_series(approval, generic, umich, sources)
    trackers = build_trackers(approval, generic, series,
                              next_release_for(season, "umich_sentiment", now))
    resolved = {}
    if os.path.exists(RESOLVED):
        with open(RESOLVED) as f:
            resolved = json.load(f)

    # Ranking rounds read ordered lists rather than a scalar series, from their
    # own archives. Gathered once, before the rounds are built, so that no
    # network call happens inside `build_rounds` and so every consumer below --
    # the nulls, the prompts, the pricing pass and the board -- reads the
    # identical object.
    ranking_obs = ranking_observations(season, fetch=True)

    rounds, hist_by_round = build_rounds(season, series, resolved, now,
                                         ranking_obs)
    # The workflow runs this module twice: once to fetch and file, then again
    # after `ssa.resolve` so the leaderboard reflects anything just resolved
    # instead of waiting six hours. Only the *second* purpose needs the second
    # pass, and it was silently paying for the first one too.
    #
    # A forecast that failed writes no file, so the second pass finds nothing
    # cached and calls the provider again. That is free when the failure was a
    # dead key -- and it is not free at all when the failure was a timeout or a
    # dropped stream, because the model generated the answer and the provider
    # billed it. On 2026-08-12 glm timed out at the full 600-second read budget
    # in both passes of one run: twenty minutes of generation, paid for twice,
    # recorded zero times. That run took 21 minutes, and every long run in the
    # history is this shape.
    #
    # So the second pass rebuilds the site and files nothing.
    if os.environ.get("SSA_SKIP_FILING") == "1":
        print("\nSSA_SKIP_FILING=1: rebuilding from what is on disk, "
              "calling no provider")
        filed, filing_failures = 0, []
    else:
        filed, filing_failures = file_baseline_forecasts(rounds, hist_by_round,
                                                         now, series,
                                                         ranking_obs)
    count_forecasts(rounds)
    stamped = stamp_locked_rounds(rounds)

    # Whether each source is still answering, and whether the arena still knows
    # the answer. A flake must not cost a run; an outage must be loud at once,
    # because everything downstream keeps working perfectly while publishing
    # numbers that stopped moving. See ssa/health.py for the two clocks.
    source_health = health.check(now)
    print("\nsources:")
    for line in health.report(source_health):
        print(line)
    board = build_leaderboard(rounds, resolved)
    profile_board = build_profile_leaderboard(rounds, resolved, series)
    ranking_board = build_ranking_leaderboard(rounds, resolved, ranking_obs)
    bt = backtest.run({
        "umich_sentiment": series["umich_sentiment"],
        "yougov_approval": series["yougov_approval"],
        "mc_approval": series["mc_approval"],
        "yougov_generic_margin": series["yougov_generic_margin"],
    })
    real_mb = load_model_backtest()
    if real_mb:
        # A real run exists, so the placeholders below are skipped entirely.
        #
        # The measured board goes into `overall` and `spans` as well as under
        # `models`, because those are the keys the site renders. Putting real
        # numbers only under a new key is how the pages ended up showing no
        # model rows at all: the placeholder path used to populate `overall`,
        # so removing it silently emptied every model table.
        #
        # The board is the matched table -- models and baselines scored on the
        # identical set of releases -- so the rows in it are comparable to each
        # other. That is not true of the long baseline replay in `spans`, which
        # covers all 339 releases including stretches no model was scored on,
        # so the two are not mixed: the measured board replaces them rather
        # than being appended to them.
        bt["models"] = real_mb
        bt["mock_models"] = []
        # `mb_board`, NOT `board`. `board` is the *live* leaderboard, built at
        # line 751 from resolved rounds, and it is published as
        # leaderboard.entries. Assigning to that name here overwrites it with
        # the backtest table, so the site presents backtest CRPS over 22
        # historical releases as though it were the live season's standings --
        # next to a resolved_rounds count that disagrees with it.
        #
        # CLAUDE.md records this exact bug being found and fixed once already.
        # It came back the moment this block was edited again, because the
        # names still collide. Renaming is the fix that does not depend on
        # anyone remembering.
        mb_board = real_mb.get("board") or []
        if mb_board:
            bt["baseline_replay"] = {"overall": bt["overall"],
                                     "spans": bt["spans"],
                                     "n_rounds": bt.get("n_rounds")}
            bt["overall"] = mb_board
            bt["spans"] = {k: mb_board for k in bt["spans"]}
            bt["n_rounds"] = real_mb.get("releases") or bt.get("n_rounds")
            # The charts draw whichever entrants this list names, and read
            # their values out of trajectory[].skills. Both were populated by
            # the placeholder path; leaving them empty is why every model curve
            # vanished while the numbers themselves were correct.
            bt["model_entrants"] = [e for e in (real_mb.get("entrants") or [])
                                    if e not in baselines.DEFAULT]
            if real_mb.get("trajectory"):
                bt["baseline_replay"]["trajectory"] = bt["trajectory"]
                bt["trajectory"] = real_mb["trajectory"]
            if real_mb.get("per_series"):
                bt["baseline_replay"]["per_series"] = bt["per_series"]
                bt["per_series"] = real_mb["per_series"]
            # Per-tracker curves. Without these the tracker tabs can only draw
            # the tracker's own line plus a dot per open round, so a model that
            # is good at Michigan and bad at the generic ballot looks identical
            # on both -- the per-series difference exists in the data and had
            # nowhere to be shown.
            if real_mb.get("per_series_trajectory"):
                bt["per_series_trajectory"] = real_mb["per_series_trajectory"]
            bt["note"] = (
                f"{real_mb.get('releases')} releases every entrant answered, "
                f"{real_mb.get('window', {}).get('first')} to "
                f"{real_mb.get('window', {}).get('last')}. Each model is scored "
                "only on releases after its own training cutoff; this table is "
                "the intersection, so every row is measured on the same points. "
                "Rows ending -zeroshot saw the question and no series history.")
    # No `else`. Deleting the placeholder path was right -- it invented model
    # rows -- but it left a bare `else:` behind, and a bare `else:` is not a
    # no-op in Python, it is an IndentationError. That made the whole module
    # unimportable, so `ssa.refresh` and `ssa.resolve` both died at startup and
    # the pipeline stopped, three days before the first release resolves.
    #
    # When no measured backtest exists the baseline replay already in `bt` is
    # the honest answer, and publishing it unaccompanied is the intended
    # behaviour rather than something to fill in.

    data = {
        "generated_at": iso(now),
        "season": season["season"],
        "trackers": trackers,
        "rounds": rounds,
        "entrants": load_entrants(),
        "leaderboard": {
            "resolved_rounds": sum(1 for r in rounds if r["status"] == "resolved"),
            "entries": board,
        },
        # The joint sixteen-cell rounds, scored with the energy score. A
        # separate section rather than rows on the board above: the two use
        # different scoring rules on different objects, and only `skill` is
        # comparable across them.
        "profile": profile_board,
        # The ordered-list rounds, scored with a metric on lists. Separate for
        # the same reason `profile` is separate: only `skill` is comparable
        # across the three sections.
        "ranking": ranking_board,
        "backtest": bt,
        "charts": {
            "approval_avg": average.weekly_series(approval, 80),
            "generic_margin": average.weekly_series(generic, 80),
            "umich_sentiment": series["umich_sentiment"][-48:],
            "yougov_approval": series["yougov_approval"],
            "mc_approval": series["mc_approval"],
        },
        "series_tail": {k: v[-8:] for k, v in series.items()},
        # Which URL, fetched when, and where the saved raw body is -- per
        # upstream file, and per series through its `source` key. A page can
        # then say "this figure came from that file at that time" instead of
        # crediting a brand.
        "sources": dict(prov, repo="https://github.com/Social-Atoms/social-sim-arena"),
        # One entry per locked round: the manifest that fixes every forecast
        # hash at the lock, and whether its proof has reached a Bitcoin block
        # yet. A reader runs `ots verify` on the file and needs to trust
        # nobody here.
        "stamps": {st["round_id"]: st for st in stamped},
        # Per source: how long since a successful fetch, how long since the
        # content moved, and the budget each is judged against. A page that
        # renders a number should be able to say how old it is.
        "source_health": source_health,
        "series_provenance": {
            sid: prov.get(spec["source"], {}).get("source", spec["source"])
            for sid, spec in series_registry.SERIES.items()
        },
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(data, f, indent=1)
    try:
        sharecard.build(data)
    except Exception as e:
        print("sharecard skipped:", e)
    print("wrote", OUT)
    keyed = sorted(m for m in harness.MODELS if harness.has_key(m))
    print("forecast files filed:", filed,
          "| models with a key:", keyed or "none")
    print("approval polls:", len(approval), "| generic:", len(generic),
          "| umich points:", len(umich))
    print("approval avg:", trackers["trump_approval_avg"]["value"],
          "| generic margin:", trackers["generic_ballot_avg"]["value"])

    # A fallback that nobody sees is the failure this design exists to avoid:
    # the site renders, the leaderboard updates, and four entrants have quietly
    # moved to a different endpoint at a lower reasoning depth. So a run that
    # used the standby says so, in the same place it says everything else.
    down = harness.dead_routes()
    if down:
        print(f"\n{len(down)} route(s) failed terminally and fell back to the "
              "OpenRouter standby:")
        for env, host in down:
            print(f"  - {env} @ {host}")
        print("  Forecasts filed this way carry via=openrouter in their notes "
              "and the standby's own input hash, so the run after the account "
              "is fixed re-asks the vendor and upgrades them automatically.")

    if filing_failures:
        # site/data.json and every successful forecast are already on disk, so
        # the workflow's commit step (which runs with if: always()) still lands
        # them and a round does not miss its lock over one bad provider. The
        # non-zero exit is what makes the failure impossible to ignore.
        print(f"\n{len(filing_failures)} forecast(s) failed and were NOT filed:")
        for f in filing_failures:
            print("  -", f)
        raise SystemExit(
            f"{len(filing_failures)} entrant forecast(s) failed. Nothing was "
            "mocked; fix the cause and re-run. Set SSA_ALLOW_MOCK=1 only for "
            "local work without keys.")


if __name__ == "__main__":
    main()
