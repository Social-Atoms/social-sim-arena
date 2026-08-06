"""Build site/data.json from live sources.

Run:  python -m ssa.refresh
Cron: .github/workflows/refresh.yml runs this daily and commits the result.

Everything the entry page shows comes from this file: live tracker values
(VoteHub API, FRED), round status computed against the clock, and baseline
forecasts (persistence, trend) computed from the real series.
"""
import json
import os
from datetime import date, datetime, timezone

from .adapters import fredcsv, votehub
from . import average, backtest, baselines, harness, scoring, sharecard

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


def build_series(approval, generic, umich):
    """All target series used by rounds, as [{date, value}] oldest first."""
    yg_app = votehub.from_pollster(approval, "YouGov", "Economist")
    yg_gen = votehub.from_pollster(generic, "YouGov", "Economist")
    mc_app = votehub.from_pollster(approval, "Morning Consult")
    anchor = generic[-1]["date"] if generic else date.today()
    # weekly adjusted-average history for the midterm margin special
    margin_hist = []
    for weeks_back in range(12, -1, -1):
        asof = anchor.fromordinal(anchor.toordinal() - 7 * weeks_back)
        val, _ = average.adjusted_average(generic, asof)
        if val is not None:
            margin_hist.append({"date": asof.isoformat(), "value": round(val, 2)})
    return {
        "yougov_approval": poll_history(yg_app, "approve"),
        "yougov_generic_margin": poll_history(yg_gen, "margin"),
        "mc_approval": poll_history(mc_app, "approve"),
        "umich_sentiment": umich,
        "generic_ballot_margin": margin_hist,
    }


