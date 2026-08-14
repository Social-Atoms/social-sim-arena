"""Is the persona panel eight people per cell, or one person asked eight times?

The `persona` condition puts a real survey instrument to 192 synthetic
respondents -- 24 quota cells x 8 replicates -- and aggregates their answers the
way a pollster would. The 8 replicates are the whole reason the arm costs what
it costs: `personas.panel()` says they "exist because one person per cell is not
a poll", and `personas.resolution()` prices the difference at 10.71 points of
discretisation error for k=1 against 3.79 for k=8. Every persona forecast filed
so far reports the k=8 number in its `sd`.

**Nobody has checked that the eight replicates ever disagree.** They are eight
distinct prompts -- gender, region and income vary across them by construction
-- but if the model reads only party, age and education and answers the same way
regardless, then the cell's eight answers are one answer written down eight
times. In that case:

  - the effective panel is 24 respondents, not 192;
  - the honest discretisation error is 10.71 points, not 3.79, so every persona
    forecast is filed roughly 2.8x too confident, and CRPS punishes exactly that;
  - the arm bills 8x for a number that 24 calls would have produced.

That is a three-way failure -- a wrong published uncertainty, a wasted budget,
and a claim in the paper about panel resolution that the data does not support
-- and it is settled by one cheap run. This script is that run.

It reports three things:

  1. **Effective sample size per cell.** Distinct answers among each cell's 8
     replicates; the intra-cell design effect; the corrected `panel_se` next to
     the one the harness currently publishes; and -- the direct test -- the
     aggregate recomputed from every k=1, k=2 and k=4 sub-panel, so the question
     "would 24 calls have produced the same number as 192?" is answered by
     arithmetic rather than by a variance model.

  2. **Bias against real ground truth.** The committed Civiqs snapshots under
     `civiqs/` carry both the national tracker and its party=Republican filter
     as the dashboard showed them at the round's lock, so the simulated topline
     and the simulated Republican cell are both checkable. The Economist/YouGov
     crosstab workbook adds 16 more cells for the nearest wave -- but its cells
     are marginals across five axes and the panel is a three-axis cross, so only
     some of them genuinely correspond. Which ones, and why the rest do not, is
     printed rather than glossed.

  3. **Answer entropy.** The weighted share of each answer option, overall and
     per cell. Low variance has two very different causes: a panel where every
     Republican strongly approves and every Democrat strongly disapproves has
     learned a caricature of partisanship, while a panel that gives one answer
     everywhere has learned nothing at all. Within-cell and between-cell entropy
     separate them.

Dry-run by default, like tools/run_model_backtest.py: it prints the plan and the
cost and contacts nobody. Only --execute bills, only --execute touches the
network, and a failed call is recorded as a failure -- this script never files a
mock, because a fabricated respondent would defeat the point of counting them.

    python tools/diagnose_persona.py                     # plan + cost only
    python tools/diagnose_persona.py --execute           # the one billing path
    python tools/diagnose_persona.py --rescore           # rebuild from cache, free

Raw replies land in cache/persona_diagnostic/ (gitignored, one file per call,
keyed by sha256 of (call identity, prompt)) so the numbers below are auditable
and a rerun costs nothing.
"""
import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import sys
import threading
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import estimate_arms                                          # noqa: E402
from ssa import envfile, harness, personas                    # noqa: E402
from ssa import series as series_registry                     # noqa: E402
from ssa.adapters import civiqs as civiqs_adapter             # noqa: E402
from ssa.adapters import yougov_xtab                          # noqa: E402

CACHE = os.path.join(ROOT, "cache", "persona_diagnostic")
SEASON = os.path.join(ROOT, "questions", "season0.json")

# Same figures tools/estimate_arms.py prices the persona arm with: a persona
# call is one short instrument and a one-word JSON answer.
PERSONA_TOKENS = estimate_arms.PERSONA_TOKENS

# The workbook is keyless and free, but the host is api-test.yougov.com and the
# adapter's own docstring says to treat it as fragile. Cached on first fetch.
XTAB_CACHE = os.path.join(CACHE, "yougov_donald-trump-approval.xlsx")

_lock = threading.Lock()


# --- what each aggregator makes of one respondent ---------------------------
#
# The aggregate is linear in a per-respondent score, and the design effect has
# to be measured on that score rather than on "did they say approve", or a net
# would be diagnosed on the wrong quantity.

def _score_indicator(positive):
    return lambda ans, spec: float(ans.get("approval") in positive)


def _score_signed(plus, minus, key):
    def fn(ans, spec):
        v = ans.get(key)
        return 1.0 if v in plus else (-1.0 if v in minus else 0.0)
    return fn


def _score_ics(ans, spec):
    fav = {"better", "good"}
    unfav = {"worse", "bad"}
    out = 0.0
    for item in spec["items"]:
        v = ans.get(item["key"])
        out += 1.0 if v in fav else (-1.0 if v in unfav else 0.0)
    return out


SCORES = {
    "approve_share": _score_indicator({"approve"}),
    "approve_share_4pt": _score_indicator({"strongly approve", "somewhat approve"}),
    "strong_share": _score_indicator({"strongly approve"}),
    "weak_share": _score_indicator({"somewhat approve"}),
    "net_approve_share": _score_signed({"approve"}, {"disapprove"}, "approval"),
    "party_margin": _score_signed({"Democrat"}, {"Republican"}, "vote"),
    "umich_ics": _score_ics,
}


# --- picking the model and the round ----------------------------------------

