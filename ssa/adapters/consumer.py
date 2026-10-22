"""Offline consumer-source parsers; network retrieval lives in the collection tool.

Only Michigan inflation, the NY Fed SCE extensions and US Mint (manual import)
are parsed here; parsers for NYT, Netflix, Steam and ACSI were removed while
their terms are unresolved. Output uses the arena's ``{date, value}`` records.
Metadata and vintage evidence never masquerade as values.
"""
import csv
import io
import math
import re
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from xml.etree import ElementTree as ET

from . import sce, umich


SCE_FIELDS = {
    "sce_spending_growth": ("HH Spending Change", "Median point prediction", "percent"),
    "sce_unemployment_rise": (
        "Unemployment Expectations",
        "Mean probability that the U.S. unemployment rate will be higher one year from now",
        "percent probability"),
    "sce_stock_prices_rise": (
        "Stock Prices", "Mean probability that U.S. stock prices will be higher one year from now",
        "percent probability"),
    "sce_income_growth": ("HH Income Change", "Median point prediction", "percent"),
    "sce_home_price_change": (
        "Home price expectations", "Median expected home price change", "percent"),
    "sce_debt_delinquency": (
        "Delinquency expectations",
        "Mean probability of missing minimum debt payment over the next three months",
        "percent probability"),
    "sce_earnings_growth": ("Earnings growth", "Median expected earnings growth", "percent"),
    "sce_job_loss": (
        "Job separation expectation", "Mean probability of losing a job",
        "percent probability"),
    "sce_job_finding": (
        "Job finding expectations", "Mean probability of finding a job in the next three months if one loses a job today",
        "percent probability"),
}


def iso_day(value):
    value = str(value)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"Expected an ISO calendar date, got {value!r}")
    return date.fromisoformat(value)


def number(value, low=None, high=None):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Non-finite values are not observations")
    if low is not None and result < low or high is not None and result > high:
        raise ValueError(f"Value {result} outside [{low}, {high}]")
    return result


def text_body(body):
    text = body.decode("utf-8-sig") if isinstance(body, bytes) else body
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Empty source body")
    if text.lstrip().startswith("<"):
        raise ValueError("Expected a data file, received HTML/XML")
    return text


def table(body, required, delimiter=","):
    reader = csv.DictReader(io.StringIO(text_body(body)), delimiter=delimiter)
    headers = reader.fieldnames or []
    if len(headers) != len(set(headers)) or not set(required) <= set(headers):
        raise ValueError(f"Missing/duplicate headers; required {sorted(required)}, got {headers}")
    rows = list(reader)
    if not rows or any(None in row for row in rows):
        raise ValueError("Empty or ragged table")
    return rows


def ordered(rows):
    if not rows:
        raise ValueError("No observations")
    rows = sorted(rows, key=lambda row: row["date"])
    if len({row["date"] for row in rows}) != len(rows):
        raise ValueError("Duplicate observation dates")
    for row in rows:
        iso_day(row["date"])
        number(row["value"])
    return rows


def result(series=None, lists=None, metadata=None):
    return {"series": series or {}, "lists": lists or {}, "metadata": metadata or {}}


def michigan_finals(body):
    rows = table(body, ("Month", "YYYY", "PX_MD", "PX5_MD"))
    out = {"umich_inflation_1y": [], "umich_inflation_5_10y": []}
    missing = defaultdict(list)
    for row in rows:
        month = umich.MONTHS.get(row["Month"].strip())
        if month is None:
            raise ValueError(f"Unrecognized month {row['Month']!r}")
        day = f"{int(row['YYYY']):04d}-{month:02d}-01"
        iso_day(day)
        for sid, col in zip(out, ("PX_MD", "PX5_MD")):
            if row[col].strip() == "":
                missing[sid].append(day)
            else:
                out[sid].append({"date": day, "value": number(row[col], -20, 100)})
    return result({sid: ordered(values) for sid, values in out.items()}, metadata={
        "release_type": "final", "missing_months": dict(missing),
        "historical_first_print": False,
        "units": {sid: "percent, median expected inflation" for sid in out}})


def michigan_current(body):
    text = text_body(body)
    if "EXPECTED CHANGE IN PRICES" not in text or "NEXT YEAR" not in text or "NEXT 5 YEARS" not in text:
        raise ValueError("Michigan current inflation table headings changed")
    out = {"umich_inflation_1y": [], "umich_inflation_5_10y": []}
    stages = {}
    for row in csv.reader(io.StringIO(text)):
        if not row or not row[0].strip():
            continue
        label = row[0].strip()
        if label == "DATE OF SURVEY":
            continue
        preliminary = label.endswith("(P)")
        month = label.removesuffix("(P)").strip()
        if month not in umich.MONTHS:
            raise ValueError(f"Unrecognized data row {label!r}")
        if len(row) < 6:
            raise ValueError("Truncated Michigan current row")
        day = f"{int(row[1]):04d}-{umich.MONTHS[month]:02d}-01"
        stages[day] = "preliminary" if preliminary else "final"
        for sid, col in zip(out, (3, 5)):
            out[sid].append({"date": day, "value": number(row[col], -20, 100)})
    return result({sid: ordered(values) for sid, values in out.items()}, metadata={
        "release_types": stages, "historical_first_print": False})


