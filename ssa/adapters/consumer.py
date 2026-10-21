"""Offline consumer-source parsers; network retrieval lives in the collection tool.

Scalar output uses the arena's ``{date, value}`` records. Ranking output uses
``{week_end, items}``. Metadata and vintage evidence never masquerade as values.
"""
import csv
import io
import json
import math
import re
import unicodedata
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
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


def entity_slug(value):
    """ASCII token for a publisher label; entity equivalence uses explicit aliases."""
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


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


class PublisherHTML(HTMLParser):
    """Read inert publisher HTML without executing scripts or browser state."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.scripts, self.text = [], {}, []
        self.script = None
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        if tag == "script":
            self.script = attrs.get("id")
            if self.script:
                self.scripts[self.script] = ""
        if tag in {"script", "style"}:
            self.ignored += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.ignored = max(0, self.ignored - 1)
            self.script = None

    def handle_data(self, data):
        if self.script:
            self.scripts[self.script] += data
        if not self.ignored and data.strip():
            self.text.append(data.strip())


def publisher_html(body):
    parser = PublisherHTML()
    parser.feed(body.decode("utf-8") if isinstance(body, bytes) else body)
    return parser


class PublisherTables(HTMLParser):
    """Read literal cells in main-content tables without running publisher code."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.table, self.row, self.cell = [], None, None, None
        self.in_main = False
        self.spanning = set()

    def handle_starttag(self, tag, attrs):
        if tag == "main":
            self.in_main = True
        if not self.in_main:
            return
        if tag == "table":
            if self.table is not None:
                raise ValueError("Nested publisher tables are not supported")
            self.table = []
        elif tag == "tr" and self.table is not None:
            self.row = []
        elif tag in {"th", "td"} and self.row is not None:
            if any(key in {"rowspan", "colspan"} and value != "1" for key, value in attrs):
                self.spanning.add(len(self.tables))
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None and data.strip():
            self.cell.append(data.strip())

    def handle_endtag(self, tag):
        if tag in {"th", "td"} and self.cell is not None:
            self.row.append(" ".join(" ".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            self.tables.append(self.table)
            self.table = None
        elif tag == "main":
            self.in_main = False


def publisher_tables(body, selected=None):
    parser = PublisherTables()
    parser.feed(body.decode("utf-8") if isinstance(body, bytes) else body)
    if selected is not None:
        if selected < 0 or selected in parser.spanning:
            raise ValueError("Spanning cells require an explicit table mapping")
        return parser.tables[selected]
    return parser.tables


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


def ranked(groups, expected=10):
    out = defaultdict(list)
    for (sid, week), entries in sorted(groups.items()):
        ranks = [rank for rank, item in entries]
        items = [item for rank, item in sorted(entries)]
        if len(set(items)) != len(items) or sorted(ranks) != list(range(1, expected + 1)):
            raise ValueError(f"Incomplete/duplicate ranking for {sid}, {week}")
        out[sid].append({"week_end": week, "items": items})
    if not out:
        raise ValueError("No complete rankings")
    return dict(out)


def netflix_country(body):
    rows = table(body, ("country_name", "country_iso2", "week", "category", "weekly_rank",
                        "show_title", "season_title"), delimiter="\t")
    groups = defaultdict(list)
    for row in rows:
        if row["country_iso2"] != "US":
            continue
        week = row["week"]
        if iso_day(week).weekday() != 6:
            raise ValueError("Netflix week must end on Sunday")
        category = row["category"]
        if category not in ("Films", "TV"):
            raise ValueError(f"Unrecognized Netflix category {category!r}")
        title = row["show_title"].strip()
        if not title:
            raise ValueError("Empty Netflix title")
        item = json.dumps([title, row["season_title"].strip()], ensure_ascii=True, separators=(",", ":"))
        groups[("netflix_us_" + category.lower(), week)].append((int(row["weekly_rank"]), item))
    return result(lists=ranked(groups), metadata={
        "country": "US", "unit": "official weekly rank", "historical_first_print": False,
        "entity_id": "JSON [show_title, season_title]; publisher has no stable title ID in this export",
        "measurement": "Monday through Sunday; no country-level views inferred"})


def nyt_books(body):
    payload = json.loads(text_body(body))
    if payload.get("status") != "OK":
        raise ValueError("NYT response is not an OK Books API payload")
    data = payload["results"]
    if not isinstance(data, dict):
        raise ValueError("Expected one dated NYT list or overview")
    published = data["published_date"]
    bestseller = data["bestsellers_date"]
    iso_day(published)
    iso_day(bestseller)
    lists = data.get("lists", [data])
    groups, markers = defaultdict(list), {}
    for listing in lists:
        slug = listing["list_name_encoded"]
        if not re.fullmatch(r"[a-z0-9-]+", slug):
            raise ValueError("Unsafe NYT list name")
        entries = []
        for book in listing["books"]:
            isbn = str(book["primary_isbn13"])
            if not re.fullmatch(r"\d{13}", isbn) or isbn == "0" * 13:
                raise ValueError("NYT book lacks an unambiguous ISBN13")
            entries.append((int(book["rank"]), isbn))
            markers[isbn] = {key: book.get(key) for key in ("title", "author", "dagger", "asterisk")}
        if len(entries) < 10:
            raise ValueError("NYT list has fewer than ten entries")
        groups[("nyt_" + slug.replace("-", "_"), published)] = [item for item in entries if item[0] <= 10]
    return result(lists=ranked(groups), metadata={
        "publication_date": published, "bestsellers_date": bestseller,
        "date_semantics": "week_end is the publisher's list publication date, not the sales-window end",
        "books": markers, "historical_first_print": False})


def steam_transcription(body):
    rows = table(body, ("country", "week_start", "week_end_exclusive", "rank", "entity_type", "entity_id"))
    groups, periods = defaultdict(list), {}
    for row in rows:
        if row["country"] != "US":
            raise ValueError("Steam review import requires US rows only")
        start, end = iso_day(row["week_start"]), iso_day(row["week_end_exclusive"])
        if start.weekday() != 1 or end - start != timedelta(days=7):
            raise ValueError("Steam measurement period must be Tuesday to Tuesday")
        kind, eid = row["entity_type"], row["entity_id"]
        if kind not in ("app", "sub", "bundle") or not eid.isdigit():
            raise ValueError("Steam entity must preserve its app/sub/bundle identity")
        week = (end - timedelta(days=1)).isoformat()
        groups[("steam_us_top_sellers", week)].append((int(row["rank"]), f"{kind}:{eid}"))
        periods[week] = {"start": start.isoformat(), "end_exclusive": end.isoformat()}
    return result(lists=ranked(groups), metadata={
        "import_type": "manual transcription; verify against archived official page",
        "measurement_periods": periods, "unit": "revenue rank", "historical_first_print": False})


def steam_official(body):
    page = publisher_html(body)
    payload = json.loads(page.scripts["valve-ssr-data"])
    if payload["route"] != "/charts/topsellers/:country?/:week?":
        raise ValueError("Expected weekly Steam chart, not a live sales chart")
    loaders = [json.loads(item) if isinstance(item, str) else item for item in payload["loaderData"]]
    weeks = [item["weekly"] for item in loaders if "weekly" in item]
    if len(weeks) != 1 or weeks[0]["strCountryCode"] != "US":
        raise ValueError("Steam embedded chart does not identify the US weekly list")
    data = weeks[0]
    start = datetime.fromtimestamp(data["rtWeekStart"], timezone.utc).date()
    if start.weekday() != 1:
        raise ValueError("Steam week label must start on Tuesday")
    end = start + timedelta(days=7)
    entries = []
    entity_labels = {}
    ranks = data["rgRanks"]
    if len({row["nRank"] for row in ranks}) != len(ranks):
        raise ValueError("Steam chart repeats a rank")
    for row in ranks:
        if row["nRank"] > 10:
            continue
        keys = [(kind, row["itemKey"][key]) for kind, key in
                (("app", "appid"), ("sub", "packageid"), ("bundle", "bundleid"))
                if row["itemKey"].get(key)]
        if len(keys) != 1:
            raise ValueError("Steam item lacks an unambiguous typed entity ID")
        kind, entity = keys[0]
        if not isinstance(entity, int) or entity <= 0:
            raise ValueError("Invalid Steam entity ID")
        entries.append((row["nRank"], f"{kind}:{entity}"))
        entity_labels[f"{kind}:{entity}"] = {"rank_last_week": row.get("nRankLastWeek"),
                                            "first_top100": row.get("bFirstTop100")}
    week_end = (end - timedelta(days=1)).isoformat()
    return result(lists=ranked({("steam_us_top_sellers", week_end): entries}), metadata={
        "country": "US", "unit": "revenue rank", "historical_first_print": False,
        "measurement_periods": {week_end: {"start_day_label": start.isoformat(), "end_exclusive_day_label": end.isoformat()}},
        "week_boundary_timezone": "America/Los_Angeles; exact measurement cutoff requires review",
        "publisher_week_timestamp": data["rtWeekStart"], "entities": entity_labels,
        "note": "rtWeekStart is a publisher calendar label, not evidence of a midnight UTC measurement cutoff"})


def steam(body):
    return steam_official(body) if body.lstrip().startswith(b"<") else steam_transcription(body)


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


def acsi_transcription(body):
    rows = table(body, ("industry_id", "company_id", "company_name", "study_year", "published_date", "score", "evidence_url", "evidence_quote"))
    out = defaultdict(list)
    studies = []
    for row in rows:
        industry, company = row["industry_id"], row["company_id"]
        if not all(re.fullmatch(r"[a-z0-9_-]+", key) for key in (industry, company)):
            raise ValueError("ACSI requires explicit stable company/industry IDs")
        year = int(row["study_year"])
        day = iso_day(row["published_date"])
        if not 2021 <= day.year <= 2026:
            raise ValueError("ACSI review scope is releases published in 2021 through 2026")
        if year < 1994:
            raise ValueError("ACSI study year predates the index")
        if day.year < year:
            raise ValueError("ACSI publication predates study year")
        if not row["evidence_url"].startswith(("https://theacsi.org/", "https://theacsi.com/")):
            raise ValueError("ACSI evidence must identify the official publisher")
        if not row["evidence_quote"].strip() or not row["company_name"].strip():
            raise ValueError("ACSI company scores require an explicit name and evidence quotation")
        out[f"acsi_{industry}_{company}"].append({"date": day.isoformat(), "value": number(row["score"], 0, 100)})
        studies.append(dict(row))
    return result({sid: ordered(points) for sid, points in out.items()}, metadata={
        "studies": studies, "historical_first_print": False,
        "import_type": "manual transcription; first-print status requires independent evidence",
        "unit": "ACSI score, 0-100; cadence is measured per company and industry"})


PARSERS = {
    "michigan_finals": michigan_finals,
    "michigan_current": michigan_current,
    "sce": sce_extensions,
    "netflix": netflix_country,
    "nyt": nyt_books,
    "steam": steam,
    "mint": mint,
    "acsi": acsi_transcription,
}