def cheapest_model_with_key():
    """(model key, $/call, table of candidates). Cheapest is per persona call.

    Priced on the persona arm's own token profile, not on a list price: a model
    can be cheap on input and dear on output, and a persona call is nearly all
    input.
    """
    rows = []
    for m in harness.active_models():
        if not harness.has_key(m):
            continue
        rows.append((estimate_arms.price(m, PERSONA_TOKENS, 1), m))
    if not rows:
        sys.exit("no API key present for any active model; nothing to run")
    rows.sort()
    return rows[0][1], rows[0][0], rows


def load_rounds():
    with open(SEASON) as f:
        return json.load(f)["rounds"]


def civiqs_cfg(series_id):
    return (series_registry.SERIES.get(series_id) or {}).get("civiqs")


def candidate_rounds(rounds):
    """[(round, has_survey, has_snapshot, why)] -- what part 2 can be run on.

    A round qualifies when its series has an instrument (or the persona arm has
    nothing honest to ask) and when ground truth for its lock date is already in
    the repository (or part 2 has nothing to check against).
    """
    out = []
    for r in rounds:
        try:
            spec = series_registry.survey(r["series"])
        except KeyError:
            spec = None
        cfg = civiqs_cfg(r["series"])
        snap = None
        if cfg:
            key = civiqs_adapter.archive_key(cfg["name"], cfg.get("filters"))
            snap = bool(civiqs_adapter.archive_days(key))
        out.append((r, spec is not None, bool(snap)))
    return out


def pick_round(rounds):
    """The first round that can answer all three questions, or a hard error."""
    for r, has_survey, has_snap in candidate_rounds(rounds):
        if has_survey and has_snap:
            return r
    sys.exit("no round has both a survey instrument and a committed Civiqs "
             "snapshot; part 2 would have nothing to check against")


# --- ground truth ------------------------------------------------------------

def civiqs_truth(name, filters, day):
    """What the dashboard showed on `day`, out of the committed archive.

    The archive's own rule (`civiqs.as_displayed`): the freshest reading
    available on day d, according to the earliest snapshot taken on or after d.
    Read from disk, never from the network -- the point of the archive is that
    the number cannot change under a resolution.
    """
    key = civiqs_adapter.archive_key(name, filters)
    days = civiqs_adapter.archive_days(key)
    if not days:
        return None
    want = date.fromisoformat(day)
    after = [d for d in days if d >= want]
    pick = after[0] if after else days[-1]
    snap = civiqs_adapter.read_archive(key, pick)
    if not snap:
        return None
    cols = {c: civiqs_adapter.snapshot_series(snap, c) for c in snap["choices"]}
    end = snap.get("end_date") or max(next(iter(cols.values())))
    reading = min(end, day)
    vals = {c: v.get(reading) for c, v in cols.items()}
    if any(v is None for v in vals.values()):
        return None
    app = vals.get("Approve")
    dis = vals.get("Disapprove")
    return {
        "snapshot_day": pick.isoformat(),
        "snapshot_end_date": end,
        "reading_date": reading,
        "shares": vals,
        "approve": app,
        "disapprove": dis,
        "net": round(app - dis, 2),
        "url": snap.get("url"),
    }


def xtab_wave(day, fetch=True):
    """The Economist/YouGov crosstab wave nearest `day`, or None.

    Cached as raw workbook bytes on first fetch; a rerun reads the file and
    touches nothing.
    """
    blob = None
    if os.path.exists(XTAB_CACHE):
        with open(XTAB_CACHE, "rb") as f:
            blob = f.read()
    elif fetch:
        try:
            blob = yougov_xtab.fetch()
        except Exception as e:                            # noqa: BLE001
            return {"error": f"{type(e).__name__}: {e}"}
        os.makedirs(CACHE, exist_ok=True)
        with open(XTAB_CACHE, "wb") as f:
            f.write(blob)
    if blob is None:
        return None
    try:
        waves = yougov_xtab.parse(blob)
    except Exception as e:                                # noqa: BLE001
        return {"error": f"parse failed: {type(e).__name__}: {e}"}
    if not waves:
        return {"error": "workbook parsed to zero waves"}
    best = min(waves, key=lambda w: abs(
        (date.fromisoformat(w["date"]) - date.fromisoformat(day)).days))
    return {"wave": best}


# Which of the crosstab's 16 cells the persona panel can actually be cut to.
# The panel is a three-axis cross (party x age x education); the workbook is
# five sets of marginals. Correspondence is the exception, not the rule, and the
# reason is recorded per cell so nothing is quietly compared that should not be.
XTAB_MATCH = {
    "Democrat":     {"party": "Democrat"},
    "Independent":  {"party": "independent"},
    "Republican":   {"party": "Republican"},
    "Under 30":     {"age": "18-29"},
    "65+":          {"age": "65+"},
}

XTAB_UNMATCHED = {
    "30-44": "panel age bucket is 30-49; 45-49 falls on the other side of the "
             "boundary, so the two are different populations",
    "45-64": "panel age bucket is 50-64; 45-49 is missing from it entirely",
    "White": "race is not an axis of the panel -- the quota cross is party x "
             "age x education, and no persona is assigned a race",
    "Black": "race is not an axis of the panel",
    "Hispanic": "race is not an axis of the panel",
    "Male": "gender is assigned by position (i % 2), not crossed: every cell "
            "gets 4 men and 4 women, so the panel's gender marginal is 50/50 "
            "by construction rather than the population's, and it is "
            "independent of party by construction too",
    "Female": "same as Male: positional texture, not a quota axis",
    "HS or less": "the panel's education axis is binary; 'no college degree' "
                  "covers HS-or-less and some-college together and cannot be "
                  "split",
    "Some college": "folded into the panel's 'no college degree' bucket",
    "College grad": "the panel's 'college graduate' bucket is BA+, which "
                    "includes postgraduates; the workbook reports them apart",
    "Postgrad": "folded into the panel's 'college graduate' bucket",
}

