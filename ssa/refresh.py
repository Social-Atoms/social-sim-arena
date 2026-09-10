"""Build site/data.json from live sources.

Run:  python -m ssa.refresh
Cron: .github/workflows/refresh.yml runs this every six hours and commits the
result.

Everything the entry page shows comes from this file: live tracker values
(Silver Bulletin poll CSVs, Michigan's own table with FRED as fallback,
VoteHub for the Congress and Supreme Court trackers), round status computed against the clock, and baseline
forecasts (persistence, trend) computed from the real series.
"""
import concurrent.futures
import threading
import json
import os
import re
from datetime import date, datetime, timedelta, timezone

from .adapters import aaii, silverbulletin, umich
from . import health
from . import provenance
from . import reliability
from . import stamps
from . import average, backtest, baselines, batches, domains, envfile, harness, scoring, sharecard
from . import participants
from . import profile_round
from . import ranking_round
from . import series as series_registry
from . import task_registry
from . import entrant_status

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUESTIONS = os.path.join(ROOT, "questions", "season0.json")
RESOLVED = os.path.join(ROOT, "resolutions", "resolved.json")
FORECASTS = os.path.join(ROOT, "forecasts")
ENTRANTS = os.path.join(ROOT, "entrants")
OUT = os.path.join(ROOT, "site", "data.json")
OPERATOR = os.path.join(ROOT, "site", "operator.json")
LOCKS = os.path.join(ROOT, "locks")

# How much history a lock snapshot keeps. The nulls need persistence (1 point),
# trend (8), climatology (24) and ewma (all, but at alpha 0.4 a point 60 back
# contributes ~1e-13). Sixty is bounded and lossless in practice.
LOCK_SNAPSHOT_POINTS = 60

UMICH_NEXT_RELEASE = "2026-08-14T14:00:00Z"  # preannounced; cron updates after each release
UMICH_COMPOSITE_FORMAT = "ssa.umich.raw.v1"
SB_MAX_OBSERVATION_AGE_DAYS = 21


