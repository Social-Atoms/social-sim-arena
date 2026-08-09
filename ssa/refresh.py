"""Build site/data.json from live sources.

Run:  python -m ssa.refresh
Cron: .github/workflows/refresh.yml runs this daily and commits the result.

Everything the entry page shows comes from this file: live tracker values
(Silver Bulletin poll CSVs, Michigan's own table with FRED as fallback,
VoteHub for the Congress and Supreme Court trackers), round status computed against the clock, and baseline
forecasts (persistence, trend) computed from the real series.
"""
import concurrent.futures
import json
import os
from datetime import date, datetime, timezone

from .adapters import fredcsv, silverbulletin, umich
from . import average, backtest, baselines, envfile, harness, scoring, sharecard
from . import series as series_registry

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUESTIONS = os.path.join(ROOT, "questions", "season0.json")
RESOLVED = os.path.join(ROOT, "resolutions", "resolved.json")
FORECASTS = os.path.join(ROOT, "forecasts")
ENTRANTS = os.path.join(ROOT, "entrants")
OUT = os.path.join(ROOT, "site", "data.json")

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
        hist = [p for p in (series.get(r["series"]) or []) if p["date"] < lock_date]
        hist_by_round[r["round_id"]] = hist
        if len(hist) >= 3:
            target = r["release_at"][:10]
            row["baselines"] = baselines.all_baselines(hist, target)
        else:
            row["baselines"] = None
            row["baseline_note"] = "no machine-readable series yet for this tracker"
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
        for entrant, _model, _variant in harness.season_entrants():
            jobs.append((r, entrant, os.path.join(rdir, entrant + ".json")))

    # One provider call per job, and at max reasoning effort a single call can
    # take a minute. Sequentially that is hours for a full season; the calls are
    # independent, so they run concurrently. Results are written by the worker
    # that produced them, and `failures` is appended under the GIL, which is
    # sufficient for list.append.
    def run_job(job):
        r, entrant, path = job
        try:
            body = harness.forecast(entrant, r,
                                    history=hist_by_round.get(r["round_id"]),
                                    previous=read_forecast(path))
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
        with concurrent.futures.ThreadPoolExecutor(max_workers=FILING_WORKERS) as ex:
            written = sum(ex.map(run_job, jobs))
    return written, failures


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


def fetch_umich():
    """Michigan sentiment, preferring the survey's own table over FRED.

    FRED republishes this series a month late, which costs every entrant the
    most recent observation -- the one that matters most. The two agree exactly
    on all 674 overlapping months, so this is strictly more data, not different
    data. FRED remains the fallback because the official file is a plain CSV on
    a university web server and the arena should not go dark if it moves.
    """
    try:
        return umich.umich_sentiment()
    except Exception as e:                         # noqa: BLE001 - reported
        print(f"official Michigan table unavailable ({type(e).__name__}: {e}); "
              "falling back to FRED, which lags one month")
        return fredcsv.umich_sentiment()


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
        "failures": mb.get("failures"),
        "cutoffs": mb.get("cutoffs"),
    }


def attach_mock_models(bt):
    """Placeholder frontier-model rows, used only until a real backtest exists.

    Deterministic and flagged (mock: true / bt["mock_models"]) so the site can
    label them, but they are invented numbers: delete this function and its
    call the moment backtest/model_backtest.json is committed, and never let a
    figure derived from it reach the paper.
    """
    import hashlib as _h

    per_crps = next((e["mean_crps"] for e in bt["overall"]
                     if e["entrant"] == "persistence"), 1.7)
    mock_skill = {"gpt-5.5": 0.024, "claude-opus": 0.031, "gemini-pro": 0.012,
                  "grok": -0.008, "deepseek": 0.018, "qwen": -0.019}
    for key, board in bt["spans"].items():
        base_crps = next((e["mean_crps"] for e in board
                          if e["entrant"] == "persistence"), per_crps)
        n_rounds = board[0]["rounds"] if board else 0
        for mid, sk in mock_skill.items():
            hb = _h.sha256(f"{mid}:{key}".encode()).digest()
            spread = 0.05 if key == "last" else (0.02 if key == "d30" else 0.008)
            sk_i = round(sk + ((hb[0] / 255.0) - 0.5) * 2 * spread, 3)
            board.append({
                "entrant": mid, "rounds": n_rounds,
                "mean_crps": round(base_crps * (1 - sk_i), 3),
                "mean_skill": sk_i, "mock": True,
            })
        board.sort(key=lambda x: -x["mean_skill"])
    bt["overall"] = bt["spans"]["all"]

    n_ck = len(bt["trajectory"])
    for i, ck in enumerate(bt["trajectory"]):
        ramp = (i + 1) / n_ck
        for mid, sk in mock_skill.items():
            hb = _h.sha256(f"{mid}:{i}".encode()).digest()
            wob = ((hb[0] / 255.0) - 0.5) * 0.02 * (1.2 - ramp)
            val = round(sk * ramp + wob, 4)
            ck["skills"][mid] = val
            if "crps" in ck and "persistence" in ck["crps"]:
                ck["crps"][mid] = round(ck["crps"]["persistence"] * (1 - val), 3)
    bt["mock_models"] = sorted(mock_skill.keys())


def main():
    # Local runs read keys from .env; in CI they arrive as Actions secrets
    # and no .env exists, so already-set variables always win.
    envfile.load()
    now = now_utc()
    # One fetch per upstream file, shared by the series registry and by the
    # averages below, so the site's headline numbers and its series can never
    # be built from different snapshots of the same source.
    sources = {
        "sb_approval": silverbulletin.fetch(silverbulletin.APPROVAL_URL),
        "sb_generic": silverbulletin.fetch(silverbulletin.GENERIC_URL),
        "umich": series_registry.michigan_history(),
    }
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
    filed, filing_failures = file_baseline_forecasts(rounds, hist_by_round, now)
    count_forecasts(rounds)
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
        board = real_mb.get("board") or []
        if board:
            bt["baseline_replay"] = {"overall": bt["overall"],
                                     "spans": bt["spans"],
                                     "n_rounds": bt.get("n_rounds")}
            bt["overall"] = board
            bt["spans"] = {k: board for k in bt["spans"]}
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
            bt["note"] = (
                f"{real_mb.get('releases')} releases every entrant answered, "
                f"{real_mb.get('window', {}).get('first')} to "
                f"{real_mb.get('window', {}).get('last')}. Each model is scored "
                "only on releases after its own training cutoff; this table is "
                "the intersection, so every row is measured on the same points. "
                "Rows ending -zeroshot saw the question and no series history.")
    else:
        attach_mock_models(bt)

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
        "sources": {
            "silver_bulletin_approval": silverbulletin.APPROVAL_URL,
            "silver_bulletin_generic": silverbulletin.GENERIC_URL,
            "fred": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=UMCSENT",
            "repo": "https://github.com/Social-Atoms/social-sim-arena",
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
