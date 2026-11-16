"""Zillow Observed Rent Index (ZORI), metro file: manual download only, raw file private.

Card `zillow-zori-metro@v3` (docs/data-cards/zillow-zori-metro.json). Rights
were granted on 2026-11-15 for the manual route with three conditions, all
from Zillow's Terms of Use, and this module is built around them:

1. **No automated query of any Zillow service** (section 5). Nothing here, in
   any workflow, collector or test, fetches from a Zillow host. A person
   downloads `Metro_zori_uc_sfrcondomfr_sm_sa_month.csv` in a browser once a
   month and runs `tools/ingest_zori.py` on the saved file. There is no
   `capture` and no URL to call.
2. **The raw CSV is never published** (section 4.A). The ingest writes it only
   into the private consumer archive (`.local/consumer/raw/zillow_zori/`,
   gitignored and vercelignored), which `tools/sync_consumer_archive.py push`
   persists as age ciphertext on the `sealed` branch, exactly as the consumer
   pipeline does with its originals.
3. **Derived numbers carry "Data Provided by Zillow Group"** (section 4.C) on
   every page that shows them; the notice lives in ssa/source_notices.py.

The public record, under `sources/zillow_zori/`, is what may be shown: per
capture day, the raw file's sha256 and size, the newest month, and the derived
month-over-month changes for the frozen universe (`universe.json`, the 50
largest metros by SizeRank in the first ingested file, identified by
RegionID; the first ten are the profile). Levels are never published and
never compared across files: Zillow recomputes history every month, so a
change is always computed inside one file.

Rounds are not scheduled yet: the card's lock depends on Zillow's Market
Report dates, which a person copies from zillow.com/research/calendar, and the
universe is frozen only by the first real file.
"""
import csv
import io
import json
import math
import os
from datetime import date, datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NAME = "zillow_zori"
PUBLIC = os.path.join(ROOT, "sources", NAME)
CARD = "zillow-zori-metro@v3"
FILE_NAME = "Metro_zori_uc_sfrcondomfr_sm_sa_month.csv"
HEADER = ["RegionID", "SizeRank", "RegionName", "RegionType", "StateName"]
FIRST_MONTH = "2015-01"
PROFILE_N = 10
UNIVERSE_N = 50
CONTINUITY_PP = 0.05
HISTORY_MONTHS = 14        # profile changes kept public: persistence + rms12
ATTRIBUTION = "Data Provided by Zillow Group"


def month_end(month):
    y, m = int(month[:4]), int(month[5:7])
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def shift(month, n):
    y, m = int(month[:4]), int(month[5:7])
    k = y * 12 + (m - 1) + n
    return f"{k // 12:04d}-{k % 12 + 1:02d}"


def parse(body):
    """CSV bytes -> {"months": [...], "rows": {RegionID: {...values}}}. Fails loud.

    Checks (b): the header starts RegionID,SizeRank,RegionName,RegionType,
    StateName and continues with consecutive, ascending month-end dates from
    2015-01-31.
    """
    try:
        text = bytes(body).decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise ValueError(f"{NAME}: not UTF-8: {e}") from None
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise ValueError(f"{NAME}: empty file")
    head = rows[0]
    if head[:5] != HEADER:
        raise ValueError(f"{NAME}: header starts {head[:5]!r}, not {HEADER!r}")
    months = []
    want = FIRST_MONTH
    for col in head[5:]:
        if col != month_end(want).isoformat():
            raise ValueError(f"{NAME}: column {col!r} where {month_end(want)} was "
                             "expected; dates must be consecutive month ends from 2015-01-31")
        months.append(want)
        want = shift(want, 1)
    if not months:
        raise ValueError(f"{NAME}: no month columns")
    out = {}
    for n, r in enumerate(rows[1:], start=2):
        if not r:
            continue
        if len(r) != len(head):
            raise ValueError(f"{NAME}: line {n} has {len(r)} fields, header {len(head)}")
        rid = r[0].strip()
        if not rid.isdigit() or rid in out:
            raise ValueError(f"{NAME}: line {n} RegionID {rid!r} is not a new integer id")
        vals = {}
        for m, v in zip(months, r[5:]):
            v = v.strip()
            if v:
                try:
                    vals[m] = float(v)
                except ValueError:
                    raise ValueError(f"{NAME}: line {n} {m} value {v!r}") from None
        out[rid] = {"size_rank": int(r[1]), "name": r[2], "type": r[3],
                    "state": r[4], "values": vals}
    return {"months": months, "rows": out}