def sce_extensions(body, fields=None):
    fields = SCE_FIELDS if fields is None else fields
    out = {}
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        strings = sce._shared_strings(archive)
        for sid, (sheet, header, unit) in fields.items():
            xml = ET.fromstring(archive.read(sce._sheet_member(archive, sheet)))
            data = xml.find(sce._NS + "sheetData")
            if data is None:
                raise ValueError(f"Missing sheetData for {sheet}")
            column, observations = None, []
            for row in data:
                cells = sce._cells(row, strings)
                hits = [key for key, value in cells.items() if value == header]
                if hits:
                    if len(hits) != 1 or column is not None:
                        raise ValueError(f"Duplicate header {header!r} in {sheet}")
                    column = hits[0]
                month = cells.get("A", "")
                if not re.fullmatch(r"20\d{4}", month):
                    continue
                if column is None or column not in cells:
                    raise ValueError(f"Missing {header!r} for {month} in {sheet}")
                day = f"{month[:4]}-{month[4:]}-01"
                value = number(cells[column], 0 if "probability" in unit else -100, 100)
                observations.append({"date": day, "value": value})
            out[sid] = ordered(observations)
            for prev, cur in zip(out[sid], out[sid][1:]):
                a, b = iso_day(prev["date"]), iso_day(cur["date"])
                if b.year * 12 + b.month - (a.year * 12 + a.month) != 1:
                    raise ValueError(f"Nonconsecutive monthly history for {sid}")
    return result(out, metadata={
        "historical_first_print": False,
        "units": {sid: field[2] for sid, field in fields.items()},
        "fields": {sid: {"sheet": field[0], "header": field[1]} for sid, field in fields.items()}})


def mint_transcription(body):
    rows = table(body, ("product_id", "period_end", "cumulative_net_sales"))
    products = defaultdict(list)
    for row in rows:
        pid = row["product_id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", pid):
            raise ValueError("Unsafe or missing Mint product ID")
        if iso_day(row["period_end"]).weekday() != 6:
            raise ValueError("Mint period must end on Sunday")
        value = number(row["cumulative_net_sales"], 0)
        if not value.is_integer():
            raise ValueError("Mint sales must be integer counts")
        products[pid].append({"date": row["period_end"], "value": value})
    return mint_series(products)


def mint_series(products):
    """Derive weekly changes after joining validated cumulative observations."""
    out, gaps = {}, {}
    for pid, points in products.items():
        points = ordered(points)
        out[f"mint_{pid}_cumulative"] = points
        gains, missing = [], []
        for prev, cur in zip(points, points[1:]):
            if iso_day(cur["date"]) - iso_day(prev["date"]) != timedelta(days=7):
                missing.append(cur["date"])
                continue
            gains.append({"date": cur["date"], "value": cur["value"] - prev["value"]})
        if gains:
            out[f"mint_{pid}_weekly_net_sales"] = gains
        gaps[pid] = missing
    return result(out, metadata={
        "import_type": "manual transcription; verify against archived official release",
        "skipped_nonweekly_differences": gaps, "historical_first_print": False,
        "target": "weekly net sales; negative changes are retained; totals are diagnostics only"})


def mint(body):
    headers = next(csv.reader(io.StringIO(text_body(body))), [])
    if "Item" not in headers:
        return mint_transcription(body)
    rows = table(body, ("Program Name", "Item", "Item Description", "Adj. Net Demand", "Date Sales Report is Valid"))
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=["product_id", "period_end", "cumulative_net_sales"])
    writer.writeheader()
    products, periods = {}, set()
    for row in rows:
        if not all(row.get(field, "") for field in headers):
            raise ValueError("Official Mint CSV contains missing fields")
        raw_day = row["Date Sales Report is Valid"]
        if not re.fullmatch(r"\d{2}/\d{2}/\d{4}", raw_day):
            raise ValueError("Official Mint CSV requires MM/DD/YYYY dates")
        day = datetime.strptime(raw_day, "%m/%d/%Y").date().isoformat()
        periods.add(day)
        writer.writerow({"product_id": row["Item"], "period_end": day,
                         "cumulative_net_sales": row["Adj. Net Demand"]})
        products[row["Item"]] = {"program_name": row["Program Name"], "description": row["Item Description"]}
    if len(periods) != 1:
        raise ValueError("Official Mint CSV must contain exactly one report date")
    parsed = mint_transcription(buffer.getvalue())
    parsed["metadata"].update(import_type="Official CSV supplied by the user; original bytes retained",
                              report_date=next(iter(periods)), products=products)
    return parsed


PARSERS = {
    "michigan_finals": michigan_finals,
    "michigan_current": michigan_current,
    "sce": sce_extensions,
    "mint": mint,
}