# Pooled cells that do correspond once the workbook's finer buckets are added
# back up, weighted by their published bases. Not part of the 16.
XTAB_POOLED = {
    "no college degree": ["HS or less", "Some college"],
    "college graduate": ["College grad", "Postgrad"],
}


# --- the calls ---------------------------------------------------------------

def cache_key(entrant, prompt):
    return hashlib.sha256(
        (harness.call_identity(entrant) + "\n" + prompt).encode("utf-8")).hexdigest()


def cache_path(entrant, prompt):
    return os.path.join(CACHE, entrant, cache_key(entrant, prompt) + ".json")


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
    with _lock:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(record, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)


def ask_panel(entrant, panel, prompts, spec, workers, execute):
    """{pid: record} for the whole panel. Cache first, provider only if allowed.

    A failed or unparseable reply is stored as a failure and kept out of every
    aggregate below. Mocking one would put an invented respondent into a count
    of how many respondents are real, which is the one thing this script must
    not do.
    """
    records, made = {}, 0
    todo = []
    for p in panel:
        got = cache_read(entrant, prompts[p["id"]])
        if got and got.get("parsed"):
            records[p["id"]] = got
        else:
            todo.append(p)

    if todo and not execute:
        return records, 0, todo

    def ask(p):
        nonlocal made
        pid = p["id"]
        rec = {"entrant": entrant, "model": harness.model_id(entrant),
               "persona": {k: p[k] for k in ("id", "cell", "party", "age",
                                             "education", "gender", "region",
                                             "income")},
               "prompt_sha256": cache_key(entrant, prompts[pid]),
               "raw": None, "parsed": None, "usage": None, "error": None}
        try:
            text, usage = harness.call_provider(entrant, prompts[pid],
                                                with_usage=True,
                                                variant="persona")
            rec["raw"] = text
            rec["usage"] = usage
            rec["parsed"] = harness.parse_survey_reply(text, spec)
        except Exception as e:                            # noqa: BLE001
            rec["error"] = f"{type(e).__name__}: {e}"
        cache_write(entrant, prompts[pid], rec)
        with _lock:
            records[pid] = rec
            made += 1

    if todo:
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(len(todo), workers)) as ex:
            list(ex.map(ask, todo))
    return records, made, []


# --- part 1: how many respondents are actually in a cell ---------------------

def cell_table(panel, answers, spec, aggregate):
    """Per cell: the replicate answers, how many are distinct, the score mean."""
    score = SCORES[aggregate]
    cells = {}
    for p in panel:
        a = answers.get(p["id"])
        c = cells.setdefault(p["cell"], {
            "cell": p["cell"], "party": p["party"], "age": p["age"],
            "education": p["education"], "answers": [], "missing": 0})
        if a is None:
            c["missing"] += 1
            continue
        c["answers"].append(a)
        c.setdefault("scores", []).append(score(a, spec))
    for c in cells.values():
        keys = [json.dumps(a, sort_keys=True) for a in c["answers"]]
        counts = {}
        for k in keys:
            counts[k] = counts.get(k, 0) + 1
        c["n"] = len(keys)
        c["distinct"] = len(counts)
        c["modal_share"] = (max(counts.values()) / len(keys)) if keys else 0.0
        c["counts"] = {json.loads(k)[spec["items"][0]["key"]] if
                       len(spec["items"]) == 1 else k: v
                       for k, v in counts.items()}
        s = c.get("scores") or []
        c["score_mean"] = sum(s) / len(s) if s else None
    return [cells[k] for k in sorted(cells)]


def icc(cells):
    """Intraclass correlation of the per-respondent score, cells as groups.

    The ANOVA estimator, so it is the number a survey statistician would put in
    deff = 1 + (m-1)rho. Two caveats travel with it and are printed alongside:

    - It is an *upper* bound on the collapse. Some of the between-cell variance
      is real -- Republicans and Democrats genuinely differ -- and a quota
      design with fixed weights does not pay a clustering penalty for that.
    - The unambiguous half of the diagnostic is the within-cell mean square. If
      MSW is exactly zero, every cell is unanimous, rho is 1 by construction and
      no interpretation is required: the eight replicates are one answer.
    """
    groups = [c["scores"] for c in cells if c.get("scores")]
    if not groups:
        return None
    m = sum(len(g) for g in groups) / len(groups)
    grand = sum(sum(g) for g in groups) / sum(len(g) for g in groups)
    ssb = sum(len(g) * (sum(g) / len(g) - grand) ** 2 for g in groups)
    ssw = sum(sum((x - sum(g) / len(g)) ** 2 for x in g) for g in groups)
    dfb = len(groups) - 1
    dfw = sum(len(g) - 1 for g in groups)
    if dfb <= 0 or dfw <= 0:
        return None
    msb, msw = ssb / dfb, ssw / dfw
    denom = msb + (m - 1) * msw
    if denom <= 0:
        return {"rho": 0.0, "msb": msb, "msw": msw, "m": m}
    rho = (msb - msw) / denom
    return {"rho": rho, "msb": msb, "msw": msw, "m": m}