def change(row, month):
    """100 x (V_M / V_M-1 - 1) inside one file, or None."""
    a, b = row["values"].get(month), row["values"].get(shift(month, -1))
    if a is None or b is None or b <= 0 or a <= 0:
        return None
    return round(100.0 * (a / b - 1.0), 6)


def freeze_universe(parsed):
    """The 50 largest metros by SizeRank in this file; the first ten are the profile."""
    metros = sorted(((r["size_rank"], rid) for rid, r in parsed["rows"].items()
                     if r["type"].lower() == "msa"), key=lambda t: t[0])
    if len(metros) < UNIVERSE_N:
        raise ValueError(f"{NAME}: only {len(metros)} metro rows")
    top = [rid for _, rid in metros[:UNIVERSE_N]]
    return {"card": CARD, "frozen_from_newest_month": parsed["months"][-1],
            "profile": top[:PROFILE_N], "universe": top,
            "names": {rid: parsed["rows"][rid]["name"] for rid in top}}


def check(parsed, expected_month, universe, previous=None):
    """Checks (c) to (f). Returns the derived public record; raises naming the check."""
    newest = parsed["months"][-1]
    if newest != expected_month:
        raise ValueError(f"{NAME} check (c): newest column is {newest}, expected "
                         f"{expected_month}; archive unchanged")
    if not any(r["type"].lower() == "country" and r["name"] == "United States"
               for r in parsed["rows"].values()):
        raise ValueError(f"{NAME} check (d): no United States country row; archive unchanged")
    missing = [rid for rid in universe["universe"] if rid not in parsed["rows"]]
    if any(rid in universe["profile"] for rid in missing):
        raise ValueError(f"{NAME} check (d): profile metro(s) {missing} missing; archive unchanged")
    for rid in universe["profile"]:
        row = parsed["rows"][rid]
        for m in (newest, shift(newest, -1)):
            v = row["values"].get(m)
            if v is None or not v > 0:
                raise ValueError(f"{NAME} check (e): {row['name']} has no positive "
                                 f"value for {m}; archive unchanged")
    profile = {}
    for rid in universe["profile"]:
        row = parsed["rows"][rid]
        hist = {}
        for i in range(HISTORY_MONTHS):
            m = shift(newest, -i)
            c = change(row, m)
            if c is not None:
                hist[m] = c
        profile[rid] = hist
    if previous is not None:
        prev_month = previous["newest_month"]
        drift = {}
        for rid in universe["profile"]:
            old = previous["profile"].get(rid, {}).get(prev_month)
            new = profile[rid].get(prev_month)
            if old is None or new is None:
                continue
            if abs(new - old) > CONTINUITY_PP:
                drift[universe["names"][rid]] = (old, new)
        if drift:
            raise ValueError(f"{NAME} check (f): the {prev_month} change moved more "
                             f"than {CONTINUITY_PP} points against the previous file "
                             f"for {sorted(drift)}: wrong variant or a method "
                             "change; a maintainer looks; archive unchanged")
    ranking = {rid: change(parsed["rows"][rid], newest)
               for rid in universe["universe"] if rid in parsed["rows"]}
    return {"card": CARD, "newest_month": newest, "attribution": ATTRIBUTION,
            "profile": profile,
            "universe_change": {rid: v for rid, v in ranking.items() if v is not None},
            "universe_missing": missing}


def persistence_reference(record, rid):
    """Card Q1 zero point mean_k(k=1) with an rms12 spread, inside one file:
    mean = the newest change, sd = RMS of persistence's own error over the 12
    newest months the record holds."""
    hist = record["profile"][rid]
    months = sorted(hist)
    errs = [hist[months[i]] - hist[months[i - 1]] for i in range(1, len(months))][-12:]
    if len(errs) < 12:
        raise ValueError(f"{NAME}: {len(errs)} persistence errors, 12 needed")
    return {"mean": hist[months[-1]],
            "sd": round(math.sqrt(sum(e * e for e in errs) / len(errs)), 6)}
