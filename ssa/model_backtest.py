"""Walk-forward backtest of the LLM entrants, replayed through the live harness.

Same protocol as ssa/backtest.py -- for every release, forecast it from the
history strictly before it and score with CRPS -- except the forecasts come
from real API calls instead of closed-form baselines. Four properties are what
make the output citable rather than merely plausible:

- **Post-cutoff only.** A model that memorized a release is not forecasting it.
  Every entrant is scored only on releases after its training cutoff plus a
  margin (ssa/cutoffs.py), and the leaderboard window is the intersection of
  those, since a mean CRPS over a different release set is not a comparable
  number.

- **Every call is cached on disk**, keyed by the sha256 of (model id, exact
  prompt). Reruns cost nothing, an interrupted run resumes, and the cache is
  the audit trail: it holds the raw reply for every scored forecast, so the
  table can be regenerated from the repository without re-billing anyone.

- **Failures are recorded, never mocked.** The live harness falls back to a
  labeled placeholder when a provider call fails, which is right for keeping
  the arena's pages populated and wrong for a paper number. Here a failure is
  stored as a failure, excluded from scoring, and counted in the output.

- **Identical prompt to the live arena.** The prompt is built by
  harness.build_prompt, so a backtest forecast and a live forecast differ only
  in which releases they cover. If the live prompt changes, this changes with
  it, and the cache misses -- which is the correct behavior, not a bug.

Usage: tools/run_model_backtest.py (dry-run and cost estimate by default).
"""
import concurrent.futures
import hashlib
import json
import os
import threading
import time

from . import baselines, cutoffs, harness, scoring
from . import series as series_registry

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, "cache", "model_backtest")

# Match ssa/backtest.py so the LLM entrants and the baselines see the same
# warm-up rule and land on the same release set.
WARMUP = 8

# Rough list prices, USD per 1M tokens, for the dry-run estimate only. These
# drift, and at max reasoning effort the output side dominates by far -- the
# estimate answers "is this $2 or $200" before a run, nothing more.
PRICING = {
    "qwen-3.7": (1.60, 6.40),
    "qwen-3.8": (1.60, 6.40),
    "deepseek-pro": (0.55, 2.19),
    "deepseek-flash": (0.28, 0.42),
    "claude-opus-5": (5.00, 25.00),
    "gpt-5.6-luna": (1.25, 10.00),
    "gpt-5.6-sol": (1.25, 10.00),
    "gpt-5.6-terra": (1.25, 10.00),
    "claude-opus": (5.00, 25.00),
    "claude-sonnet": (3.00, 15.00),
    "claude-fable": (10.00, 50.00),
    "gemini-pro": (1.25, 10.00),
    "gemini-flash": (0.30, 2.50),
    "grok": (3.00, 15.00),
    "kimi": (1.00, 5.00),
    "glm": (0.60, 2.20),
    "minimax": (0.40, 2.00),
}

MAX_RETRIES = 3
RETRY_BACKOFF = 4.0  # seconds, multiplied by attempt number

_cache_lock = threading.Lock()


# --- pseudo-rounds ---------------------------------------------------------

def pseudo_round(series, target_date):
    """A round definition for a historical release, shaped like season0.json.

    Question, unit, methodology and cadence come from ssa/series.py, the same
    registry the live rounds read, so a backtest prompt and a live prompt differ
    only in which release they name. A series added to the registry is
    backtestable immediately, with no template to keep in sync here.
    """
    meta = series_registry.describe(series)
    return {
        "round_id": f"bt-{series}-{target_date}",
        "series": series,
        "question": meta["question"],
        "unit": meta["unit"],
        "methodology": meta["methodology"],
        "cadence": meta["cadence"],
        # Releases land mid-day UTC in the live season; only the date is used.
        "release_at": target_date + "T14:00:00Z",
    }


# --- cache -----------------------------------------------------------------

def cache_key(entrant, prompt):
    return hashlib.sha256(
        (harness.call_identity(entrant) + "\n" + prompt).encode("utf-8")).hexdigest()

def cache_path(entrant, prompt):
    return os.path.join(CACHE_DIR, entrant, cache_key(entrant, prompt) + ".json")


