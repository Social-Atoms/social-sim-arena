"""Consumer releases use explicit periods and first captured publisher vintages.

The same contract feeds refresh, submission validation and settlement. Raw files
and normalized histories stay in the private consumer archive; public evidence
contains hashes, source URLs, attribution and the scored outcome only.
"""
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import batches, consumer
from .adapters import consumer as adapters

SOURCE_KEYS = {
    "sce": "consumer_sce_extensions",
    "michigan_current": "consumer_michigan_inflation",
    "michigan_finals": "consumer_michigan_inflation",
    "mint": "consumer_mint", "nyt": "consumer_nyt", "steam": "consumer_steam",
}
SERIES_SOURCE = {
    "sce_spending_growth": "sce", "sce_stock_prices_rise": "sce",
    "sce_unemployment_rise": "sce",
    "umich_inflation_1y_preliminary": "michigan_current",
    "umich_inflation_1y_final": "michigan_finals",
    "mint_weekly_net_sales": "mint", "nyt_bestsellers": "nyt",
    "steam_weekly_top_sellers": "steam",
}
ATTRIBUTION = {
    "sce": (
        "Source: Survey of Consumer Expectations, \u00a9 2013-2026 Federal Reserve Bank "
        "of New York (FRBNY). The SCE data are available without charge at "
        "http://www.newyorkfed.org/microeconomics/sce and may be used subject to "
        "license terms posted there. FRBNY disclaims any responsibility for this "
        "analysis and interpretation of Survey of Consumer Expectations data."),
    "mint": "Source: United States Mint.",
    "nyt": "Data provided by The New York Times.",
    "steam": "Data from Steam. This project is not endorsed by Valve.",
    "michigan_current": "Source: Surveys of Consumers. Copyright \u00a9 2026, The Regents of the University of Michigan. All Rights Reserved.",
    "michigan_finals": "Source: Surveys of Consumers. Copyright \u00a9 2026, The Regents of the University of Michigan. All Rights Reserved.",
}