def encode_umich_composite(finals_raw, preliminary_raw):
    """Canonical bytes containing both exact UMich response bodies.

    Michigan's published series is the merge of two distinct official files.
    A provenance vintage containing only ``tbmics.csv`` cannot safely recreate
    a preliminary release.  Store both verbatim bodies, with their semantic
    URLs, in one hash-covered envelope so one manifest row is still the atomic
    source vintage.
    """
    if not isinstance(finals_raw, str) or not finals_raw.strip():
        raise RuntimeError("umich: finals raw body is missing")
    if not isinstance(preliminary_raw, str) or not preliminary_raw.strip():
        raise RuntimeError("umich: preliminary raw body is missing")
    document = {
        "format": UMICH_COMPOSITE_FORMAT,
        "parts": {
            "finals": {"url": umich.URL, "body": finals_raw},
            "preliminary": {
                "url": umich.PRELIM_URL, "body": preliminary_raw,
            },
        },
    }
    return (json.dumps(document, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def parse_umich_composite(raw):
    """Re-parse a hash-validated UMich composite through both live parsers."""
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        document = json.loads(raw)
    except (UnicodeDecodeError, TypeError, ValueError) as error:
        raise RuntimeError(
            "umich: archived source is not a valid raw-body composite") from error
    if not isinstance(document, dict) or document.get("format") != \
            UMICH_COMPOSITE_FORMAT:
        raise RuntimeError("umich: archived source has the wrong composite format")
    parts = document.get("parts")
    if not isinstance(parts, dict) or set(parts) != {"finals", "preliminary"}:
        raise RuntimeError(
            "umich: archived composite must contain finals and preliminary bodies")

    def body(name, expected_url):
        part = parts.get(name)
        if not isinstance(part, dict) or set(part) != {"url", "body"}:
            raise RuntimeError(f"umich: malformed {name} composite part")
        if part.get("url") != expected_url:
            raise RuntimeError(
                f"umich: archived {name} URL {part.get('url')!r} does not "
                f"match {expected_url!r}")
        value = part.get("body")
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError(f"umich: archived {name} raw body is missing")
        return value

    finals = umich.parse(body("finals", umich.URL))
    preliminary = umich.parse_prelim(
        body("preliminary", umich.PRELIM_URL))
    if not finals:
        raise RuntimeError("umich: archived finals body parsed to zero rows")
    if not preliminary:
        raise RuntimeError("umich: archived preliminary body parsed to zero rows")
    rows = umich.merge(finals, preliminary)
    if not rows:
        raise RuntimeError("umich: archived composite parsed to zero rows")
    return rows


def validate_silverbulletin_rows(name, rows, *, today=None):
    """Validate every registered derivation and the sheet's observation age.

    Google can return an old but structurally valid published sheet with HTTP
    200, and wrapper/timestamp bytes may change even while its poll rows stay
    frozen.  Recording those bytes first resets the provenance change clock and
    hides the outage.  The newest meaningful field end must therefore pass a
    generous cadence guard before ``provenance.record`` is allowed to run.
    """
    if name not in {"sb_approval", "sb_generic"}:
        raise ValueError(f"unknown Silver Bulletin source {name!r}")
    record_builder = (silverbulletin.approval_polls
                      if name == "sb_approval" else
                      silverbulletin.generic_ballot_polls)
    newest = None
    for series_id, spec in series_registry.SERIES.items():
        if spec["source"] != name:
            continue
        filters = spec.get("filters") or {}
        records = record_builder(rows=rows, **filters)
        built = silverbulletin.to_series(records, spec["value"])
        if not built:
            raise RuntimeError(
                f"{series_id} built to zero points; refusing an incomplete "
                "source extraction")
        newest_for_series = max(record["end_date"] for record in records)
        newest = max(newest, newest_for_series) if newest else newest_for_series
    if newest is None:
        raise RuntimeError(
            f"{name} has no registered meaningful observations; refusing an "
            "incomplete source extraction")
    clock = today or datetime.now(timezone.utc).date()
    if isinstance(clock, datetime):
        clock = clock.date()
    age = (clock - newest).days
    if age > SB_MAX_OBSERVATION_AGE_DAYS:
        raise RuntimeError(
            f"{name} HTTP 200 but newest observation is {newest.isoformat()}, "
            f"{age} days old: frozen page exceeds the "
            f"{SB_MAX_OBSERVATION_AGE_DAYS}-day freshness limit; refusing to "
            "archive changing wrapper bytes as a new release")
    return rows


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


def build_series(approval, generic, umich, sources=None, *,
                 unavailable_sources=(), isolate_failures=False,
                 source_diagnostics=None):
    """All target series used by rounds, as [{date, value}] oldest first.

    The registered trackers come from ssa/series.py, which is the single place
    a series and its filters are declared. `generic_ballot_margin` is derived
    here instead: it is not a published tracker but this pipeline's own weekly
    adjusted average, which the midterm special resolves against.
    """
    built = series_registry.build_all(
        sources, unavailable_sources=unavailable_sources,
        isolate_failures=isolate_failures,
        diagnostics=source_diagnostics)
    if isolate_failures:
        out, source_failures = built
    else:
        out, source_failures = built, None
    out = dict(out)
    generic_available = bool(generic) and not (
        isolate_failures and "sb_generic" in source_failures)
    anchor = generic[-1]["date"] if generic_available else date.today()
    # weekly adjusted-average history for the midterm margin special
    margin_hist = []
    for weeks_back in range(12, -1, -1):
        asof = anchor.fromordinal(anchor.toordinal() - 7 * weeks_back)
        val, _ = average.adjusted_average(generic if generic_available else [], asof)
        if val is not None:
            margin_hist.append({"date": asof.isoformat(), "value": round(val, 2)})
    if margin_hist:
        out["generic_ballot_margin"] = margin_hist
    return (out, source_failures) if isolate_failures else out


def build_trackers(approval, generic, series, next_umich_release=None,
                   umich_source=None):
    # Anchor each average at its source's real freshness, not the wall clock.
    # Even a same-day source is behind the field dates it reports, so every
    # number is labelled with the date it is actually as of.
    def latest(s):
        return s[-1] if s else None

    t = {}
    if approval:
        asof_app = approval[-1]["date"]
        app_avg, app_n = average.adjusted_average(approval, asof_app)
        app_prev, _ = average.adjusted_average(
            approval, asof_app.fromordinal(asof_app.toordinal() - 30))
        t["trump_approval_avg"] = {
            "label": "Trump approval, adjusted average",
            "unit": "% approve",
            "value": round(app_avg, 1),
            "asof": asof_app.isoformat(),
            "delta_30d": (round(app_avg - app_prev, 1)
                          if app_prev is not None else None),
            "n_polls_window": app_n,
            "source": ("Silver Bulletin poll database, house-effect adjusted "
                       "here, 21-day window"),
        }
    if generic:
        asof_gen = generic[-1]["date"]
        gen_avg, gen_n = average.adjusted_average(generic, asof_gen)
        gen_prev, _ = average.adjusted_average(
            generic, asof_gen.fromordinal(asof_gen.toordinal() - 30))
        t["generic_ballot_avg"] = {
            "label": "2026 generic ballot, adjusted average",
            "unit": "margin, Dem minus Rep",
            "value": round(gen_avg, 1),
            "asof": asof_gen.isoformat(),
            "delta_30d": (round(gen_avg - gen_prev, 1)
                          if gen_prev is not None else None),
            "n_polls_window": gen_n,
            "source": ("Silver Bulletin poll database, house-effect adjusted "
                       "here, 21-day window"),
        }
    yg = latest(series.get("yougov_approval") or [])
    if yg:
        t["yougov_approval"] = {
            "label": "Economist/YouGov, latest wave",
            "unit": "% approve",
            "value": yg["value"],
            "asof": yg["date"],
            "source": "Silver Bulletin poll database (poll-level)",
        }
    mc = latest(series.get("mc_approval") or [])
    if mc:
        t["mc_approval"] = {
            "label": "Morning Consult, latest wave",
            "unit": "% approve",
            "value": mc["value"],
            "asof": mc["date"],
            "source": "Silver Bulletin poll database (poll-level)",
        }
    um = latest(series.get("umich_sentiment") or [])
    if um:
        t["umich_sentiment"] = {
            "label": "Michigan consumer sentiment",
            "unit": "index",
            "value": um["value"],
            "asof": um["date"],
            "next_release": next_umich_release or UMICH_NEXT_RELEASE,
            "source": umich_source or series_registry.MICHIGAN_SOURCE,
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
    # `open` means a participant can still file, which ends at the round's own
    # close. Read through `batches.effective_deadline` rather than `lock_at`
    # directly, so the page and the validator can never disagree about it.
    if now < batches.effective_deadline(r["lock_at"]):
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
    """(entrant_id, model, variant) for every condition this run will file.

    Route A participants are appended last and only when they can actually be
    called -- registered, not revoked, and this run holding the arena's
    signing key. A run without the key leaves them out of the roster rather
    than queuing them and failing every six hours: the entrant has not gone
    wrong, we have not finished setting up, and a red run every cycle through
    a week of setup trains everyone to ignore the colour.
    """
    roster = [] if elicitation_only() else list(harness.season_entrants())
    want = elicitation_variants()
    if want:
        roster += list(harness.elicitation_entrants(variants=want))
    for entrant in participants.registered():
        if participants.callable_now(entrant)[0]:
            # The model/context/elicitation slots are ours, not theirs: what
            # generates a participant's answer is their business, and is
            # recorded in their registration's `method`.
            roster.append((entrant, "agent-api", "participant", "participant"))
    return roster


def _merge_snapshot(round_id, updates):
    """Read, merge, write. Returns True.

    Merging rather than replacing, because two different freezes write into
    this file -- the close freeze below and `freeze_for_answering` -- and a
    whole-file rewrite by either one silently drops the other's field.
    """
    body = read_lock_snapshot(round_id) or {}
    body.update(updates)
    os.makedirs(LOCKS, exist_ok=True)
    with open(lock_snapshot_path(round_id), "w") as f:
        json.dump(body, f, indent=2, sort_keys=True)
        f.write("\n")
    return True


def freeze_for_answering(r, field, value, now):
    """What the entrants were handed for this round, frozen once and for all
    when the call window opened. Returns it, or None if nothing was frozen.

    **This is the boundary a null must be built on.** Every endpoint is called
    inside `[window_opens_at, effective_deadline)`, and what it is handed is
    fixed when that window opens, so whoever is called first and whoever is
    retried last answer the same question. A null frozen at the *close*, up to
    a day later, is a ruler that read what the people it measures could not:
    on `mc-2026-w37-approval` the entrants were called on a history ending at
    40.0 while the persistence null had already read the 46.0 that landed
    inside the window.

    Written every refresh until the window opens and never again. It has to be
    an observation time rather than a date filter for the same reason
    `update_lock_snapshot` exists: a monthly value is dated by its month label
    and published weeks later, so no filter on `date` can tell "existed when
    the window opened" from "labelled before it".

    Returns None for a round whose window had already opened when this landed.
    The callers then fall back to the older, later boundary. That is the old
    behaviour on purpose: what a series looked like twelve hours ago cannot be
    reconstructed, and inventing it would be worse than saying so.

    `value` is never replaced by an empty one -- see `update_lock_snapshot` for
    the build that blanked a round's history in one pass.
    """
    previous = (read_lock_snapshot(r["round_id"]) or {}).get(field)
    if now >= batches.window_opens_at(r["lock_at"]):
        return previous                   # frozen, or never taken
    if not value and previous:
        return previous
    _merge_snapshot(r["round_id"], {
        "round_id": r["round_id"],
        "lock_at": r["lock_at"],
        field: value,
        "answer_frozen_at": iso(now),
    })
    return value


def update_lock_snapshot(r, hist, now):
    """Record the history a round had at its close, while it is still open.

    Filtering by `date < lock_at` does not actually freeze anything for a
    monthly series, because the point's date is its month label, not its
    publication day: Michigan's August value is dated 2026-08-01 and published
    on the 14th, so a round locking on the 12th would absorb the very answer it
    is scored against the moment it appeared. The dates cannot distinguish
    "existed at lock" from "labelled before lock"; only observation time can.

    So while a round is open every refresh overwrites its snapshot, and after
    `batches.freeze_at` nothing touches it again. The last write before that
    moment is the freeze, and it is a committed artifact rather than something
    recomputed from data that has since changed underneath it.

    **This one answers "what had been published by the time answering
    stopped".** That is what `resolve.candidate` needs, to tell the round's
    answer from history it already had. It is *not* the boundary the nulls are
    built on -- that is `freeze_for_answering`, up to a day earlier, because
    entrants stop being handed new history when the window opens rather than
    when it closes.

    Empty history is never written over a snapshot that has some. A series
    missing from the map produces `hist == []`, which is a caller with an
    incomplete map -- not a tracker whose history disappeared -- and writing it
    destroys the one record of what the round's nulls saw. It has happened:
    a build that omitted `generic_ballot_margin` blanked that round's snapshot
    in one pass.
    """
    if now >= batches.freeze_at(r["lock_at"]):
        return False                      # frozen; never rewritten
    if not hist and (read_lock_snapshot(r["round_id"]) or {}).get("history"):
        return False                      # never trade a real freeze for nothing
    return _merge_snapshot(r["round_id"], {
        "round_id": r["round_id"],
        "series": r["series"],
        "lock_at": r["lock_at"],
        "observed_at": iso(now),
        "history": hist[-LOCK_SNAPSHOT_POINTS:],
    })


def build_rounds(season, series, resolved, now, ranking_obs=None):
    """Returns (rounds, history_by_round).

    The history is the slice frozen at the effective participant deadline;
    both baselines and the model harness condition on it, so entrants and nulls
    see one series. For pre-cutover rounds that boundary remains ``lock_at``.

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
        row["deadline"] = iso(batches.effective_deadline(r["lock_at"]))
        # When this round is listed, and which week it displays under. The
        # deadline is its own lock; the week is only how the site groups it,
        # and is null for a round older than the calendar. Published rather
        # than derived in the browser: a page that recomputes "which Monday"
        # owns a second copy of `ssa/batches.py` and drifts the first time the
        # calendar moves.
        row["published_at"] = iso(batches.published_at(r["lock_at"]))
        row["horizon_days"] = round(
            batches.horizon_days(r["lock_at"], r["release_at"]), 3)
        row["batch_id"] = (batches.batch_of(r["lock_at"])
                           if batches.governed_by_batch(r["lock_at"]) else None)
        # What the question is *about*, as opposed to what shape its answer
        # takes (`target_type`) or who published the figure (`tracker`). The
        # board groups on this, and it is published rather than derived in the
        # browser for the reason `batch_id` is: a second copy of the taxonomy
        # in a page drifts the first time a series is reassigned.
        # `strict=False`: an unplaced series is published by name rather
        # than stopping the run. `tests/test_domains.py` is the gate that
        # keeps one from ever getting this far.
        row["domain"] = domains.domain_of(r["series"], strict=False)
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
            attach_ranking(row, r, (ranking_obs or {}).get(r["round_id"]), now)
            hist_by_round[r["round_id"]] = []
            if r["round_id"] in resolved:
                row["resolution"] = resolved[r["round_id"]]
            out.append(row)
            continue
        # Baselines are frozen where the entrants answered -- when the call
        # window opened -- and only history strictly before the close counts.
        # Three reasons, and the third is why this is the window rather than
        # the close.
        #
        # Contamination: once a release lands in the series, a null built from
        # it would contain the outcome it is scored against.
        #
        # Comparability: the headline metric divides the entrant's CRPS by this
        # null's, so a null frozen anywhere later than the entrants answered
        # hands the denominator series the numerator never saw.
        #
        # And that is not hypothetical at a day's granularity: on
        # `mc-2026-w37-approval` the entrants were called on a history ending at
        # 40.0, and the persistence null had read the 46.0 that landed inside
        # the window. Every entrant was scored against a ruler that had seen the
        # answer move and they had not.
        close = batches.freeze_at(r["lock_at"])
        opens = batches.window_opens_at(r["lock_at"])
        lock_date = close.strftime("%Y-%m-%d")
        live = [p for p in (series.get(r["series"]) or []) if p["date"] < lock_date]
        # Runs until the close, so `history` -- what `resolve` needs -- stays
        # current after the window has shut.
        update_lock_snapshot(r, live, now)
        handed = freeze_for_answering(r, "answer_history",
                                      live[-LOCK_SNAPSHOT_POINTS:], now)
        if now < opens:
            hist = live                       # nobody has been called yet
        else:
            # In the window or past the close: whatever the entrants were
            # handed. Failing that -- a round whose window had already opened
            # when the two freezes were separated -- the close snapshot, and
            # failing that the date filter, which is only for rounds predating
            # snapshots and carries the flaw update_lock_snapshot describes.
            snap = read_lock_snapshot(r["round_id"]) or {}
            hist = handed or snap.get("history") or live
            row["history_source"] = (
                "window snapshot" if handed else
                "lock snapshot" if snap.get("history") else
                "date filter (pre-snapshot round)")
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
            attach_profile(row, r, series, now)
        if r["round_id"] in resolved:
            row["resolution"] = resolved[r["round_id"]]
        out.append(row)
    return out, hist_by_round


def attach_profile(row, r, series, now=None):
    """Attach the profile block: the round's cells and their frozen nulls.

    **The date filter alone is a day too coarse.** It is exact about what a
    cell's series *is*: the Civiqs cells are the daily dashboard, archived every
    day under the date it was read, so label and observation are the same day,
    and a point dated on or after the close is excluded from every null. What a
    date cannot say is what time of day a point appeared, and the call window is
    shorter than a day. A cell point dated the day before the close, archived
    in the evening, sits inside the window: the null reads it and an endpoint
    called that afternoon did not. That is the same inequality the scalar branch
    fixed, on the round type this module calls the headline one.

    So the per-cell history is frozen by observation time too
    (`freeze_for_answering`), and only falls back to the date filter for a round
    whose window had already opened when this landed -- which is every round
    scored so far, so nothing published moves.

    The Economist/YouGov crosstab cells are weekly waves and need no snapshot
    either, for the same reason: a wave is dated by its own field end, an
    observation date, and enters the workbook within days of it. The wave a
    round scores is published after the close that froze it, so the strict `<`
    on the freeze date excludes it from every entrant's null. What the date
    filter cannot do on its own is keep the *previous* wave from resolving the
    round; `profile_round.resolution` adds the round's own seven-day wave
    window for that, and `ssa/series.py`'s crosstab block says where the series
    are declared.
    """
    cells = profile_round.cells_for(r)
    hist = profile_round.frozen_history(r, series, cells)
    block_source = None
    if now is not None:
        handed = freeze_for_answering(
            r, "answer_history_by_cell",
            {c: hist[c][-LOCK_SNAPSHOT_POINTS:] for c in cells}, now)
        if now >= batches.window_opens_at(r["lock_at"]):
            # Every cell or none: a mixture of frozen and live cells is a
            # profile no entrant was ever shown, and the energy score reads the
            # vector as one thing.
            if handed and all(c in handed for c in cells):
                hist = {c: handed[c] for c in cells}
                block_source = "window snapshot"
            elif handed:
                block_source = "date filter (cells changed since the freeze)"
            else:
                block_source = "date filter (pre-snapshot round)"
    block = {
        "cells": list(cells),
        "labels": profile_round.labels_for(cells),
        "history_points": {c: len(hist[c]) for c in cells},
    }
    if block_source:
        block["history_source"] = block_source
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


def attach_ranking(row, r, obs, now=None):
    """Attach the ranking block: the round's spec, its frozen history, its null.

    **The date filter is exact about the week and blind to the hour.** Neither
    source has the month-label gap snapshots were built for: a Wikipedia week is
    dated by the Sunday it ends and its seven daily counts are final within
    about two days, and a Trends week is dated by its Saturday and takes the
    value the earliest archived snapshot showed. Label and observation are the
    same week either way.

    But a week is archived at some moment, and about one archive in seven lands
    inside a round's call window. The null would read that week and an endpoint
    called before it arrived would not, so the observations are frozen by
    observation time as well (`freeze_for_answering`), exactly as the scalar and
    profile branches do. A round whose window had already opened when this
    landed keeps the date filter alone.

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
        if now is not None:
            handed = freeze_for_answering(
                r, "answer_obs", hist[-LOCK_SNAPSHOT_POINTS:], now)
            if now >= batches.window_opens_at(r["lock_at"]):
                if handed:
                    hist = handed
                block["history_source"] = ("window snapshot" if handed else
                                           "date filter (pre-snapshot round)")
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


# Stop re-filing this long before the round closes. A refresh writes to the
# working tree, but the commit only lands minutes later; without the margin a
# run that starts just before the close could push a file that the merge-time
# audit then (correctly) rejects as late.
LOCK_MARGIN_SECONDS = 30 * 60

# One number, one forecast, bought at one fixed vantage point.
#
# Every entrant's forecast for a round is bought inside one window before that
# round closes: SSA_FILE_WINDOW_HOURS wide, 24 by default, ending
# LOCK_MARGIN_SECONDS before the close. A forecast stamped inside the window
# (`harness.filed_stamp`) is final -- data arriving afterwards does not reopen
# it -- so every entrant answers the same question from the same distance and a
# round costs one call per entrant per condition, ever.
#
# Everything the arena hands over is frozen at the window's opening: the
# history, the persistence null, and the news corpus (`information_asof`). So
# an entrant reached in the first minute and one retried in the last hour were
# shown the same thing, and the window bounds only what an entrant looks up for
# itself. Three days of that was a real advantage to whoever happened to be
# retried late, which is why the window is a day.
#
# The window spans ~4 six-hourly runs. Runs after the first are failure
# insurance: they buy a forecast that is still missing and never rewrite one
# that exists. SSA_BUY_BY_SECONDS, half the window by default, is the point
# after which an unstamped pre-window draft stands rather than being replaced.
#
# Baselines are exempt: they are free and the site shows them from listing.
# Web retrieval is scoped to the same window by construction, since the query
# turn cannot run before the window opens. FILE_WINDOW_SECONDS lives in
# harness because `_retrieve` and `filed_in_window` need it too.
FILE_WINDOW_SECONDS = harness.FILE_WINDOW_SECONDS
# The point inside the window after which an unstamped pre-window draft stands
# rather than being replaced. Derived from the window rather than set beside
# it: the two used to be three days and two days, and a window shorter than
# the boundary silently means "never replace a draft".
BUY_BY_SECONDS = float(os.environ.get("SSA_BUY_BY_SECONDS")
                       or FILE_WINDOW_SECONDS / 2)


def model_jobs_due(r, now):
    """True while the round's buy window (plus its insurance tail) is open.

    Measured back from the round's own close, so our models are called in the
    same window every external entrant is, and the retry tail stops
    `LOCK_MARGIN_SECONDS` before that close.
    """
    left = (batches.effective_deadline(r["lock_at"]) - now).total_seconds()
    return LOCK_MARGIN_SECONDS <= left <= FILE_WINDOW_SECONDS


def information_asof(r):
    """The fixed, already-observable boundary for shared context arms.

    Model buying starts ``FILE_WINDOW_SECONDS`` before the participant
    deadline. Freezing news at that window opening gives every entrant the
    same complete corpus, including entrants retried by a later refresh. Using
    the deadline (or the still-later lock) would request a future Wikipedia
    revision and freeze whichever partial page happened to exist at call time.
    """
    due = batches.effective_deadline(r["lock_at"])
    return iso(due - timedelta(seconds=FILE_WINDOW_SECONDS))


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
    left = (batches.effective_deadline(r["lock_at"]) - now).total_seconds()
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
        if participants.is_participant(entrant):
            # A Route A call bills the participant's own provider, not ours,
            # so it is genuinely $0 here. Named rather than reached by way of
            # the KeyError below, so "free" is a statement about who pays and
            # not a side effect of an id this module could not resolve.
            continue
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
    """{cell: history frozen at the effective deadline}, or None."""
    if not profile_round.is_profile(r):
        return None
    return profile_round.frozen_history(r, series)


def ranking_source_name(round_):
    """A ranking feed is not the scalar source with a similar brand name."""
    kind = ((round_.get("ranking") or {}).get("kind") or "unknown")
    return {
        "wiki_top10": "ranking_wikitop",
        "trends_basket": "ranking_trends_basket",
    }.get(kind, f"ranking_{kind}")


def ranking_observations(season, fetch=True, *, with_failures=False,
                         with_degraded=False):
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
    ``with_failures`` additionally returns the semantic source, affected round,
    and unusable error. ``with_degraded`` adds live-attempt errors for rounds
    that still have useful same-source archive history.  Those rows remain
    usable, but the operator source state must be stale/deadline-risk rather
    than silently healthy.
    """
    if with_degraded and not with_failures:
        raise ValueError("with_degraded requires with_failures")
    out, failures, degraded = {}, [], []
    for r in (season or {}).get("rounds", []):
        if not ranking_round.is_ranking(r):
            continue
        source = ranking_source_name(r)
        diagnostics = []
        try:
            rows = ranking_round.observations(
                r, fetch=fetch, diagnostics=diagnostics)
            if not rows:
                raise RuntimeError(
                    "no complete pre-lock ranking observation is available")
            out[r["round_id"]] = rows
            if diagnostics:
                degraded.append((source, r["round_id"], diagnostics))
        except Exception as e:                     # noqa: BLE001 - reported
            print(f"  ranking {r['round_id']}: no observations "
                  f"({type(e).__name__}: {e})")
            out[r["round_id"]] = []
            failures.append((source, r["round_id"], e))
    if with_degraded:
        return out, failures, degraded
    return (out, failures) if with_failures else out


def load_ranking_sources(season, run_status, *, fetch=True,
                         next_deadline=None, next_lock=None):
    """Load and account for ranking feeds without conflating their semantics."""
    groups = {}
    for definition in (season or {}).get("rounds", []):
        if ranking_round.is_ranking(definition):
            groups.setdefault(
                ranking_source_name(definition), []).append(
                    definition["round_id"])
    for name in sorted(groups):
        run_status.source_started(
            name, route=f"ssa.ranking:{name}",
            next_lock=next_lock, next_deadline=next_deadline)

    observations, faults, degradations = ranking_observations(
        season, fetch=fetch, with_failures=True, with_degraded=True)
    faults_by_source = {}
    for name, round_id, error in faults:
        faults_by_source.setdefault(name, []).append((round_id, error))
    degraded_by_source = {}
    for name, round_id, diagnostics in degradations:
        degraded_by_source.setdefault(name, []).append((round_id, diagnostics))

    source_failures = []
    for name, round_ids in sorted(groups.items()):
        failed = faults_by_source.get(name) or []
        degraded = degraded_by_source.get(name) or []
        if failed:
            combined = RuntimeError("; ".join(
                f"{round_id}: {type(error).__name__}: {error}"
                for round_id, error in failed))
            run_status.source_failed(
                name, combined, route=f"ssa.ranking:{name}",
                next_lock=next_lock, next_deadline=next_deadline,
                evidence="affected_rounds=" + ",".join(
                    round_id for round_id, _error in failed)
                + ("; degraded_archive_rounds=" + ",".join(
                    round_id for round_id, _items in degraded)
                   if degraded else ""))
            source_failures.append((name, combined))
        elif degraded:
            messages, evidence = [], []
            for round_id, items in degraded:
                for item in items:
                    error = item.get("error") if isinstance(item, dict) else item
                    messages.append(
                        f"{round_id}/{(item.get('scope') if isinstance(item, dict) else 'live')}: "
                        f"{type(error).__name__}: {error}")
                    if isinstance(item, dict) and item.get("archive_evidence"):
                        evidence.append(str(item["archive_evidence"]))
            run_status.source_degraded(
                name, RuntimeError("; ".join(messages)),
                route=f"ssa.ranking:{name}",
                next_lock=next_lock, next_deadline=next_deadline,
                archive_evidence=("; ".join(sorted(set(evidence))) or
                                  "same-source ranking archive used"))
        else:
            weeks = sum(len(observations.get(round_id) or [])
                        for round_id in round_ids)
            run_status.source_succeeded(
                name, route=f"ssa.ranking:{name}",
                evidence=(f"{weeks} complete weekly observations across "
                          f"{len(round_ids)} round(s)"),
                next_lock=next_lock, next_deadline=next_deadline)
    return observations, source_failures


def read_forecast(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except ValueError:
        return None


def scoreable_forecast(forecast):
    """False for a labelled local placeholder, whatever its valid shape.

    ``SSA_ALLOW_MOCK=1`` remains useful for rendering a local site without
    credentials.  It must never turn that placeholder into a leaderboard row
    or a member of the crowd mixture: syntactic validity is not evidence that
    an entrant answered.
    """
    return bool(forecast) and not (
        (forecast.get("notes") or "").startswith("MOCK"))


def file_baseline_forecasts(rounds, hist_by_round, now, series=None,
                            ranking_obs=None, run_status=None):
    """Write every entrant's forecast for each open round.

    Returns (files_written, failures). Failures are messages, never mocks: a
    placeholder filed on error is a green workflow hiding a wrong model name.
    """
    written = 0
    failures = []
    jobs = []
    roster_cache = None

    def roster():
        nonlocal roster_cache
        if roster_cache is None:
            roster_cache = list(season_roster())
        return roster_cache

    def configured_route(entrant):
        try:
            return harness.route(entrant)
        except Exception as exc:                    # surfaced by the job too
            return f"unconfigured:{type(exc).__name__}"

    def observed_route(entrant, forecast):
        notes = (forecast or {}).get("notes") or ""
        matched = re.search(r"(?:^|[,; ]+)via=([^,; ]+)", notes)
        via = matched.group(1) if matched else None
        if via:
            try:
                return harness.route(entrant, via=via)
            except Exception:                       # route remains visible below
                return via
        return configured_route(entrant)

    def record_existing(r, entrant, path, forecast):
        if run_status is None or not scoreable_forecast(forecast):
            return
        run_status.entrant_queued(
            r["round_id"], entrant, lock_at=r["lock_at"],
            deadline=batches.effective_deadline(r["lock_at"]),
            route=observed_route(entrant, forecast),
            action="preserve the already-filed valid forecast")
        run_status.entrant_succeeded(
            r["round_id"], entrant,
            route=observed_route(entrant, forecast),
            artifact=os.path.relpath(path, ROOT))

    for r in rounds:
        if r["status"] != "open":
            continue
        deadline = batches.effective_deadline(r["lock_at"])
        # Batch deadlines can precede a round's lock by almost a week.  During
        # that interval the public round still says "open", but filing is
        # already closed.  Name every missing real answer as missed instead of
        # letting it disappear from a jobs list whose due predicate is false.
        if run_status is not None and now >= deadline:
            rdir = os.path.join(FORECASTS, r["round_id"])
            for entrant, _model, _ctx, _eli in roster():
                path = os.path.join(rdir, entrant + ".json")
                previous = read_forecast(path)
                if previous is None or not scoreable_forecast(previous):
                    run_status.entrant_missed(
                        r["round_id"], entrant, lock_at=r["lock_at"],
                        deadline=deadline, route=configured_route(entrant))
                else:
                    record_existing(r, entrant, path, previous)
        if (parse_iso(r["lock_at"]) - now).total_seconds() < LOCK_MARGIN_SECONDS:
            continue
        rdir = os.path.join(FORECASTS, r["round_id"])
        if not nulls_for(r):
            # A source failure removes only its series, which deliberately
            # leaves the affected round without a persistence denominator.
            # Hold those calls visibly; buying a model answer without the
            # frozen input it is meant to see is not a useful partial result.
            if run_status is not None and model_jobs_due(r, now):
                for entrant, _model, _ctx, _eli in roster():
                    path = os.path.join(rdir, entrant + ".json")
                    previous = read_forecast(path)
                    if scoreable_forecast(previous):
                        record_existing(r, entrant, path, previous)
                    else:
                        run_status.entrant_queued(
                            r["round_id"], entrant, lock_at=r["lock_at"],
                            deadline=deadline, route=configured_route(entrant),
                            next_retry=now + timedelta(hours=6),
                            action=("hold until this round has a validated source "
                                    "and persistence baseline; no provider call made"))
            continue
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
                "notes": ("auto-filed baseline (" + method
                          + "), frozen at participant deadline"),
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
        for entrant, _model, _ctx, _eli in roster():
            path = os.path.join(rdir, entrant + ".json")
            if not job_still_due(r, path, now):
                record_existing(r, entrant, path, read_forecast(path))
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

    # The same object for ranking rounds: weeks strictly before the effective
    # deadline, so an entrant sees exactly the history persistence saw.
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
                news_cache[rid] = newsdigest.for_round(rid, information_asof(r))
            return news_cache[rid]

    def run_job(job):
        r, entrant, path = job
        if run_status is not None:
            run_status.entrant_started(r["round_id"], entrant)
        try:
            # A Route A participant has no context axis: `resolve` knows only
            # our model ids and raises on theirs, which used to fail every
            # participant job here before a request was built.
            if participants.is_participant(entrant):
                context = None
            else:
                _, context, _elicitation = harness.resolve(entrant)
            news = news_for(r) if context == "news" else None
            if context == "news" and not (news or {}).get("text") \
                    and not (news or {}).get("window_closed"):
                # A round due far out has a news window mostly in the
                # future; the digest grows a day at a time and this job
                # starts succeeding as the deadline approaches. Not a failure:
                # nothing is wrong and nothing was spent -- an empty digest
                # on a CLOSED window still falls through and fails loudly.
                if run_status is not None:
                    run_status.entrant_deferred(
                        r["round_id"], entrant,
                        next_retry=now + timedelta(hours=6),
                        action="wait for the model-filing window to open")
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
            # would strand every other entrant's forecast unwritten, and the
            # participant deadline is hard. The successes land; main() reports
            # every failure and exits non-zero, so a run is loudly broken
            # without being silently incomplete.
            failures.append(f"{r['round_id']}/{entrant}: {e}")
            if run_status is not None:
                run_status.entrant_failed(r["round_id"], entrant, e)
            return 0
        with open(path, "w") as f:
            json.dump(body, f, indent=2)
            f.write("\n")
        if run_status is not None:
            if not scoreable_forecast(body):
                run_status.entrant_failed(
                    r["round_id"], entrant,
                    "labelled MOCK artifact was filed for local rendering and "
                    "is excluded from scoring")
            else:
                route = observed_route(entrant, body)
                via = route.get("via") if isinstance(route, dict) else route
                primary = configured_route(entrant)
                primary_via = primary.get("via") if isinstance(primary, dict) else None
                fallback_error = None
                if via == "openrouter" and primary_via == "direct":
                    fallback_error = (
                        "configured direct route failed terminally; standby used")
                run_status.entrant_succeeded(
                    r["round_id"], entrant, route=route,
                    artifact=os.path.relpath(path, ROOT),
                    fallback_error=fallback_error)
        return 1

    if jobs:
        billable, usd = price_jobs(jobs, hist_by_round, read_forecast,
                                   news_for, prof_hist, rank_hist)
        costs = {(r["round_id"], entrant): cost
                 for r, entrant, _path, cost in billable}
        if run_status is not None:
            for r, entrant, _path in jobs:
                run_status.entrant_queued(
                    r["round_id"], entrant, lock_at=r["lock_at"],
                    deadline=batches.effective_deadline(r["lock_at"]),
                    route=configured_route(entrant),
                    estimated_spend=costs.get((r["round_id"], entrant), 0.0))
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
            if run_status is not None:
                for r, entrant, _path, cost in tail:
                    run_status.entrant_withheld(
                        r["round_id"], entrant, estimated_spend=cost)
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
                        if not scoreable_forecast(fc):
                            continue
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


# What `site/index.html` and `site/leaderboard.html` read from every round
# without checking first. A hole in any of them renders as the literal word
# "undefined" on a public page.
#
# Checked here rather than only in the tests because the tests run against the
# committed `site/data.json` while the live one is rebuilt every six hours by
# the cron and never passes through them. A missing field would reach a
# participant before it reached CI.
SITE_ROUND_FIELDS = ("round_id", "question", "lock_at", "release_at",
                     "status", "target_type", "deadline", "series", "domain")

# Present or absent, but never the wrong shape when present.
SITE_ROUND_TYPES = {
    "cells": list, "forecasts": dict, "resolution": dict,
    "baselines": dict, "n_forecasts": int, "batch_id": (str, type(None)),
    "published_at": (str, type(None)),
    "horizon_days": (float, int, type(None)),
}


def assert_site_contract(rounds):
    """Refuse to publish a round the site cannot render.

    `site/data.json` is a public artifact read by two pages that were written
    against it. This is the seam where the two agree, and it is checked on the
    way out: failing the run is recoverable, and a page telling a participant
    the answer is `undefined` is not.
    """
    problems = []
    for r in rounds:
        rid = r.get("round_id") or "<no round_id>"
        for k in SITE_ROUND_FIELDS:
            if r.get(k) in (None, ""):
                problems.append(f"{rid}: {k} is missing; the site renders it "
                                "unconditionally")
        for k, want in SITE_ROUND_TYPES.items():
            if k in r and r[k] is not None and not isinstance(r[k], want):
                problems.append(f"{rid}: {k} is {type(r[k]).__name__}, "
                                f"expected {want}")
        res = r.get("resolution")
        if isinstance(res, dict) and "value" in res:
            if not isinstance(res["value"], (int, float)) \
                    and not isinstance(res["value"], (list, dict)):
                problems.append(f"{rid}: resolution.value is "
                                f"{type(res['value']).__name__}")
    if problems:
        raise RuntimeError(
            f"{len(problems)} round(s) would render broken on the site:\n  "
            + "\n  ".join(problems[:12])
            + ("\n  ..." if len(problems) > 12 else ""))


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
            if not scoreable_forecast(fc):
                continue
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
            if not scoreable_forecast(fc):
                skipped.append((f"{r['round_id']}/{fn[:-5]}",
                                "labelled MOCK excluded from scoring"))
                continue
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
            if not scoreable_forecast(fc):
                skipped.append((f"{r['round_id']}/{fn[:-5]}",
                                "labelled MOCK excluded from scoring"))
                continue
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


def next_operational_times(season, now):
    """(next close, next round lock), both after ``now``. They are the same
    instant now that a round closes on its own lock, and both are kept because
    the operator rows name them separately.

    They are computed once and attached to every source row.  A source outage
    without the next moment it can hurt is an alert an operator cannot triage.
    """
    locks = [parse_iso(r["lock_at"]) for r in (season or {}).get("rounds", [])]
    future_locks = sorted(t for t in locks if t > now)
    future_deadlines = sorted({batches.effective_deadline(r["lock_at"])
                               for r in (season or {}).get("rounds", [])
                               if batches.effective_deadline(r["lock_at"]) > now})
    return (future_deadlines[0] if future_deadlines else None,
            future_locks[0] if future_locks else None)


def run_source_tasks(tasks, run_status, *, next_deadline=None, next_lock=None):
    """Run independent source loaders without discarding sibling successes.

    A task is ``(name, route, loader[, same_source_archive])``.  ``loader``
    validates before it archives and returns ``(value, evidence)``.  The
    optional archive loader re-parses the exact manifest body for *that same
    source*; it is marked stale/deadline-risk and is never treated as evidence
    of a new release.  Every task runs even when an earlier one fails, so a 403
    on one source cannot prevent another source's valid vintage from being
    committed.
    """
    values, failures = {}, []
    for task in tasks:
        if len(task) == 3:
            name, route, loader = task
            archive = None
        elif len(task) == 4:
            name, route, loader, archive = task
        else:
            raise ValueError("source task must have 3 or 4 fields")
        run_status.source_started(
            name, route=route, next_lock=next_lock,
            next_deadline=next_deadline)
        try:
            value, evidence = loader()
        except Exception as exc:                       # noqa: BLE001 - isolated
            if archive is not None:
                try:
                    value, evidence = archive()
                except Exception as archive_error:     # noqa: BLE001 - reported together
                    combined = RuntimeError(
                        f"live attempt failed ({type(exc).__name__}: {exc}); "
                        "no valid same-source archive was usable "
                        f"({type(archive_error).__name__}: {archive_error})")
                    run_status.source_failed(
                        name, combined, route=route, next_lock=next_lock,
                        next_deadline=next_deadline)
                    failures.append((name, combined))
                    continue
                values[name] = value
                run_status.source_degraded(
                    name, exc, route=route, archive_evidence=evidence,
                    next_lock=next_lock, next_deadline=next_deadline)
                continue
            run_status.source_failed(
                name, exc, route=route, next_lock=next_lock,
                next_deadline=next_deadline)
            failures.append((name, exc))
            continue
        values[name] = value
        run_status.source_succeeded(
            name, route=route, evidence=evidence, next_lock=next_lock,
            next_deadline=next_deadline)
    return values, failures


def build_and_account_registry_sources(
        approval, generic, umich_rows, sources, unavailable_sources,
        run_status, *, next_deadline=None, next_lock=None):
    """Main's scalar-registry boundary, callable for fault-injection tests.

    Adapters may keep serving a validated same-source archive after their live
    request fails.  ``series.build_all`` collects those non-fatal diagnostics;
    this seam preserves the resulting series but turns the semantic source
    stale/deadline-risk.  A derivation that cannot return valid rows remains an
    atomic source failure and removes only that source's series.
    """
    registry_sources = sorted(
        {spec["source"] for spec in series_registry.SERIES.values()}
        - {"sb_approval", "sb_generic", "umich", "aaii"})
    for name in registry_sources:
        run_status.source_started(
            name, route=f"ssa.series:{name}",
            next_lock=next_lock, next_deadline=next_deadline)

    diagnostics = []
    try:
        series, registry_failures = build_series(
            approval, generic, umich_rows, sources,
            unavailable_sources=unavailable_sources,
            isolate_failures=True, source_diagnostics=diagnostics)
    except Exception as error:                     # noqa: BLE001 - persisted by caller
        run_status.source_failed(
            "series_registry", error, route="ssa.series.build_all",
            next_lock=next_lock, next_deadline=next_deadline)
        raise

    by_source = {}
    for item in diagnostics:
        if not isinstance(item, dict):
            item = {"source": "series_registry", "error": item,
                    "archive_evidence": "same-source archive used"}
        by_source.setdefault(item.get("source") or "series_registry", []).append(item)

    new_failures = []
    for name, error in sorted(registry_failures.items()):
        route = f"ssa.series:{name}" if name in registry_sources else None
        run_status.source_failed(
            name, error, route=route, next_lock=next_lock,
            next_deadline=next_deadline,
            evidence="series derivation rejected this source atomically")
        new_failures.append((name, error))

    for name in registry_sources:
        if name in registry_failures:
            continue
        count = sum(spec["source"] == name and sid in series
                    for sid, spec in series_registry.SERIES.items())
        degraded = by_source.get(name) or []
        if degraded:
            messages, evidence = [], []
            for item in degraded:
                error = item.get("error")
                scope = item.get("scope") or "live"
                messages.append(
                    f"{scope}: {type(error).__name__}: "
                    f"{reliability.error_text(error)}")
                if item.get("archive_evidence"):
                    evidence.append(str(item["archive_evidence"]))
            run_status.source_degraded(
                name, RuntimeError("; ".join(messages)),
                route=f"ssa.series:{name}",
                archive_evidence=("; ".join(sorted(set(evidence))) or
                                  "same-source adapter archive used"),
                next_lock=next_lock, next_deadline=next_deadline)
        else:
            run_status.source_succeeded(
                name, route=f"ssa.series:{name}",
                evidence=f"{count} validated non-empty series",
                next_lock=next_lock, next_deadline=next_deadline)
    return series, registry_failures, new_failures, diagnostics


def print_operator_status(run_status):
    print("\noperator:")
    for line in run_status.summary():
        print(" ", line)


def _load_previous_operator():
    try:
        with open(OPERATOR) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None



def main():
    # Local runs read keys from .env; in CI they arrive as Actions secrets
    # and no .env exists, so already-set variables always win.
    envfile.load()
    now = now_utc()
    with open(QUESTIONS) as f:
        season = json.load(f)
    next_deadline, next_lock = next_operational_times(season, now)
    run_status = reliability.RunStatus(now)
    skip_filing = os.environ.get("SSA_SKIP_FILING") == "1"
    if skip_filing:
        run_status.carry_entrant_states(_load_previous_operator())

    # One fetch per upstream file, shared by the series registry and by the
    # averages below, so the site's headline numbers and its series can never
    # be built from different snapshots of the same source.
    # A body becomes the current archive vintage only after its parser accepts
    # it. A 200 carrying a login page or a shifted table is evidence of a
    # failed attempt, not a safe replacement for yesterday's valid vintage.
    # Twenty-one of the registered series come out of one published Google
    # Sheet that is revised in place, so without a dated vintage a resolution
    # computed from it today cannot be rechecked tomorrow. See ssa/provenance.py.

    def load_sb(name, url, note):
        def load():
            raw = silverbulletin.fetch_text(url)
            rows = validate_silverbulletin_rows(
                name, silverbulletin.parse(raw),
                today=now.date())  # before manifest update
            block = provenance.record(name, url, raw, note=note)
            return {"rows": rows, "provenance": block}, \
                f"{block['file']} sha256={block['sha256']}"
        return load

    def archive_sb(name, url):
        def load():
            raw, block = provenance.current(name, expected_url=url)
            rows = validate_silverbulletin_rows(
                name, silverbulletin.parse(raw.decode("utf-8")),
                today=now.date())
            return {"rows": rows, "provenance": block}, \
                (f"same-source {block['file']} sha256={block['sha256']} "
                 f"fetched_at={block.get('fetched_at')}")
        return load

    def load_umich():
        rows = series_registry.michigan_history()
        raw = encode_umich_composite(
            series_registry.MICHIGAN_RAW,
            series_registry.MICHIGAN_PRELIM_RAW)
        block = provenance.record(
            "umich", series_registry.MICHIGAN_URL,
            raw, ext="json",
            note=(series_registry.MICHIGAN_SOURCE + "; hash-covered canonical "
                  f"raw composite of {umich.URL} and {umich.PRELIM_URL}"))
        return {"rows": rows, "provenance": block}, \
            f"{block['file']} sha256={block['sha256']}"

    def archive_umich():
        # Re-run both live parsers over the manifest-hashed envelope.  A legacy
        # finals-only file, a missing preliminary response, or a modified byte
        # is unsafe.  site/data.json is derived output, never source evidence.
        raw, block = provenance.current(
            "umich", expected_url=series_registry.MICHIGAN_URL)
        rows = parse_umich_composite(raw)
        return {"rows": rows, "provenance": block}, \
            (f"same-source {block['file']} sha256={block['sha256']} contains "
             "validated finals+preliminary raw responses")

    # AAII serves a ~22-week rolling window with no deeper machine-readable
    # history, so the committed vintages *are* the long history: each week the
    # window slides and the archive keeps the week that fell off. The body is
    # parsed with the asof from the response that carried it (the page's dates
    # have no year), and both go into `sources` so the registry never fetches
    # a second, different snapshot of the same page.
    def load_aaii():
        raw, asof = aaii.fetch_text()
        rows = aaii.parse(raw, asof)       # validate before manifest update
        block = provenance.record(
            "aaii", aaii.URL, raw, ext="html",
            note=("AAII sentiment survey results page, a ~22-week rolling "
                  f"window parsed against the response's own date {asof}; "
                  "the full 1987-present .xls is OLE2 and unreadable without "
                  "a dependency"))
        return {"rows": rows, "provenance": block}, \
            f"{block['file']} sha256={block['sha256']}"

    def archive_aaii():
        raw, block = provenance.current("aaii", expected_url=aaii.URL)
        match = re.search(
            r"parsed against the response's own date (\d{4}-\d{2}-\d{2})",
            block.get("note") or "")
        if not match:
            raise RuntimeError(
                "aaii: archived vintage has no response-date parsing anchor")
        asof = date.fromisoformat(match.group(1))
        rows = aaii.parse(raw.decode("utf-8"), asof)
        return {"rows": rows, "provenance": block}, \
            (f"same-source {block['file']} sha256={block['sha256']} "
             f"parsed_asof={asof}")

    loaded, source_failures = run_source_tasks([
        ("sb_approval", silverbulletin.APPROVAL_URL,
         load_sb("sb_approval", silverbulletin.APPROVAL_URL,
                 "Silver Bulletin poll database, published as a Google Sheet"),
         archive_sb("sb_approval", silverbulletin.APPROVAL_URL)),
        ("sb_generic", silverbulletin.GENERIC_URL,
         load_sb("sb_generic", silverbulletin.GENERIC_URL,
                 "Silver Bulletin generic-ballot database, published as a Google Sheet"),
         archive_sb("sb_generic", silverbulletin.GENERIC_URL)),
        ("umich", series_registry.MICHIGAN_URL, load_umich, archive_umich),
        ("aaii", aaii.URL, load_aaii, archive_aaii),
    ], run_status, next_deadline=next_deadline, next_lock=next_lock)

    # Every independent loader above has already run and every success is on
    # disk.  A source with neither live data nor a validated same-source archive
    # makes only its own series unavailable.  The registry below omits those
    # series, so their rounds have no baseline and are held without a provider
    # call; unrelated rounds still file.  The run remains red at the end.
    unavailable_sources = {name for name, _error in source_failures}
    sources = {name: block["rows"] for name, block in loaded.items()}
    prov = {name: block["provenance"] for name, block in loaded.items()}
    for name, block in sorted(prov.items()):
        print(f"  {name:12s} {block['bytes']:>9,}B  sha {block['sha256'][:12]}")
    approval = (silverbulletin.approval_polls(rows=sources["sb_approval"])
                if "sb_approval" in sources else [])
    generic = (silverbulletin.generic_ballot_polls(rows=sources["sb_generic"])
               if "sb_generic" in sources else [])
    umich_rows = sources.get("umich") or []
    try:
        series, registry_failures, new_failures, _diagnostics = \
            build_and_account_registry_sources(
                approval, generic, umich_rows, sources,
                unavailable_sources, run_status,
                next_deadline=next_deadline, next_lock=next_lock)
    except Exception as error:                     # noqa: BLE001 - persisted below
        run_status.write(OPERATOR)
        print_operator_status(run_status)
        raise
    source_failures.extend(new_failures)
    unavailable_sources.update(registry_failures)
    if "sb_approval" in registry_failures:
        approval = []
    if "sb_generic" in registry_failures:
        generic = []
    if "umich" in registry_failures:
        umich_rows = []

    try:
        trackers = build_trackers(
            approval, generic, series,
            next_release_for(season, "umich_sentiment", now),
            umich_source=(prov.get("umich") or {}).get("note"))
    except Exception as error:                     # noqa: BLE001 - persisted below
        run_status.source_failed(
            "series_registry", error, route="ssa.series.build_trackers",
            next_lock=next_lock, next_deadline=next_deadline)
        run_status.write(OPERATOR)
        print_operator_status(run_status)
        raise
    resolved = {}
    if os.path.exists(RESOLVED):
        with open(RESOLVED) as f:
            resolved = json.load(f)

    # Ranking rounds read ordered lists rather than a scalar series, from their
    # own archives. Gathered once, before the rounds are built, so that no
    # network call happens inside `build_rounds` and so every consumer below --
    # the nulls, the prompts, the pricing pass and the board -- reads the
    # identical object.
    ranking_obs, ranking_source_failures = load_ranking_sources(
        season, run_status, fetch=True, next_deadline=next_deadline,
        next_lock=next_lock)
    source_failures.extend(ranking_source_failures)

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
    if skip_filing:
        print("\nSSA_SKIP_FILING=1: rebuilding from what is on disk, "
              "calling no provider")
        filed, filing_failures = 0, []
    else:
        filed, filing_failures = file_baseline_forecasts(
            rounds, hist_by_round, now, series, ranking_obs,
            run_status=run_status)
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
    for health_row in source_health:
        run_status.source_health(
            health_row, next_lock=next_lock, next_deadline=next_deadline)
    operator_status = run_status.write(OPERATOR)
    print_operator_status(run_status)
    board = build_leaderboard(rounds, resolved)
    profile_board = build_profile_leaderboard(rounds, resolved, series)
    ranking_board = build_ranking_leaderboard(rounds, resolved, ranking_obs)
    replay_series = {
        name: series[name]
        for name in ("umich_sentiment", "yougov_approval", "mc_approval",
                     "yougov_generic_margin")
        if name in series
    }
    bt = backtest.run(replay_series)
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

    charts = {}
    if approval:
        charts["approval_avg"] = average.weekly_series(approval, 80)
    if generic:
        charts["generic_margin"] = average.weekly_series(generic, 80)
    for name in ("umich_sentiment", "yougov_approval", "mc_approval"):
        if name in series:
            charts[name] = (series[name][-48:]
                            if name == "umich_sentiment" else series[name])

    # The task registry is checked before anything is written: a task that names a
    # series the pipeline does not know is a registry bug, not a data hole.
    task_rows = task_registry.publish_or_raise()
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
        "charts": charts,
        "series_tail": {k: v[-8:] for k, v in series.items()},
        "tasks": task_rows,
        "entrant_status": entrant_status.build(rounds, load_entrants()),
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
        # Finite source and entrant-round states with the evidence and action
        # an operator needs.  Also written to site/operator.json so a source
        # failure that prevents data.json from rebuilding still leaves a
        # machine-readable incident record.
        "operator_status": operator_status,
        "series_provenance": {
            sid: prov.get(spec["source"], {}).get("source", spec["source"])
            for sid, spec in series_registry.SERIES.items()
        },
    }
    # Last gate before a public artifact. See `assert_site_contract`.
    assert_site_contract(data["rounds"])
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
          "| umich points:", len(umich_rows))
    print("approval avg:",
          (trackers.get("trump_approval_avg") or {}).get("value", "unavailable"),
          "| generic margin:",
          (trackers.get("generic_ballot_avg") or {}).get("value", "unavailable"))

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

    if source_failures:
        print(f"\n{len(source_failures)} source(s) failed; affected rounds were "
              "held and unrelated forecasts were preserved:")
        for name, error in source_failures:
            print(f"  - {name}: {type(error).__name__}: {error}")

    if filing_failures:
        # site/data.json and every successful forecast are already on disk, so
        # the workflow's commit step (which runs with if: always()) still lands
        # them and a round does not miss its participant deadline over one bad
        # provider. The non-zero exit makes the failure impossible to ignore.
        print(f"\n{len(filing_failures)} forecast(s) failed and were NOT filed:")
        for f in filing_failures:
            print("  -", f)
    if source_failures or filing_failures:
        details = []
        if source_failures:
            details.append(f"{len(source_failures)} source(s)")
        if filing_failures:
            details.append(f"{len(filing_failures)} entrant forecast(s)")
        raise SystemExit(
            " and ".join(details) + " failed. Affected rounds were held; "
            "successful source vintages and forecasts were kept. Nothing was "
            "silently substituted or mocked for scoring.")


if __name__ == "__main__":
    main()