def realized_se(cells, panel, population, k):
    """Standard error of the weighted aggregate from the *observed* within-cell
    spread, in the aggregate's own unit.

    `personas.panel_se` is a worst case: it assumes every respondent is an
    independent coin flip (p=0.5) and reports the same number whatever the panel
    actually said. This is the same arithmetic run on what the panel did say --
    Var of the weighted mean = sum over cells of w^2 * s^2 / k, with s^2 the
    within-cell sample variance of the per-respondent score. Two ways it differs
    from the published figure, both material:

    - a cell that answered unanimously contributes zero, so a panel that agrees
      with itself looks *more* precise here than the worst case, which is
      exactly the trap: agreement without accuracy is not precision, it is bias,
      and the ground-truth section is what separates them;
    - the score for a net runs over [-1, +1] rather than [0, 1], so a net's
      per-respondent variance is up to four times a share's. The harness applies
      no `se_scale` on the Civiqs series, so its published sd is a share's error
      attached to a net's target.
    """
    tot = 0.0
    wsum = sum(sum(p["weight"] * (p["registered"]
                                  if (population or "A").upper() != "A" else 1.0)
                   for p in panel if p["cell"] == c["cell"])
               for c in cells)
    for c in cells:
        s = c.get("scores") or []
        if len(s) < 2:
            continue
        mean = sum(s) / len(s)
        var = sum((x - mean) ** 2 for x in s) / (len(s) - 1)
        w = sum(p["weight"] * (p["registered"]
                               if (population or "A").upper() != "A" else 1.0)
                for p in panel if p["cell"] == c["cell"]) / wsum
        tot += w * w * var / k
    return 100.0 * math.sqrt(tot)


def unanimity_test(cells, party, rates, reps):
    """Is a party's block of unanimous cells consistent with the real rates?

    A unanimous cell is not automatically a failure -- a cell that really does
    answer 97-2-1 will come out unanimous most of the time in eight draws. So
    the test is against the published subgroup distribution: under independent
    sampling the chance that eight draws all land on one option is the sum of
    the option shares to the eighth power, and the chance that *every* cell in
    the party does so is that to the number of cells.

    Returns None when there is no ground truth for the party, because guessing
    the rates would turn the test into an assumption.
    """
    block = [c for c in cells if c["party"] == party]
    if not block or not rates:
        return None
    tot = sum(rates.values())
    if tot <= 0:
        return None
    p_unan = sum((v / tot) ** reps for v in rates.values())
    obs = sum(1 for c in block if c["distinct"] == 1)
    n = len(block)
    # P(at least obs of n cells unanimous), binomial.
    tail = sum(math.comb(n, i) * p_unan ** i * (1 - p_unan) ** (n - i)
               for i in range(obs, n + 1))
    return {"party": party, "cells": n, "unanimous": obs,
            "p_unanimous_per_cell": p_unan, "expected": n * p_unan,
            "p_value": tail, "rates": rates}