def timestamp(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Consumer timestamps require an explicit timezone")
    return parsed.astimezone(timezone.utc)


def spec_for(r):
    spec = r.get("consumer")
    if not isinstance(spec, dict):
        raise ValueError("Consumer round requires a structured consumer contract")
    if not re.fullmatch(r"consumer-[a-z0-9-]{1,55}", r.get("round_id", "")):
        raise ValueError("Consumer round IDs must use lowercase letters, digits and hyphens, start with consumer-, and fit in 64 characters")
    source = spec.get("source")
    if source not in SOURCE_KEYS or SERIES_SOURCE.get(r.get("series")) != source:
        raise ValueError("Consumer source does not match the declared target")
    ref = adapters.iso_day(spec["reference_date"])
    release, lock = timestamp(r["release_at"]), timestamp(r["lock_at"])
    until = timestamp(spec["capture_until"])
    if not lock < release <= until or until - release > timedelta(days=7):
        raise ValueError("Invalid consumer lock, release or capture deadline")
    if not spec.get("population") or not spec.get("calendar_url"):
        raise ValueError("Consumer contract requires a population and calendar evidence URL")
    if not spec["calendar_url"].startswith("https://"):
        raise ValueError("Calendar evidence must be an HTTPS URL")
    if source in {"sce", "michigan_current", "michigan_finals"}:
        if ref.day != 1 or r.get("target_type") != "continuous_normal":
            raise ValueError("Monthly survey targets require a month label and a normal forecast")
        if release - lock < timedelta(hours=48):
            raise ValueError("Monthly consumer releases require at least 48 hours between lock and release")
    else:
        start = timestamp(spec["measurement_start"])
        end = timestamp(spec["measurement_end"])
        zone = ZoneInfo(spec.get("measurement_timezone", "UTC"))
        local_start, local_end = start.astimezone(zone), end.astimezone(zone)
        duration = local_end.replace(tzinfo=None) - local_start.replace(tzinfo=None)
        if not lock < start < end <= release or duration != timedelta(days=7):
            raise ValueError("Weekly target must lock before its complete seven-day measurement window")
        if local_end.date() - timedelta(days=1) != ref:
            raise ValueError("Reference date must be the measurement window's final included day")
        if source == "mint":
            if ref.weekday() != 6 or r.get("target_type") != "continuous_normal":
                raise ValueError("Mint targets require Sunday report dates and normal forecasts")
            if not re.fullmatch(r"[A-Za-z0-9_-]+", spec.get("product_id", "")):
                raise ValueError("Mint contract requires a product ID")
            product = spec.get("product", {})
            if not all(product.get(k) is True for k in (
                    "fixed_supply", "general_public_buyers", "batches_known", "not_sold_out")):
                raise ValueError("Mint product must pass fixed-supply, buyer, batch and sellout checks")
            limit = product.get("supply_limit")
            if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0 or not \
                    str(product.get("evidence_url", "")).startswith("https://"):
                raise ValueError("Mint product needs a positive supply limit and official product evidence")
        elif r.get("target_type") != "ranking_list":
            raise ValueError("Consumer charts require an ordered ranking forecast")
    if source == "nyt":
        if not re.fullmatch(r"[a-z0-9-]+", spec.get("list_name", "")):
            raise ValueError("NYT requires an exact list name")
        adapters.iso_day(spec["published_date"])
    if source == "steam" and not re.fullmatch(r"[A-Z]{2}", spec.get("country", "")):
        raise ValueError("Steam requires a fixed two-letter country")
    return spec


def source_for(r):
    return SOURCE_KEYS[spec_for(r)["source"]]


def vintages(source, root=None, before=None):
    root = consumer.DEFAULT_ARCHIVE if root is None else root
    entry = consumer.index(root)["sources"].get(source, {})
    rows = []
    for name in entry.get("vintages", []):
        parsed, manifest = consumer.read_vintage(root, name)
        if manifest["source"] != source:
            raise ValueError("Consumer archive index crosses source boundaries")
        captured = timestamp(manifest["captured_at"])
        if before is None or captured <= before:
            rows.append((captured, name, parsed, manifest))
    return sorted(rows, key=lambda row: (row[0], row[1]))


def _series_id(r, spec):
    source = spec["source"]
    if source.startswith("michigan_"):
        return "umich_inflation_1y"
    if source == "mint":
        return f"mint_{spec['product_id']}_cumulative"
    if source == "nyt":
        return "nyt_" + spec["list_name"]
    if source == "steam":
        return "steam_" + spec["country"]
    return r["series"]


def _points(r, spec, parsed):
    field = "lists" if spec["source"] in {"nyt", "steam"} else "series"
    points = parsed[field].get(_series_id(r, spec), [])
    if spec["source"] == "michigan_current":
        stages = parsed["metadata"]["release_types"]
        points = [p for p in points if stages.get(p["date"]) == "preliminary"]
    return points


def history(r, root=None, before=None):
    """Only bytes actually available before the entrants' shared input freeze."""
    spec = spec_for(r)
    before = before or batches.window_opens_at(r["lock_at"])
    by_date = {}
    for captured, _, parsed, manifest in vintages(spec["source"], root, before):
        if captured >= before:
            continue
        for point in _points(r, spec, parsed):
            if point["date"] >= spec["reference_date"]:
                continue
            by_date.setdefault(point["date"], dict(point, observed_at=manifest["captured_at"]))
    points = [by_date[day] for day in sorted(by_date)]
    # Use earlier final releases as prior-month context when a new preliminary
    # series has few retained prints. The current target's final is excluded.
    if spec['source'] == 'michigan_current':
        final_spec = dict(r, series='umich_inflation_1y_final',
                          consumer=dict(spec, source='michigan_finals'))
        finals = history(final_spec, root=root, before=before)
        combined = {p['date']: p for p in finals}
        combined.update({p['date']: p for p in points})
        points = [combined[day] for day in sorted(combined)]
    if spec["source"] == "mint":
        points = adapters.mint_series({spec["product_id"]: points})["series"].get(
            f"mint_{spec['product_id']}_weekly_net_sales", []) if points else []
    return points


def resolution(r, now=None, root=None):
    """Select the first in-window artifact for exactly the promised period."""
    spec = spec_for(r)
    now = now or datetime.now(timezone.utc)
    release, until = timestamp(r["release_at"]), timestamp(spec["capture_until"])
    if now < release:
        raise ValueError("Consumer release is not due")
    rows = vintages(spec["source"], root, min(now, until))
    if any(captured < release and any(p["date"] == spec["reference_date"]
           for p in _points(r, spec, parsed)) for captured, _, parsed, _ in rows):
        raise ValueError("The declared answer was already captured before release; correct the release contract")
    selected = None
    for captured, name, parsed, manifest in rows:
        if captured < release:
            continue
        if manifest.get("published_at") and not release <= timestamp(manifest["published_at"]) <= captured:
            continue
        match = next((p for p in _points(r, spec, parsed)
                      if p["date"] == spec["reference_date"]), None)
        if match is not None:
            selected = match, manifest, name
            break
    if selected is None:
        raise ValueError("No exact-period publisher vintage within the capture window; do not settle from stale data")
    point, manifest, name = selected
    out = {"series": r["series"], "observed_date": point["date"],
           "consumer_target": [spec["source"], spec["reference_date"],
                               spec.get("product_id"), spec.get("list_name"), spec.get("country")],
           "captured_at": manifest["captured_at"], "source_url": manifest["url"],
           "raw_sha256": manifest["raw_sha256"], "manifest_sha256": name.split("/")[-1][:-5],
           "attribution": ATTRIBUTION[spec["source"]],
           "resolved_at": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "method": "first captured publisher artifact for the declared period and release stage"}
    if spec["source"] in {"nyt", "steam"}:
        ranking = r["ranking"]
        if point["week_start"] != ranking["week_start"] or point["week_end"] != ranking["week_end"]:
            raise ValueError("Publisher chart does not match the declared measurement week")
        if spec["source"] == "nyt" and point["published_date"] != spec["published_date"]:
            raise ValueError("NYT returned a different publication date")
        out.update(items=point["items"][:ranking["length"]],
                   week_start=point["week_start"], week_end=point["week_end"])
    elif spec["source"] == "mint":
        previous = (adapters.iso_day(spec["reference_date"]) - timedelta(days=7)).isoformat()
        baseline = None
        for _, _, parsed, earlier in rows:
            match = next((p for p in _points(r, spec, parsed) if p["date"] == previous), None)
            if match is not None:
                baseline = match, earlier
                break
        if baseline is None:
            raise ValueError("Mint needs both adjacent official weekly reports; never fill a missing report")
        out.update(value=point["value"] - baseline[0]["value"],
                   previous_raw_sha256=baseline[1]["raw_sha256"], previous_report_date=previous)
    else:
        out["value"] = point["value"]
    return out


def ranking_observations(r, root=None, now=None):
    now = now or datetime.now(timezone.utc)
    points = history(r, root, before=min(now, batches.window_opens_at(r["lock_at"])))
    try:
        outcome = resolution(r, root=root, now=now)
    except ValueError:
        return points
    return points + [dict(outcome, date=outcome["observed_date"])]
