"""Walk-forward backtest of the baseline models on real historical series.

For every release in a series (after a warm-up of 8 points), each model
forecasts that release using only the history strictly before it, and is
scored with CRPS against the value that actually came out. This is the same
protocol the live arena runs, replayed over the past, and it fills the
leaderboard with real numbers before the first live round resolves.

Series used (all fetched live by the refresh pipeline):
- umich_sentiment: FRED monthly, from 2015
- yougov_approval, mc_approval, yougov_generic_margin: poll waves via VoteHub

Deterministic: no randomness anywhere (the crowd mixture uses fixed
quantile-level samples).
"""
from . import baselines, scoring

WARMUP = 8
MODELS = ["persistence", "trend", "ewma", "climatology", "crowd"]


def run_series(history, since=None):
    """history: [{date, value}] oldest first. Returns per-release records."""
    records = []
    for i in range(WARMUP, len(history)):
        target = history[i]
        if since and target["date"] < since:
            continue
        past = history[:i]
        fcs = baselines.all_baselines(past, target["date"])
        fcs["crowd"] = {"pool_of": [v for k, v in fcs.items()]}
        row = {"date": target["date"], "outcome": target["value"], "crps": {}}
        for name in MODELS:
            if name == "crowd":
                xs = scoring.pool_samples([{"topline": t} for t in
                                           (fcs[m] for m in MODELS if m != "crowd")])
                row["crps"][name] = scoring.crps_samples(xs, target["value"])
            else:
                row["crps"][name] = scoring.crps_forecast(fcs[name], target["value"])
        records.append(row)
    return records


def run(series_map):
    """series_map: {series_name: history}. Returns leaderboard + trajectory."""
    per_series = {}
    all_records = []
    for name, hist in series_map.items():
        since = "2015-01-01" if name == "umich_sentiment" else None
        recs = run_series(hist, since=since)
        per_series[name] = summarize(recs)
        for r in recs:
            r["series"] = name
        all_records.extend(recs)

    all_records.sort(key=lambda r: r["date"])
    overall = summarize(all_records)
    trajectory = rank_trajectory(all_records)
    return {
        "note": "walk-forward replay on real historical series; each forecast uses only data before its release",
        "n_rounds": len(all_records),
        "overall": overall,
        "per_series": per_series,
        "trajectory": trajectory,
    }


def summarize(records):
    out = []
    if not records:
        return out
    for m in MODELS:
        crps = [r["crps"][m] for r in records if m in r["crps"]]
        per = [r["crps"]["persistence"] for r in records if m in r["crps"]]
        mean_crps = sum(crps) / len(crps)
        mean_per = sum(per) / len(per)
        out.append({
            "entrant": m,
            "rounds": len(crps),
            "mean_crps": round(mean_crps, 3),
            "mean_skill": round(scoring.skill(mean_crps, mean_per), 3),
        })
    out.sort(key=lambda x: -x["mean_skill"])
    return out


def rank_trajectory(records, checkpoints=30):
    """Cumulative ranking of models at evenly spaced checkpoints, for the
    rank-over-time (bump) chart."""
    if len(records) < checkpoints:
        return []
    step = len(records) / checkpoints
    traj = []
    for c in range(1, checkpoints + 1):
        upto = records[: int(round(c * step))]
        board = summarize(upto)
        ranks = {e["entrant"]: i + 1 for i, e in enumerate(board)}
        skills = {e["entrant"]: e["mean_skill"] for e in board}
        crpss = {e["entrant"]: e["mean_crps"] for e in board}
        traj.append({"date": upto[-1]["date"], "n": len(upto),
                     "ranks": ranks, "skills": skills, "crps": crpss})
    return traj
