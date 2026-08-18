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
#   SSA_ELICITATION=news            just the fixed news corpus
#   SSA_ELICITATION=news,superfc    two of them
#   SSA_ELICITATION=1 / all         every arm, as before
#
# Unset means none, which stays the default: nothing about merging this starts
# spending anything.
def elicitation_variants(value=None):
    """Which elicitation arms this run files, from SSA_ELICITATION.

    Raises on an unknown name rather than silently filing nothing: a typo in a
    workflow variable is otherwise invisible until someone notices a leaderboard
    row that never appeared.
    """
    raw = (os.environ.get("SSA_ELICITATION") if value is None else value) or ""
    raw = raw.strip()
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
    roster = list(harness.season_entrants())
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


def build_rounds(season, series, resolved, now):
    """Returns (rounds, history_by_round). The history is the strictly pre-lock
    slice each round's baselines were computed from; the model harness
    conditions on exactly the same data, so entrants and nulls see one series."""
    out = []
    hist_by_round = {}
    for r in season["rounds"]:
        row = {k: r[k] for k in ("round_id", "tracker", "series", "question", "unit",
                                  "release_at", "release_estimated", "lock_at", "resolve")}
        row["status"] = round_status(r, resolved, now)
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
        if r["round_id"] in resolved:
            row["resolution"] = resolved[r["round_id"]]
        out.append(row)
    return out, hist_by_round


# Stop re-filing this long before lock_at. A refresh writes to the working
# tree, but the commit only lands minutes later; without the margin a run that
# starts just before lock could push a file that the merge-time lock audit
# then (correctly) rejects as late.
LOCK_MARGIN_SECONDS = 30 * 60

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


def price_jobs(jobs, hist_by_round, read_forecast, news_for):
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
            prompt = harness.build_prompt(
                r, hist_by_round.get(r["round_id"]), ctx, eli,
                news=news_for(r) if ctx == "news" else None)
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


def read_forecast(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except ValueError:
        return None


def file_baseline_forecasts(rounds, hist_by_round, now):
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
        if r["status"] != "open" or not r.get("baselines"):
            continue
        if (parse_iso(r["lock_at"]) - now).total_seconds() < LOCK_MARGIN_SECONDS:
            continue
        rdir = os.path.join(FORECASTS, r["round_id"])
        os.makedirs(rdir, exist_ok=True)
        for name, fc in r["baselines"].items():
            path = os.path.join(rdir, name + ".json")
            body = {
                "round_id": r["round_id"],
                "entrant": name,
                "topline": {"mean": fc["mean"], "sd": fc["sd"]},
                "notes": "auto-filed baseline (" + fc.get("method", name) + "), refreshed until lock",
            }
            with open(path, "w") as f:
                json.dump(body, f, indent=2)
                f.write("\n")
            written += 1
        # Every model runs both conditions and they are filed as separate
        # entrants: same weights, different information, so their scores answer
        # different questions and belong on different leaderboard rows.
        for entrant, _model, _ctx, _eli in season_roster():
            jobs.append((r, entrant, os.path.join(rdir, entrant + ".json")))

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
            body = harness.forecast(entrant, r,
                                    history=hist_by_round.get(r["round_id"]),
                                    previous=read_forecast(path),
                                    news=news)
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
                                   news_for)
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

    rounds, hist_by_round = build_rounds(season, series, resolved, now)
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
        filed, filing_failures = file_baseline_forecasts(rounds, hist_by_round, now)
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