def subpanel_groups(k, reps):
    """Contiguous blocks of replicate indices: [0..k-1], [k..2k-1], ...

    Contiguous rather than strided on purpose. Gender cycles with period 2 and
    region with period 4, so a stride-2 sub-panel would be all men or all women
    and the spread would measure the gender split rather than the replicate
    spread. A k=1 sub-panel is still single-gender -- unavoidable at one
    respondent per cell -- and that is noted where the numbers are printed.
    """
    if reps % k:
        return []
    return [[g * k + j for j in range(k)] for g in range(reps // k)]


def weights_of(people, population):
    """{pid: weight} over a subset, unnormalised.

    Every aggregator in `personas` divides by the weight it actually saw, so an
    unnormalised subset weight is the same estimator restricted to that subset
    -- which is what a sub-panel and a subgroup both are.
    """
    rv = (population or "A").upper() != "A"
    return {p["id"]: p["weight"] * (p["registered"] if rv else 1.0)
            for p in people}


def subpanel_spread(panel, answers, spec, aggregate, population, reps):
    """[(k, n sub-panels, [estimates], spread)] -- the direct test of k.

    If the replicates carry independent information the spread of the k=1
    estimates should be about sqrt(8) times the spread at k=8 and the whole
    curve should fall as one over the root of k. If they are copies, every
    sub-panel returns the same number and 168 of the 192 calls bought nothing.
    """
    out = []
    for k in sorted({1, 2, 4, reps}):
        groups = subpanel_groups(k, reps)
        if not groups:
            continue
        ests = []
        for g in groups:
            sub = [p for p in panel if int(p["id"][1:]) % reps in g]
            got = {p["id"]: answers[p["id"]] for p in sub if p["id"] in answers}
            if not got:
                continue
            try:
                ests.append(personas.aggregate(aggregate, got,
                                               weights_of(sub, population)))
            except ValueError:
                continue
        if not ests:
            continue
        mean = sum(ests) / len(ests)
        sd = (sum((e - mean) ** 2 for e in ests) / (len(ests) - 1)) ** 0.5 \
            if len(ests) > 1 else 0.0
        out.append({"k": k, "panels": len(ests),
                    "estimates": [round(e, 3) for e in ests],
                    "mean": mean, "sd": sd,
                    "range": max(ests) - min(ests)})
    return out


# --- part 3: entropy ---------------------------------------------------------

def entropy(shares):
    tot = sum(shares.values())
    if tot <= 0:
        return 0.0
    h = 0.0
    for v in shares.values():
        p = v / tot
        if p > 0:
            h -= p * math.log2(p)
    return h


def option_shares(panel, answers, spec, population, key, subset=None):
    """{option: weighted percent} over a subset of the panel."""
    people = [p for p in panel if subset is None or subset(p)]
    w = weights_of(people, population)
    got, tot = {}, 0.0
    for p in people:
        a = answers.get(p["id"])
        if not a or a.get(key) is None:
            continue
        got[a[key]] = got.get(a[key], 0.0) + w[p["id"]]
        tot += w[p["id"]]
    if tot <= 0:
        return {}
    return {o: 100.0 * v / tot for o, v in got.items()}


# --- reporting ---------------------------------------------------------------

def fmt_usd(x):
    return f"${x:.4f}" if x < 0.01 else f"${x:.3f}"


def main():
    ap = argparse.ArgumentParser(
        description="Diagnose whether the persona panel's replicates are real.")
    ap.add_argument("--execute", action="store_true",
                    help="actually call the provider (default: plan only)")
    ap.add_argument("--rescore", action="store_true",
                    help="rebuild the report from cached replies; bills nothing")
    ap.add_argument("--round", help="round_id override")
    ap.add_argument("--model", help="model key override (default: the cheapest "
                                    "one with a key present)")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--max-usd", type=float, default=0.50,
                    help="refuse to run if the estimate exceeds this")
    ap.add_argument("--no-xtab", action="store_true",
                    help="skip the Economist/YouGov crosstab (keyless, free, "
                         "but a live fetch from a fragile host)")
    ap.add_argument("--out", default=os.path.join(CACHE, "report.json"))
    args = ap.parse_args()

    envfile.load()

    # --- what we are about to do -------------------------------------------
    if args.model:
        if args.model not in harness.MODELS:
            sys.exit(f"unknown model: {args.model}")
        model = args.model
        per_call = estimate_arms.price(model, PERSONA_TOKENS, 1)
        why = "chosen with --model"
        table = [(per_call, model)]
    else:
        model, per_call, table = cheapest_model_with_key()
        why = ("cheapest active model with a key present, priced on the persona "
               "arm's own token profile")
    entrant = model + harness.VARIANT_SUFFIX["persona"]

    rounds = load_rounds()
    if args.round:
        r = next((x for x in rounds if x["round_id"] == args.round), None)
        if r is None:
            sys.exit(f"unknown round: {args.round}")
    else:
        r = pick_round(rounds)

    spec = series_registry.survey(r["series"])
    if spec is None:
        sys.exit(f"series {r['series']!r} has no survey instrument; the persona "
                 "arm cannot run on it and neither can this diagnostic")
    population = spec.get("population", "A")
    aggregate = spec["aggregate"]
    if aggregate not in SCORES:
        sys.exit(f"no per-respondent score defined for aggregate {aggregate!r}")

    reps = personas.REPLICATES
    panel = personas.panel()
    prompts = {p["id"]: harness.build_persona_prompt(p, spec) for p in panel}
    cached = sum(1 for p in panel if (cache_read(entrant, prompts[p["id"]])
                                      or {}).get("parsed"))
    todo = len(panel) - cached
    est = estimate_arms.price(model, PERSONA_TOKENS, todo)

    print("model")
    print(f"  {model}  ({harness.MODELS[model]['name']}, "
          f"{harness.model_id(entrant)})")
    print(f"  {why}")
    for usd, m in table[:5]:
        mark = "  <- selected" if m == model else ""
        print(f"    {m:16s} {fmt_usd(usd * len(panel)):>10s} / 192 calls{mark}")
    if len(table) > 5:
        print(f"    ... {len(table) - 5} more with keys, all dearer")

    print("\nround")
    print(f"  {r['round_id']}  series={r['series']}  lock={r['lock_at']}")
    cfg = civiqs_cfg(r["series"])
    print(f"  instrument: {len(spec['items'])} item(s), "
          f"{len(spec['items'][0]['options'])} options, population "
          f"{population}, aggregate {aggregate}")
    if cfg:
        print(f"  ground truth: committed Civiqs snapshots for "
              f"{cfg['name']} (national and party=Republican)")

    print("\npanel")
    print(f"  {personas.CELLS} cells x {reps} replicates = {len(panel)} calls")
    print(f"  claimed discretisation error at k={reps}: "
          f"{personas.panel_se(personas.weights_for(population, reps)):.2f} points")
    print(f"  ... at k=1:                              "
          f"{personas.panel_se(personas.weights_for(population, 1)):.2f} points")

    print(f"\ncalls {len(panel)} total, {cached} cached, {todo} to make")
    print(f"estimated cost {fmt_usd(est)}" + (" (fully cached)" if not todo else ""))
    print(f"  at {PERSONA_TOKENS[0]} input / {PERSONA_TOKENS[1]} output tokens "
          f"per call, list prices from tools/estimate_arms.py")

    if todo and est > args.max_usd:
        sys.exit(f"\nestimate {fmt_usd(est)} exceeds --max-usd "
                 f"{fmt_usd(args.max_usd)}; refusing to run")

    if not args.execute and not args.rescore:
        print("\ndry run -- nothing called, nothing fetched. Add --execute to "
              "run, or --rescore to rebuild from the cache for free.")
        return

    if args.execute and not harness.has_key(entrant):
        sys.exit(f"\nno {harness.MODELS[model]['env']} in the environment")

    print(f"\nrunning {todo} calls with {args.workers} workers..."
          if (todo and args.execute) else "\nreading the cache...")
    records, made, pending = ask_panel(entrant, panel, prompts, spec,
                                       args.workers, args.execute)
    if pending:
        print(f"\n{len(pending)} calls are not cached and --rescore makes none. "
              "Run --execute first.")
        if not records:
            sys.exit("nothing cached at all; there is nothing to report on.")

    answers = {pid: rec["parsed"] for pid, rec in records.items()
               if rec.get("parsed")}
    failures = {pid: rec["error"] for pid, rec in records.items()
                if not rec.get("parsed")}

    spent = 0.0
    cin, cout = estimate_arms.PRICING.get(model, (2.0, 10.0))
    for rec in records.values():
        u = rec.get("usage") or {}
        if u.get("input_tokens"):
            spent += u["input_tokens"] / 1e6 * cin
        if u.get("output_tokens"):
            spent += u["output_tokens"] / 1e6 * cout

    print(f"\n{len(answers)}/{len(panel)} respondents answered"
          + (f", {len(failures)} failed" if failures else ""))
    if failures:
        for pid in sorted(failures)[:5]:
            print(f"  {pid}: {failures[pid][:160]}")
        if len(failures) > 5:
            print(f"  ... and {len(failures) - 5} more")
    if made:
        print(f"measured cost of the {made} calls just made: "
              f"{fmt_usd(spent * made / max(len(records), 1))} "
              f"(whole panel, from provider token reports: {fmt_usd(spent)})")

    if not answers:
        sys.exit("no usable answers; nothing to diagnose.")

    item_key = spec["items"][0]["key"]
    options = spec["items"][0]["options"]
    report = {"round": r["round_id"], "series": r["series"], "entrant": entrant,
              "model": harness.model_id(entrant), "population": population,
              "aggregate": aggregate, "replicates": reps,
              "responded": len(answers), "failures": failures,
              "measured_cost_usd": round(spent, 5)}

    # ---------------------------------------------------------------- part 1
    print("\n" + "=" * 74)
    print("1. EFFECTIVE SAMPLE SIZE PER CELL")
    print("=" * 74)
    cells = cell_table(panel, answers, spec, aggregate)
    print(f"\n{'cell':5s} {'party':12s} {'age':6s} {'education':18s} "
          f"{'n':>2s} {'dist':>4s} {'modal':>6s}  answers")
    for c in cells:
        brief = ", ".join(f"{k}x{v}" for k, v in
                          sorted(c["counts"].items(), key=lambda x: -x[1]))
        print(f"{c['cell']:5s} {c['party']:12s} {c['age']:6s} "
              f"{c['education']:18s} {c['n']:2d} {c['distinct']:4d} "
              f"{c['modal_share']:6.2f}  {brief[:34]}")

    dist = {}
    for c in cells:
        dist[c["distinct"]] = dist.get(c["distinct"], 0) + 1
    print("\ndistinct answers per cell, across the "
          f"{len(cells)} cells (max possible {min(reps, len(options))}):")
    for d in sorted(dist):
        print(f"  {d} distinct: {dist[d]:2d} cells "
              f"{'#' * dist[d]}")
    unanimous = dist.get(1, 0)
    mean_distinct = sum(c["distinct"] for c in cells) / len(cells)
    mean_modal = sum(c["modal_share"] for c in cells) / len(cells)
    print(f"  unanimous cells: {unanimous}/{len(cells)}"
          f"   mean distinct: {mean_distinct:.2f}"
          f"   mean modal agreement: {mean_modal:.3f}")

    ic = icc(cells)
    se8 = personas.panel_se(personas.weights_for(population, reps))
    se1 = personas.panel_se(personas.weights_for(population, 1))
    if ic:
        rho = max(0.0, min(1.0, ic["rho"]))
        deff = 1 + (ic["m"] - 1) * rho
        k_eff = ic["m"] / deff
        corrected = se8 * math.sqrt(deff)
        print(f"\nintra-cell design effect (ANOVA ICC on the per-respondent "
              f"score for {aggregate})")
        print(f"  within-cell mean square  MSW = {ic['msw']:.4f}")
        print(f"  between-cell mean square MSB = {ic['msb']:.4f}")
        print(f"  rho = {ic['rho']:.4f}   deff = 1 + (k-1)rho = {deff:.2f}")
        print(f"  nominal k = {reps:.0f}   effective k = {k_eff:.2f}")
        print(f"\n  panel_se as personas.resolution() claims it (k={reps}): "
              f"{se8:.2f} points")
        print(f"  panel_se corrected for the observed agreement:      "
              f"{corrected:.2f} points")
        print(f"  panel_se the harness would report at k=1:           "
              f"{se1:.2f} points")
        if ic["msw"] == 0:
            print("\n  MSW is exactly zero: every cell is unanimous, so this "
                  "needs no interpretation.\n  The eight replicates are one "
                  "answer written eight times and k_eff = 1 exactly.")
        else:
            print("\n  Caveat: rho from the ANOVA counts real between-cell "
                  "difference as clustering,\n  so deff here is an upper bound. "
                  "The unambiguous half is MSW and the distinct\n  count above.")
        report["icc"] = {"rho": ic["rho"], "msw": ic["msw"], "msb": ic["msb"],
                         "deff": deff, "k_eff": k_eff,
                         "panel_se_claimed": se8, "panel_se_corrected": corrected,
                         "panel_se_k1": se1}

    rse8 = realized_se(cells, panel, population, reps)
    rse1 = realized_se(cells, panel, population, 1)
    print(f"\nthe same error computed from what the panel actually said, not "
          f"from p=0.5")
    print(f"  realized se at k={reps}: {rse8:6.2f} points   "
          f"at k=1: {rse1:6.2f} points")
    print(f"  ({sum(1 for c in cells if c['distinct'] == 1)} unanimous cells "
          f"contribute exactly zero to this, which is why the ground-truth\n"
          f"   section below is the half that matters: agreement is not "
          f"accuracy.)")
    if aggregate.startswith("net") or aggregate.endswith("margin"):
        print(f"  note: the target is a net, so a respondent's score runs over "
              f"[-1,+1] and its variance\n  is up to 4x a share's. The series "
              f"carries no se_scale, so the published "
              f"{se8:.2f}\n  is a share's error attached to a net's target -- "
              f"a second, independent understatement.")
    report["realized_se"] = {"k8": rse8, "k1": rse1}

    spreads = subpanel_spread(panel, answers, spec, aggregate, population, reps)
    print(f"\nthe direct test -- the same aggregate from sub-panels of size k")
    print(f"  {'k':>2s} {'panel':>6s} {'sub-panels':>11s} {'estimate(s)':>34s} "
          f"{'sd':>7s} {'range':>7s}")
    for s in spreads:
        ests = ", ".join(f"{e:.2f}" for e in s["estimates"][:6])
        if len(s["estimates"]) > 6:
            ests += ", ..."
        print(f"  {s['k']:2d} {s['k'] * len(cells):6d} {s['panels']:11d} "
              f"{ests:>34s} {s['sd']:7.3f} {s['range']:7.3f}")
    print("  (a k=1 sub-panel is single-gender by the panel's construction -- "
          "gender cycles\n   with period 2 -- so any spread at k=1 mixes "
          "replicate noise with a gender split.)")
    report["cells"] = cells
    report["subpanels"] = spreads

    # ---------------------------------------------------------------- part 2
    print("\n" + "=" * 74)
    print("2. BIAS AGAINST REAL GROUND TRUTH")
    print("=" * 74)
    lock_day = r["lock_at"][:10]
    bias = {}
    party_rates = {}
    if cfg:
        nat = civiqs_truth(cfg["name"], cfg.get("filters"), lock_day)
        rep = civiqs_truth(cfg["name"], dict(cfg.get("filters") or {},
                                             party="Republican"), lock_day)
        w_all = weights_of(panel, population)
        sim_net = personas.aggregate(aggregate, answers, w_all)
        sim_app = personas.approve_share(answers, w_all, item=item_key)

        def party_subset(name):
            people = [p for p in panel if p["party"] == name]
            got = {p["id"]: answers[p["id"]] for p in people
                   if p["id"] in answers}
            w = weights_of(people, population)
            return got, w

        print(f"\nCiviqs, as the dashboard read at the round's lock "
              f"({lock_day})")
        for label, truth, subset in (("topline (all RV)", nat, None),
                                     ("Republicans", rep, "Republican")):
            if not truth:
                print(f"  {label:18s} no snapshot")
                continue
            if subset is None:
                got, w = answers, w_all
            else:
                got, w = party_subset(subset)
            if not got:
                print(f"  {label:18s} no simulated respondents")
                continue
            s_net = personas.net_approve_share(got, w, item=item_key)
            s_app = personas.approve_share(got, w, item=item_key)
            print(f"  {label}")
            print(f"    snapshot {truth['snapshot_day']} "
                  f"(end_date {truth['snapshot_end_date']}), "
                  f"reading for {truth['reading_date']}")
            print(f"    {'':14s} {'real':>8s} {'simulated':>10s} {'error':>8s}")
            print(f"    {'net approval':14s} {truth['net']:8.1f} "
                  f"{s_net:10.1f} {s_net - truth['net']:+8.1f}")
            print(f"    {'% approve':14s} {truth['approve']:8.1f} "
                  f"{s_app:10.1f} {s_app - truth['approve']:+8.1f}")
            bias[label] = {"real_net": truth["net"], "sim_net": round(s_net, 2),
                           "real_approve": truth["approve"],
                           "sim_approve": round(s_app, 2),
                           "snapshot": truth["snapshot_day"],
                           "reading_date": truth["reading_date"]}
            if subset == "Republican":
                # Same tracker, same instrument, same three options the panel
                # was offered -- the closest ground truth a cell can have.
                party_rates["Republican"] = dict(truth["shares"])
        report["civiqs_bias"] = bias
        report["sim_topline"] = {"net": round(sim_net, 2),
                                 "approve": round(sim_app, 2)}

    # the crosstab, cell by cell, with the correspondence spelled out
    if args.no_xtab:
        print("\nEconomist/YouGov crosstab: skipped (--no-xtab)")
    else:
        got = xtab_wave(lock_day, fetch=args.execute)
        if got is None:
            print("\nEconomist/YouGov crosstab: not cached and --rescore makes "
                  "no fetches")
        elif got.get("error"):
            print(f"\nEconomist/YouGov crosstab unavailable: {got['error']}")
        else:
            w = got["wave"]
            print(f"\nEconomist/YouGov crosstab, wave {w['date']} "
                  f"(nearest to the {lock_day} lock), % approve among "
                  f"registered voters")
            print("  a different house from Civiqs, so a level gap between the "
                  "two is a house effect,\n  not simulation error; the "
                  "comparison worth reading is cell-to-cell shape.")
            print(f"\n  {'cell':16s} {'real':>6s} {'sim':>7s} {'error':>7s}  "
                  f"basis")
            rows = {}
            print(f"  -- comparable ({len(XTAB_MATCH)} of 16) "
                  + "-" * 28)
            for cell, sel in XTAB_MATCH.items():
                key_, val = next(iter(sel.items()))
                people = [p for p in panel if p[key_] == val]
                sub = {p["id"]: answers[p["id"]] for p in people
                       if p["id"] in answers}
                if not sub:
                    continue
                sim = personas.approve_share(sub, weights_of(people, population),
                                             item=item_key)
                real = w["cells"][cell]["approve"]
                rows[cell] = {"real": real, "sim": round(sim, 2),
                              "error": round(sim - real, 2), "basis": sel}
                print(f"  {cell:16s} {real:6.1f} {sim:7.1f} {sim - real:+7.1f}  "
                      f"panel {key_}={val}")
            print(f"  -- pooled, not one of the 16 " + "-" * 32)
            for label, parts in XTAB_POOLED.items():
                base = sum(w["cells"][c]["base"] for c in parts)
                real = sum(w["cells"][c]["approve"] * w["cells"][c]["base"]
                           for c in parts) / base
                people = [p for p in panel if p["education"] == label]
                sub = {p["id"]: answers[p["id"]] for p in people
                       if p["id"] in answers}
                if not sub:
                    continue
                sim = personas.approve_share(sub, weights_of(people, population),
                                             item=item_key)
                rows[label] = {"real": round(real, 2), "sim": round(sim, 2),
                               "error": round(sim - real, 2),
                               "basis": {"pooled": parts}}
                print(f"  {label:16s} {real:6.1f} {sim:7.1f} {sim - real:+7.1f}  "
                      f"= {' + '.join(parts)}, base-weighted")
            print(f"  -- not comparable ({len(XTAB_UNMATCHED)} of 16) "
                  + "-" * 24)
            for cell, reason in XTAB_UNMATCHED.items():
                print(f"  {cell:16s} {w['cells'][cell]['approve']:6.1f} "
                      f"{'--':>7s} {'--':>7s}  {reason}")
            tl = w["cells"][yougov_xtab.TOPLINE]
            sim_tl = personas.approve_share(answers, weights_of(panel, population),
                                            item=item_key)
            print(f"\n  {'TOPLINE (RV)':16s} {tl['approve']:6.1f} "
                  f"{sim_tl:7.1f} {sim_tl - tl['approve']:+7.1f}")
            report["xtab"] = {"wave": w["date"], "cells": rows,
                              "topline": {"real": tl["approve"],
                                          "sim": round(sim_tl, 2)},
                              "not_comparable": XTAB_UNMATCHED}
            for cell, party in (("Democrat", "Democrat"),
                                ("Independent", "independent")):
                party_rates.setdefault(party, {
                    k: w["cells"][cell][k]
                    for k in ("approve", "disapprove", "not_sure")})

    # Is the unanimity justified? A cell that really answers 97-2-1 will come
    # out unanimous most of the time; one that answers 80-11-8 will not.
    if party_rates:
        print("\nis a unanimous cell defensible? -- eight independent draws at "
              "the real subgroup rates")
        print(f"  {'party':12s} {'cells':>5s} {'unan':>5s} {'expected':>9s} "
              f"{'P(cell unan)':>12s} {'P(>=obs)':>10s}  real rates")
        tests = {}
        for party in personas.PARTY:
            t = unanimity_test(cells, party, party_rates.get(party), reps)
            if not t:
                print(f"  {party:12s} no ground truth for this cut; not tested")
                continue
            tests[party] = t
            tot = sum(t["rates"].values())
            rates = " / ".join(f"{k.split()[0].lower()[:7]} {100 * v / tot:.0f}"
                               for k, v in t["rates"].items())
            print(f"  {party:12s} {t['cells']:5d} {t['unanimous']:5d} "
                  f"{t['expected']:9.2f} {t['p_unanimous_per_cell']:12.3f} "
                  f"{t['p_value']:10.2e}  {rates}")
        print("  Republican rates are Civiqs's own party filter (same tracker, "
              "same three options).\n  Democrat and independent are the "
              "Economist/YouGov wave -- a different house, so read\n  those two "
              "rows as indicative rather than exact.")
        report["unanimity"] = tests

    # ---------------------------------------------------------------- part 3
    print("\n" + "=" * 74)
    print("3. ANSWER ENTROPY")
    print("=" * 74)
    overall = option_shares(panel, answers, spec, population, item_key)
    print(f"\nweighted share of each option, whole panel")
    for o in options:
        v = overall.get(o, 0.0)
        print(f"  {o:34s} {v:6.2f}%  {'#' * int(round(v / 2))}")
    h_all = entropy(overall)
    print(f"  entropy {h_all:.3f} bits of a possible "
          f"{math.log2(len(options)):.3f}")

    print(f"\nby party (weighted within party)")
    for party in personas.PARTY:
        sh = option_shares(panel, answers, spec, population, item_key,
                           subset=lambda p, q=party: p["party"] == q)
        line = "  ".join(f"{o[:12]}={sh.get(o, 0.0):5.1f}" for o in options)
        print(f"  {party:12s} {line}   H={entropy(sh):.3f}")

    per_cell_h = []
    for c in cells:
        sh = {k: float(v) for k, v in c["counts"].items()}
        per_cell_h.append(entropy(sh))
    mean_h = sum(per_cell_h) / len(per_cell_h) if per_cell_h else 0.0
    print(f"\nper-cell entropy (unweighted, over the {reps} replicates)")
    print(f"  mean {mean_h:.3f} bits, max {max(per_cell_h):.3f}, "
          f"min {min(per_cell_h):.3f}, "
          f"{sum(1 for h in per_cell_h if h == 0)}/{len(per_cell_h)} cells at 0")
    print(f"  {'cell':5s} {'party':12s} {'age':6s} {'education':18s} {'H':>6s}")
    for c, h in zip(cells, per_cell_h):
        print(f"  {c['cell']:5s} {c['party']:12s} {c['age']:6s} "
              f"{c['education']:18s} {h:6.3f}")
    print(f"\n  within-cell entropy {mean_h:.3f} vs whole-panel entropy "
          f"{h_all:.3f}:")
    print("  near-zero within and non-zero overall means the panel is "
          "deterministic per cell and\n  varies only between cells -- a "
          "caricature of the axes, not a population.")
    report["entropy"] = {"overall": overall, "overall_bits": h_all,
                         "per_cell_mean_bits": mean_h,
                         "per_cell_bits": per_cell_h,
                         "zero_entropy_cells": sum(1 for h in per_cell_h
                                                   if h == 0)}

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)
        f.write("\n")
    print(f"\nwrote {os.path.relpath(args.out, ROOT)}")


if __name__ == "__main__":
    main()
