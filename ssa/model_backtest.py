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
    "gpt-5.6-luna": (1.25, 10.00),
    "gpt-5.6-sol": (1.25, 10.00),
    "gpt-5.6-terra": (1.25, 10.00),
    "claude-opus": (5.00, 25.00),
    "claude-sonnet": (3.00, 15.00),
    "claude-fable": (10.00, 50.00),
    "gemini-pro": (1.25, 10.00),
    "gemini-flash": (0.30, 2.50),
    "grok": (3.00, 15.00),
    "qwen": (1.60, 6.40),
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

    `start` is the first release date to score, normally cutoffs.common_start.
    Returns a list of task dicts; each carries the pre-target history slice so
    the prompt is built from exactly what the baselines will see.
    """
    tasks = []
    for series, history in sorted(series_map.items()):
        for i in range(warmup, len(history)):
            target = history[i]
            if target["date"] < start:
                continue
            if end and target["date"] > end:
                continue
            past = history[:i]
            r = pseudo_round(series, target["date"])
            for entrant in entrants:
                prompt = harness.build_prompt(r, past)
                tasks.append({
                    "entrant": entrant, "series": series,
                    "date": target["date"], "outcome": target["value"],
                    "round": r, "history": past, "prompt": prompt,
                    "cached": cache_read(entrant, prompt) is not None,
                })
    return tasks


def estimate_cost(tasks):
    """Rough USD for the uncached calls. Assumes ~700 in / ~120 out tokens,
    measured off the live prompts; thinking models will exceed the output side."""
    per_entrant, total = {}, 0.0
    for t in tasks:
        if t["cached"]:
            continue
        cin, cout = PRICING.get(t["entrant"], (2.0, 10.0))
        usd = (700 / 1e6) * cin + (120 / 1e6) * cout
        per_entrant[t["entrant"]] = per_entrant.get(t["entrant"], 0.0) + usd
        total += usd
    return total, per_entrant


# --- execution -------------------------------------------------------------

def run_task(t, use_cache=True):
    """One forecast. Returns a record; never raises for provider errors."""
    if use_cache:
        hit = cache_read(t["entrant"], t["prompt"])
        if hit is not None:
            hit["from_cache"] = True
            return hit

    record = {
        "entrant": t["entrant"], "model": harness.model_id(t["entrant"]),
        "series": t["series"], "date": t["date"], "outcome": t["outcome"],
        "prompt_sha256": cache_key(t["entrant"], t["prompt"]),
        "topline": None, "error": None, "raw": None,
        "harness": "v1", "from_cache": False,
    }

    last = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            raw = harness.call_provider(t["entrant"], t["prompt"])
            record["raw"] = (raw or "")[:2000]
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

    return {
        "window": {"first": keys[0][1] if keys else None,
                   "last": keys[-1][1] if keys else None},
        "releases": len(keys),
        "matched_releases": len(answered_all),
        "per_entrant": per_entrant,
        "matched": matched,
        "failures": failures,
        "cutoffs": {e: cutoffs.describe(e) for e in entrants},
    }