def cache_read(entrant, prompt):
    path = cache_path(entrant, prompt)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def cache_write(entrant, prompt, record):
    path = cache_path(entrant, prompt)
    with _cache_lock:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(record, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)


# --- planning --------------------------------------------------------------

def plan(series_map, entrants, start, warmup=WARMUP, end=None):
    """Every (entrant, series, release) call the backtest needs.

    `start` is the first release date to score. Pass a single ISO day to put
    every entrant on one window, or a {entrant: day} mapping to give each its
    own -- the mapping is what lets a recently trained model be scored at all,
    on the shorter stretch of history it could not have memorized, instead of
    dragging every other entrant's window down to meet it.

    Cross-entrant comparison then rests on skill against persistence computed
    on each entrant's own points, which travels across different release sets
    far better than a raw mean CRPS does. score() still reports the matched
    table for the strict comparison.

    Returns a list of task dicts; each carries the pre-target history slice so
    the prompt is built from exactly what the baselines will see.
    """
    starts = start if isinstance(start, dict) else {e: start for e in entrants}
    missing = [e for e in entrants if e not in starts]
    if missing:
        raise ValueError(f"no start date for: {', '.join(missing)}")
    tasks = []
    for series, history in sorted(series_map.items()):
        for i in range(warmup, len(history)):
            target = history[i]
            if target["date"] < min(starts.values()):
                continue
            if end and target["date"] > end:
                continue
            past = history[:i]
            r = pseudo_round(series, target["date"])
            for entrant in entrants:
                if target["date"] < starts[entrant]:
                    continue          # inside this model's training window
                # The condition is carried by the entrant id, exactly as in the
                # live arena, so the backtest scores both information
                # conditions rather than silently replaying only the default.
                _, variant = harness.resolve(entrant)
                prompt = harness.build_prompt(r, past, variant)
                tasks.append({
                    "entrant": entrant, "series": series,
                    "date": target["date"], "outcome": target["value"],
                    "round": r, "history": past, "prompt": prompt,
                    "cached": (cache_read(entrant, prompt) or {}).get("topline")
                              is not None,
                })
    return tasks


# Assumed output tokens per call. The visible answer is one small JSON object,
# so the number is almost entirely reasoning: a model at max effort can think
# for thousands of tokens before writing twenty. Costing every entrant at the
# no-reasoning figure understated a full run by more than an order of
# magnitude, which is the wrong direction for an estimate whose only job is to
# stop a surprise.
# Measured, not assumed. One live call to claude-fable-5 at effort=max on a
# real round prompt used 378 input and 454 output tokens, 435 of them thinking:
# the task is small enough that maximum effort still means a few hundred
# tokens of reasoning, not a few thousand. Guessing 6,000 overstated a full run
# by a factor of twelve, after an earlier guess of 120 understated it by
# twenty-four. Both errors came from estimating a quantity the API reports.
#
# cache_write records the real usage per call, so `python tools/run_model_backtest.py`
# reports what a completed run actually cost rather than what it was predicted
# to cost. These constants only price calls not yet made.
OUT_TOKENS_PLAIN = 150
OUT_TOKENS_REASONING = 500
IN_TOKENS = 400


def estimate_cost(tasks):
    """Rough USD for the uncached calls, split by entrant. Reasoning-heavy
    entrants are costed at a much larger output budget; see the constants."""
    per_entrant, total = {}, 0.0
    for t in tasks:
        if t["cached"]:
            continue
        model, _ = harness.resolve(t["entrant"])
        cin, cout = PRICING.get(model, (2.0, 10.0))
        params = harness.MODELS[model].get("params") or {}
        reasoning = ("reasoning_effort" in params
                     or "output_config" in params or "thinking" in params)
        out = OUT_TOKENS_REASONING if reasoning else OUT_TOKENS_PLAIN
        usd = (IN_TOKENS / 1e6) * cin + (out / 1e6) * cout
        per_entrant[t["entrant"]] = per_entrant.get(t["entrant"], 0.0) + usd
        total += usd
    return total, per_entrant