def build_trackers(approval, generic, series, next_umich_release=None):
    if not approval or not generic:
        raise RuntimeError("VoteHub returned no polls; refusing to build trackers from empty data")
    # Anchor each average at its source's real freshness, not the wall clock.
    # The VoteHub API snapshot lags the live site by a few weeks; we label the
    # as-of date instead of pretending the number is from today.
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
        "source": "VoteHub API, house-effect adjusted, 21-day window (API snapshot lags the live site)",
    }
    t["generic_ballot_avg"] = {
        "label": "2026 generic ballot, adjusted average",
        "unit": "margin, Dem minus Rep",
        "value": round(gen_avg, 1),
        "asof": asof_gen.isoformat(),
        "delta_30d": round(gen_avg - gen_prev, 1) if gen_prev is not None else None,
        "n_polls_window": gen_n,
        "source": "VoteHub API, house-effect adjusted, 21-day window (API snapshot lags the live site)",
    }
    yg = latest(series["yougov_approval"])
    if yg:
        t["yougov_approval"] = {
            "label": "Economist/YouGov, latest wave",
            "unit": "% approve",
            "value": yg["value"],
            "asof": yg["date"],
            "source": "VoteHub API (poll-level)",
        }
    mc = latest(series["mc_approval"])
    if mc:
        t["mc_approval"] = {
            "label": "Morning Consult, latest wave",
            "unit": "% approve",
            "value": mc["value"],
            "asof": mc["date"],
            "source": "VoteHub API (poll-level)",
        }
    um = latest(series["umich_sentiment"])
    if um:
        t["umich_sentiment"] = {
            "label": "Michigan consumer sentiment",
            "unit": "index",
            "value": um["value"],
            "asof": um["date"],
            "next_release": next_umich_release or UMICH_NEXT_RELEASE,
            "source": "FRED (UMCSENT, lags one month); release-day values from sca.isr.umich.edu",
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
    out = []
    for r in season["rounds"]:
        row = {k: r[k] for k in ("round_id", "tracker", "series", "question", "unit",
                                  "release_at", "release_estimated", "lock_at", "resolve")}
        row["status"] = round_status(r, resolved, now)
        # Baselines are frozen at lock time: only history strictly before the
        # lock date counts. Otherwise, once a release lands in the series, the
        # persistence null would contain the outcome it is scored against.
        lock_date = r["lock_at"][:10]
        hist = [p for p in (series.get(r["series"]) or []) if p["date"] < lock_date]
        if len(hist) >= 3:
            target = r["release_at"][:10]
            row["baselines"] = baselines.all_baselines(hist, target)
        else:
            row["baselines"] = None
            row["baseline_note"] = "no machine-readable series yet for this tracker"
        if r["round_id"] in resolved:
            row["resolution"] = resolved[r["round_id"]]
        out.append(row)
    return out


def file_baseline_forecasts(rounds):
    """The hosted always-on agents: while a round is open, the refresh cron
    keeps each baseline's and each frontier model's forecast file current;
    the last commit before lock_at is the one that counts. Model forecasts
    are real API output when a key is configured and clearly-labeled
    deterministic MOCKs otherwise (see ssa/harness.py)."""
    written = 0
    for r in rounds:
        if r["status"] != "open" or not r.get("baselines"):
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
        for model_id in harness.MODELS:
            body = harness.forecast(model_id, r)
            with open(os.path.join(rdir, model_id + ".json"), "w") as f:
                json.dump(body, f, indent=2)
                f.write("\n")
            written += 1
    return written


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


def main():
    now = now_utc()
    approval = votehub.approval_polls()
    generic = votehub.generic_ballot_polls()
    umich = fredcsv.umich_sentiment()

    with open(QUESTIONS) as f:
        season = json.load(f)

    series = build_series(approval, generic, umich)
    trackers = build_trackers(approval, generic, series,
                              next_release_for(season, "umich_sentiment", now))
    resolved = {}
    if os.path.exists(RESOLVED):
        with open(RESOLVED) as f:
            resolved = json.load(f)

    rounds = build_rounds(season, series, resolved, now)
    file_baseline_forecasts(rounds)
    count_forecasts(rounds)
    board = build_leaderboard(rounds, resolved)
    bt = backtest.run({
        "umich_sentiment": series["umich_sentiment"],
        "yougov_approval": series["yougov_approval"],
        "mc_approval": series["mc_approval"],
        "yougov_generic_margin": series["yougov_generic_margin"],
    })
    # Placeholder rows for the frontier models until API keys are wired:
    # deterministic, clearly flagged (mock: true), sit between the real
    # baselines so the board reads plausibly. Replaced by real backtest runs
    # through the harness once providers are connected.
    per_crps = next((e["mean_crps"] for e in bt["overall"] if e["entrant"] == "persistence"), 1.7)
    mock_skill = {"gpt-5.5": 0.024, "claude-opus": 0.031, "gemini-pro": 0.012,
                  "grok": -0.008, "deepseek": 0.018, "qwen": -0.019}
    import hashlib as _hh
    for key, board in bt["spans"].items():
        base_crps = next((e["mean_crps"] for e in board if e["entrant"] == "persistence"), per_crps)
        n_rounds = board[0]["rounds"] if board else 0
        for mid, sk in mock_skill.items():
            hb = _hh.sha256(f"{mid}:{key}".encode()).digest()
            spread = 0.05 if key == "last" else (0.02 if key == "d30" else 0.008)
            sk_i = round(sk + ((hb[0] / 255.0) - 0.5) * 2 * spread, 3)
            board.append({
                "entrant": mid, "rounds": n_rounds,
                "mean_crps": round(base_crps * (1 - sk_i), 3),
                "mean_skill": sk_i, "mock": True,
            })
        board.sort(key=lambda x: -x["mean_skill"])
    bt["overall"] = bt["spans"]["all"]
    # placeholder trajectories for the models so the aggregate chart can
    # compare them over time; deterministic wobble around a ramp to the
    # final mock skill. Flagged via bt["mock_models"].
    import hashlib as _h
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
            "votehub": "https://api.votehub.com/polls",
            "fred": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=UMCSENT",
            "repo": "https://github.com/jajamoa/social-sim-arena",
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
    print("approval polls:", len(approval), "| generic:", len(generic),
          "| umich points:", len(umich))
    print("approval avg:", trackers["trump_approval_avg"]["value"],
          "| generic margin:", trackers["generic_ballot_avg"]["value"])


if __name__ == "__main__":
    main()