# --- execution -------------------------------------------------------------

def run_task(t, use_cache=True):
    """One forecast. Returns a record; never raises for provider errors."""
    if use_cache:
        hit = cache_read(t["entrant"], t["prompt"])
        # A cached *failure* is not a result. Timeouts and proxy errors are
        # transient, so serving them from cache would freeze a bad afternoon
        # into the record permanently and no re-run could ever repair it. The
        # record stays on disk for the audit trail; it just does not count as
        # an answer.
        if hit is not None and hit.get("topline") is not None:
            hit["from_cache"] = True
            return hit

    record = {
        "entrant": t["entrant"], "model": harness.model_id(t["entrant"]),
        "series": t["series"], "date": t["date"], "outcome": t["outcome"],
        "prompt_sha256": cache_key(t["entrant"], t["prompt"]),
        "topline": None, "error": None, "raw": None, "usage": None,
        "harness": "v1", "from_cache": False,
    }

    last = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            raw, usage = harness.call_provider(t["entrant"], t["prompt"],
                                               with_usage=True)
            record["raw"] = (raw or "")[:2000]
            record["usage"] = usage
            record["topline"] = harness.parse_forecast(raw)
            record["error"] = None
            break
        except Exception as e:                     # noqa: BLE001 - recorded, not raised
            last = f"{type(e).__name__}: {e}"
            record["error"] = last
            # A malformed reply will not fix itself on retry; a transport or
            # rate-limit error usually will.
            if isinstance(e, (ValueError, KeyError)):
                break
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * attempt)

    if record["topline"] is not None or record["error"]:
        cache_write(t["entrant"], t["prompt"], record)
    return record


def replay(tasks):
    """(records, missing) from the cache alone -- no provider, ever.

    The cache is the audit trail, so any change to how a reply is *scored*
    should regenerate the published table from the repo rather than re-billing
    the run that produced it. This is the code path that makes that true: it
    reads and never writes, and cannot call out even if a key is present.

    Tasks with no cached reply are counted, not invented. A count above zero
    means the plan and the cache disagree -- usually a changed prompt, since
    the cache is keyed on it -- and the resulting table covers fewer releases
    than the run did.
    """
    records, missing = [], 0
    for t in tasks:
        hit = cache_read(t["entrant"], t["prompt"])
        if hit is None:
            missing += 1
            continue
        hit = dict(hit)
        hit["from_cache"] = True
        records.append(hit)
    return records, missing


def execute(tasks, workers=4, use_cache=True, progress=None):
    """Run every task, cached ones for free. Results come back sorted, so the
    output does not depend on completion order."""
    results = [None] * len(tasks)
    done = [0]

    def work(idx):
        results[idx] = run_task(tasks[idx], use_cache=use_cache)
        done[0] += 1
        if progress and done[0] % progress == 0:
            print(f"  {done[0]}/{len(tasks)} calls", flush=True)

    if workers <= 1:
        for i in range(len(tasks)):
            work(i)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(work, range(len(tasks))))

    results.sort(key=lambda r: (r["series"], r["date"], r["entrant"]))
    return results


# --- scoring ---------------------------------------------------------------

def score(records, series_map, warmup=WARMUP):
    """CRPS per entrant, plus the baselines on the identical release set.

    Two tables come out:
      per_entrant - each entrant over every release it answered
      matched     - every entrant over the releases *all* of them answered

    The paper should cite `matched`. A model that failed on the ten hardest
    weeks would otherwise post a better mean than one that answered them.
    """
    ok = [r for r in records if r.get("topline")]
    keys = sorted({(r["series"], r["date"]) for r in ok})
    entrants = sorted({r["entrant"] for r in records})

    # baselines on the same (series, date) points the models were asked about
    base_crps, outcome_of = {}, {}
    for series, date in keys:
        history = series_map[series]
        idx = next((i for i, p in enumerate(history) if p["date"] == date), None)
        if idx is None or idx < warmup:
            continue
        past, target = history[:idx], history[idx]
        outcome_of[(series, date)] = target["value"]
        fcs = baselines.all_baselines(past, date)
        base_crps[(series, date)] = {
            name: scoring.crps_forecast(f, target["value"]) for name, f in fcs.items()
        }

    crps = {}
    for r in ok:
        k = (r["series"], r["date"])
        if k not in base_crps:
            continue
        crps.setdefault(r["entrant"], {})[k] = scoring.crps_forecast(
            r["topline"], outcome_of[k])

    answered_all = sorted(set.intersection(
        *[set(v) for v in crps.values()])) if crps else []

    def mean_over(values, points):
        return sum(values[k] for k in points) / len(points)

    def table(points_by_entrant):
        """points_by_entrant: {entrant: [(series, date), ...]}. Baselines are
        scored over the union of those points, so every row in one table is
        measured against the same persistence denominator it is compared to."""
        rows, pool = [], set()
        for e in entrants:
            pts = points_by_entrant.get(e) or []
            if not pts:
                continue
            pool.update(pts)
            mc = mean_over(crps[e], pts)
            mp = mean_over({k: base_crps[k]["persistence"] for k in pts}, pts)
            rows.append({"entrant": e, "rounds": len(pts),
                         "mean_crps": round(mc, 3),
                         "mean_skill": round(scoring.skill(mc, mp), 3)})
        pool = sorted(pool)
        if pool:
            denom = mean_over({k: base_crps[k]["persistence"] for k in pool}, pool)
            for name in baselines.DEFAULT:
                mc = mean_over({k: base_crps[k][name] for k in pool}, pool)
                rows.append({"entrant": name, "rounds": len(pool),
                             "mean_crps": round(mc, 3),
                             "mean_skill": round(scoring.skill(mc, denom), 3),
                             "baseline": True})
        rows.sort(key=lambda x: -x["mean_skill"])
        return rows

    per_entrant = table({e: sorted(pts) for e, pts in crps.items()})
    matched = table({e: answered_all for e in crps})

    failures = {}
    for r in records:
        if not r.get("topline"):
            failures[r["entrant"]] = failures.get(r["entrant"], 0) + 1

    # Cumulative skill over time, and a board per series. The site draws both,
    # and until now drew them only for the baselines: the placeholder path used
    # to inject model curves and per-series rows, so removing it left the
    # charts silently baseline-only. Real numbers have to fill the same shapes,
    # not just a new key.
    trajectory = _trajectory(crps, base_crps, answered_all)
    per_series = _per_series(crps, base_crps)
    # The same curve restricted to one tracker, so each tab can show how the
    # models did on *that* series rather than only the overall average. Built
    # over every release of the series rather than the matched intersection:
    # intersecting first leaves three or four points per tracker, which is a
    # table, not a curve. Entrants therefore cover different spans within a
    # tab -- the same relaxation `_per_series` already makes, and the reason
    # both are labelled per-series rather than matched.
    per_series_trajectory = {
        s: _trajectory(crps, base_crps, [k for k in base_crps if k[0] == s])
        for s in sorted({k[0] for k in base_crps})
    }

    return {
        "window": {"first": keys[0][1] if keys else None,
                   "last": keys[-1][1] if keys else None},
        "releases": len(keys),
        "matched_releases": len(answered_all),
        "per_entrant": per_entrant,
        "matched": matched,
        "trajectory": trajectory,
        "per_series": per_series,
        "per_series_trajectory": per_series_trajectory,
        "entrants": sorted(crps),
        "failures": failures,
        "cutoffs": {e: cutoffs.describe(e) for e in entrants},
    }


def _trajectory(crps, base_crps, points, checkpoints=30):
    """Cumulative mean skill per entrant at evenly spaced checkpoints.

    Computed on the matched points only, so every curve is over the same
    releases and the lines are comparable to each other at every x.
    """
    pts = sorted(points, key=lambda k: k[1])
    if len(pts) < 2:
        return []
    step = max(len(pts) / checkpoints, 1)
    out = []
    for c in range(1, min(checkpoints, len(pts)) + 1):
        upto = pts[: max(int(round(c * step)), 1)]
        skills, crpss = {}, {}
        denom = sum(base_crps[k]["persistence"] for k in upto) / len(upto)
        for e, per_point in crps.items():
            have = [k for k in upto if k in per_point]
            if not have:
                continue
            mc = sum(per_point[k] for k in have) / len(have)
            crpss[e] = round(mc, 3)
            skills[e] = round(scoring.skill(mc, denom), 4)
        for name in baselines.DEFAULT:
            mc = sum(base_crps[k][name] for k in upto) / len(upto)
            crpss[name] = round(mc, 3)
            skills[name] = round(scoring.skill(mc, denom), 4)
        ranks = {e: i + 1 for i, e in enumerate(
            sorted(skills, key=lambda x: -skills[x]))}
        out.append({"date": upto[-1][1], "n": len(upto),
                    "ranks": ranks, "skills": skills, "crps": crpss})
    return out


def _per_series(crps, base_crps):
    """One board per series, so a tracker that is easy or hard shows up as
    itself rather than being averaged away in the overall table."""
    series_names = sorted({k[0] for k in base_crps})
    out = {}
    for s in series_names:
        pts = [k for k in base_crps if k[0] == s]
        if not pts:
            continue
        denom = sum(base_crps[k]["persistence"] for k in pts) / len(pts)
        rows = []
        for e, per_point in sorted(crps.items()):
            have = [k for k in pts if k in per_point]
            if not have:
                continue
            mc = sum(per_point[k] for k in have) / len(have)
            rows.append({"entrant": e, "rounds": len(have),
                         "mean_crps": round(mc, 3),
                         "mean_skill": round(scoring.skill(mc, denom), 3)})
        for name in baselines.DEFAULT:
            mc = sum(base_crps[k][name] for k in pts) / len(pts)
            rows.append({"entrant": name, "rounds": len(pts),
                         "mean_crps": round(mc, 3),
                         "mean_skill": round(scoring.skill(mc, denom), 3),
                         "baseline": True})
        rows.sort(key=lambda x: -x["mean_skill"])
        out[s] = rows
    return out


def actual_cost(records):
    """What a finished run really cost, from the usage each provider reported.

    Returns (total_usd, per_entrant, tokens_missing). Providers that report no
    usage are counted in `tokens_missing` rather than silently priced at zero.
    """
    per, total, missing = {}, 0.0, 0
    for r in records:
        u = r.get("usage") or {}
        ino, out = u.get("input_tokens"), u.get("output_tokens")
        if ino is None or out is None:
            missing += 1
            continue
        model, _ = harness.resolve(r["entrant"])
        cin, cout = PRICING.get(model, (2.0, 10.0))
        usd = ino / 1e6 * cin + out / 1e6 * cout
        per[r["entrant"]] = per.get(r["entrant"], 0.0) + usd
        total += usd
    return total, per, missing


RUNS_DIR = os.path.join(ROOT, "backtest", "runs")


def export_run(stamp, records=None):
    """Consolidate the per-call cache into one committable JSONL.

    The working cache is one file per call, which is right for resumability and
    wrong for a repository: 2,868 files make a clone slow and a diff useless.
    This writes the same content as one sorted, line-per-call file, which is
    what belongs in git.

    That file is the reproducibility mechanism, not a convenience. The models
    run at their providers' default temperature, so re-running does not
    reproduce; the only way a reader regenerates the published table is from
    the replies as they were actually returned.
    """
    if records is None:
        records = []
        for entrant in sorted(os.listdir(CACHE_DIR)):
            d = os.path.join(CACHE_DIR, entrant)
            if not os.path.isdir(d):
                continue
            for name in sorted(os.listdir(d)):
                if not name.endswith(".json"):
                    continue
                with open(os.path.join(d, name)) as f:
                    records.append(json.load(f))
    records.sort(key=lambda r: (r.get("entrant", ""), r.get("series", ""),
                                r.get("date", "")))
    os.makedirs(RUNS_DIR, exist_ok=True)
    path = os.path.join(RUNS_DIR, f"{stamp}.jsonl")
    with open(path, "w") as f:
        for r in records:
            r = dict(r)
            r.pop("from_cache", None)
            f.write(json.dumps(r, sort_keys=True) + "\n")
    return path, len(records)
