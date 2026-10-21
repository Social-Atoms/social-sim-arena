"""Collect public ACSI benchmark tables and append dated, evidenced snapshots.

These are current publisher vintages, not verified historical first prints.
Only explicitly selected national score tables enter the existing scalar shape.
"""
import argparse
import csv
import io
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import requests

from ssa import consumer
from ssa.adapters import consumer as adapter
from ssa.adapters.consumer import entity_slug as slug

PREFIX = "https://theacsi.com/industries/"
# Table positions are pinned after inspecting headers and population scope.
PROFILES = {
    "energy/cooperative-utilities/": ("energy-utilities", (0,)),
    "energy/electric-investor-owned-utilities/": ("energy-utilities", (0,)),
    "energy/energy-utilities/": ("energy-utilities", (0,)),
    "energy/gas-investor-owned-utilities/": ("energy-utilities", (0,)),
    "energy/municipal-utilities/": ("energy-utilities", (0,)),
    "finance-and-insurance/banks/": ("finance", (0, 1, 2)),
    "finance-and-insurance/credit-unions/": ("finance", (0,)),
    "finance-and-insurance/financial-advisors/": ("finance", (0,)),
    "finance-and-insurance/online-investment/": ("finance", (0,)),
    "finance-and-insurance/health-insurance/": ("insurance", (0,)),
    "finance-and-insurance/life-insurance/": ("insurance", (0,)),
    "finance-and-insurance/mortgage-lenders/": ("insurance", (0,)),
    "finance-and-insurance/property-and-casualty-insurance/": ("insurance", (0,)),
    "interactive-media-subscription-entertainment/ai-platforms/": ("ai-platforms", (0,)),
    "interactive-media-subscription-entertainment/audio-streaming-service/": ("entertainment", (0,)),
    "interactive-media-subscription-entertainment/search-engines-and-information/": ("entertainment", (0,)),
    "interactive-media-subscription-entertainment/social-media/": ("entertainment", (0,)),
    "interactive-media-subscription-entertainment/subscription-tv-service/": ("entertainment", (0,)),
    "interactive-media-subscription-entertainment/video-streaming-service/": ("entertainment", (0,)),
    "manufacturing/automobiles/": ("automobile", (1, 2)),
    "manufacturing/cell-phones/": ("telecommunications", (0,)),
    "manufacturing/household-appliances/": ("appliances", (0,)),
    "manufacturing/personal-computers/": ("appliances", (0,)),
    "manufacturing/smartwatches/": ("telecommunications", (0,)),
    "manufacturing/televisions/": ("appliances", (0,)),
    "manufacturing/vacuum-cleaners/": ("appliances", (0,)),
    "manufacturing/nondurable-products/": ("nondurable-products", (0, 1, 2, 3, 4, 5)),
    "restaurant/food-delivery/": ("restaurant", (0,)),
    "restaurant/full-service-restaurants/": ("restaurant", (0,)),
    "restaurant/quick-service-restaurants/": ("restaurant", (0,)),
    "retail/convenience-stores/": ("convenience-store", (0,)),
    "retail/gas-stations/": ("retail-and-consumer-shipping", (0,)),
    "retail/general-merchandise-retailers/": ("retail-and-consumer-shipping", (0,)),
    "retail/online-retailers/": ("retail-and-consumer-shipping", (0,)),
    "retail/specialty-retailers/": ("retail-and-consumer-shipping", (0,)),
    "retail/supermarkets/": ("retail-and-consumer-shipping", (0,)),
    "consumer-shipping-and-mail/": ("retail-and-consumer-shipping", (0, 1)),
    "drugstores/": ("retail-and-consumer-shipping", (0,)),
    "telecommunications/fiber-internet-service-provider/": ("telecommunications", (1,)),
    "telecommunications/internet-service-providers/": ("telecommunications", (1,)),
    "telecommunications/wireless-phone-service/": ("telecommunications", (0, 1, 2)),
    "travel/airlines/": ("travel", (0,)),
    "travel/car-rentals/": ("travel", (0,)),
    "travel/lodging/": ("travel", (0,)),
    "travel/online-travel-agencies/": ("travel", (0,)),
    "travel/rideshare/": ("travel", (0,)),
}
FIELDS = ["industry_id", "company_id", "company_name", "study_year", "published_date", "score",
          "evidence_url", "evidence_quote", "evidence_kind", "evidence_table", "evidence_row",
          "evidence_column", "evidence_name_column", "evidence_header", "release_url", "value_vintage", "entity_kind"]


def entity_kind(industry, name):
    key = slug(name)
    if "all_other" in key:
        return "aggregate"
    equivalent = lambda value: value.replace("_and_", "_").replace("_", "")
    if equivalent(key) == equivalent(industry):
        return "industry"
    if key in {"national_banks", "super_regional_banks", "regional_community_banks",
            "streaming_audio_services", "mobile_network_operators", "full_service_mvnos", "value_mvnos",
            "mass_market_nameplates", "luxury_nameplates", "cooperative_utilities", "municipal_utilities",
            "electric_investor_owned_utilities", "gas_investor_owned_utilities", "consumer_shipping",
            "us_postal_service", "fiber_internet_service_providers", "non_fiber_internet_service_providers"}:
        return "industry_segment"
    return "company_or_brand"


def release_for(documents, year, group):
    def matches(url):
        tail = url.split("/press-releases/")[-1]
        if not tail.startswith(str(year) + "/"):
            return False
        title = tail.rsplit("/", 2)[-2]
        if group == "insurance":
            return "insurance" in title and "national" not in title
        if group == "appliances":
            return "appliance" in title or "household-and-electronics" in title
        return group in title and "national" not in title and "segment-leaders" not in title
    urls = [url for url, doc in documents.items() if doc["status"] == "captured" and matches(url)]
    if len(urls) != 1:
        raise ValueError(f"Need one dated {group} study release in {year}; found {len(urls)}")
    return urls[0]


def selected_rows(url, body, documents):
    path = url.removeprefix(PREFIX)
    group, selected = PROFILES[path]
    rows, pending = [], []
    for table_index in selected:
        cells = adapter.publisher_tables(body, selected=table_index)
        header = cells[0]
        if header[0] == "Anchor Link":
            name_column = 1
        elif header[0] in {"", "Brand", "Company", "Industry", "Category", "Benchmarks"}:
            name_column = 0
        else:
            raise ValueError(f"Unexpected national benchmark header: {url}: {header}")
        columns = [(i, re.search(r"(?<!\d)(202[1-6])(?!\d)", label)) for i, label in enumerate(header)]
        columns = [(i, int(match.group(1))) for i, match in columns if match]
        if not columns or len({year for i, year in columns}) != len(columns):
            raise ValueError(f"Missing or ambiguous study-year headers: {url}")
        industry = slug(path.rstrip("/").split("/")[-1])
        if path.endswith("search-engines-and-information/"):
            if cells[1][name_column] != "Online Sports Betting and iGaming":
                raise ValueError("Reused ACSI URL no longer identifies the inspected betting population")
            industry = "online_sports_betting_igaming"
        if path.endswith("nondurable-products/"):
            industry = slug(cells[1][name_column])
        for row_index, cell in enumerate(cells[1:], 1):
            if len(cell) != len(header):
                raise ValueError(f"Ragged selected ACSI table: {url}, row {row_index}")
            name = cell[name_column].strip("*")
            if not name:
                continue
            company = "lexus" if industry == "automobiles" and name == "Lexus (Toyota)" else slug(name)
            scoped_industry = industry
            if path.endswith("wireless-phone-service/") and "Category" in header:
                scoped_industry += "_" + slug(cell[header.index("Category")])
            for column, year in columns:
                if cell[column] == "NA":
                    continue
                score = adapter.number(cell[column], 0, 100)
                try:
                    release = release_for(documents, year, group)
                except ValueError as error:
                    pending.append({"url": url, "table": table_index, "row": row_index,
                                    "company": name, "year": year, "score": score, "reason": str(error)})
                    continue
                published = re.search(r"/press-releases/(\d{4}/\d{2}/\d{2})/", release).group(1).replace("/", "-")
                rows.append(dict(zip(FIELDS, [scoped_industry, company, name, year, published, score,
                    url, " ".join(item for item in cell if item), "benchmark_table", table_index,
                    row_index, column, name_column, json.dumps(header, ensure_ascii=True), release,
                    "current_benchmark_snapshot; historical_first_print_unverified", entity_kind(scoped_industry, name)])))
    return rows, pending


def collect(root, timeout):
    path = root / "corpus/acsi-benchmarks.json"
    corpus = json.loads(path.read_text()) if path.exists() else {"format": "ssa.consumer.corpus.v1", "documents": {}}
    for suffix in PROFILES:
        url = PREFIX + suffix
        if corpus["documents"].get(url, {}).get("status") == "captured":
            continue
        try:
            response = requests.get(url, headers={"User-Agent": "SSA-Consumer-Review/1.0"}, timeout=(8, timeout))
            consumer.official_url("acsi", response.url)
            body = response.content
            file = f"corpus/acsi-benchmarks/{consumer.sha256(body)}.html"
            consumer.immutable(root / file, body)
            valid = response.status_code == 200 and adapter.publisher_tables(body)
            corpus["documents"][url] = {"status": "captured" if valid else "invalid_response", "file": file,
                "sha256": consumer.sha256(body), "http_status": response.status_code, "captured_at": consumer.now()}
        except requests.RequestException as error:
            corpus["documents"][url] = {"status": "transport_failure", "error": type(error).__name__, "at": consumer.now()}
        consumer.write_json(path, corpus)
        print(url, corpus["documents"][url]["status"], flush=True)


def collect_releases(root, timeout):
    """Use the publisher sitemap to cover releases omitted by the 100-item index."""
    artifacts, candidates = [], set()

    def xml(url):
        consumer.official_url("acsi", url)
        response = requests.get(url, headers={"User-Agent": "SSA-Consumer-Review/1.0"}, timeout=(8, timeout))
        response.raise_for_status()
        consumer.official_url("acsi", response.url)
        file = f"discovery/acsi/{consumer.sha256(response.content)}.xml"
        consumer.immutable(root / file, response.content)
        artifacts.append({"url": url, "file": file, "sha256": consumer.sha256(response.content)})
        return [node.text for node in ET.fromstring(response.content).iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]

    for url in xml("https://theacsi.com/sitemap_index.xml"):
        if re.fullmatch(r"https://theacsi.com/post-sitemap\d*\.xml", url):
            candidates.update(item for item in xml(url) if re.fullmatch(
                r"https://theacsi.com/news-and-resources/press-releases/202[1-6]/\d{2}/\d{2}/[^/?#]+/", item))
    path = root / "corpus/acsi.json"
    corpus = json.loads(path.read_text())
    consumer.write_json(root / "acsi-sitemap-discovery.json", {"artifacts": artifacts, "candidate_urls": sorted(candidates)})
    for url in sorted(candidates):
        if corpus["documents"].get(url, {}).get("status") == "captured":
            continue
        try:
            response = requests.get(url, headers={"User-Agent": "SSA-Consumer-Review/1.0"}, timeout=(8, timeout))
            consumer.official_url("acsi", response.url)
            file = f"corpus/acsi/{consumer.sha256(response.content)}.html"
            consumer.immutable(root / file, response.content)
            text = " ".join(adapter.publisher_html(response.content).text)
            valid = response.status_code == 200 and "ANN ARBOR" in text and "Just a moment" not in text
            corpus["documents"][url] = {"status": "captured" if valid else "invalid_response", "file": file,
                "sha256": consumer.sha256(response.content), "http_status": response.status_code,
                "captured_at": consumer.now(), "historical_first_print": False, "discovered_by": "publisher sitemap"}
        except requests.RequestException as error:
            corpus["documents"][url] = {"status": "transport_failure", "error": type(error).__name__, "at": consumer.now()}
        consumer.write_json(path, corpus)
        print(url, corpus["documents"][url]["status"], flush=True)


def build(root):
    releases = json.loads((root / "corpus/acsi.json").read_text())
    benchmarks = json.loads((root / "corpus/acsi-benchmarks.json").read_text())
    documents = {**releases["documents"], **benchmarks["documents"]}
    rows, pending, seen = [], [], {}
    for suffix in PROFILES:
        url = PREFIX + suffix
        artifact = documents[url]
        if artifact["status"] != "captured":
            pending.append({"url": url, "reason": "No captured benchmark page"})
            continue
        body = consumer.archive_path(root, artifact["file"]).read_bytes()
        if consumer.sha256(body) != artifact["sha256"]:
            raise ValueError("ACSI benchmark corpus hash mismatch")
        selected, missing = selected_rows(url, body, documents)
        pending.extend(missing)
        for row in selected:
            key = (row["industry_id"], row["company_id"], row["published_date"])
            if key in seen:
                if row["score"] != seen[key]:
                    raise ValueError(f"Ambiguous benchmark company identity: {key}")
                continue
            seen[key] = row["score"]
            rows.append(row)
    consumer.write_json(root / "acsi-benchmark-pending.json", pending)
    catalog = {}
    for row in rows:
        sid = f"acsi_{row['industry_id']}_{row['company_id']}"
        entry = catalog.setdefault(sid, {"industry_id": row["industry_id"], "company_name": row["company_name"],
            "entity_kind": row["entity_kind"], "evidence_urls": [], "release_urls": [], "historical_first_print": False})
        for field, value in (("evidence_urls", row["evidence_url"]), ("release_urls", row["release_url"])):
            if value not in entry[field]:
                entry[field].append(value)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    corpus = {"format": "ssa.consumer.corpus.v1", "documents": documents}
    body, evidence = buffer.getvalue().encode(), consumer.encoded(corpus)
    entry = consumer.index(root)["sources"].get("acsi", {})
    existing = None
    for relative in entry.get("vintages", []):
        manifest = json.loads(consumer.archive_path(root, relative).read_text())
        if (manifest["raw_sha256"] == consumer.sha256(body) and manifest.get("evidence_file")
                and Path(manifest["evidence_file"]).stem == consumer.sha256(evidence)):
            consumer.read_vintage(root, relative)
            existing = relative
            break
    if existing:
        consumer.update_index(root, "acsi", {"at": consumer.now(), "status": "collected",
            "detail": "Reused identical verified benchmark transcription"}, existing)
    else:
        consumer.ingest("acsi", body, "https://theacsi.com/news-and-resources/press-releases/", root,
                        evidence_body=evidence, evidence_suffix=".json")
    consumer.record_attempt(root, "acsi", "corpus_only",
        f"Verified {len(rows)} dated benchmark cells plus prior release samples; first prints and complete history remain unverified",
        benchmark_pages=len(benchmarks["documents"]), pending_cells=len(pending),
        benchmark_corpus="corpus/acsi-benchmarks.json", pending_file="acsi-benchmark-pending.json")
    merged = {sid: points for sid, points in consumer.build_all(root)["series"].items() if sid.startswith("acsi_")}
    for relative in consumer.index(root)["sources"]["acsi"]["vintages"]:
        parsed, _ = consumer.read_vintage(root, relative)
        for row in parsed["metadata"]["studies"]:
            sid = f"acsi_{row['industry_id']}_{row['company_id']}"
            entry = catalog.setdefault(sid, {"industry_id": row["industry_id"], "company_name": row["company_name"],
                "entity_kind": row.get("entity_kind", "company_or_brand"), "evidence_urls": [], "release_urls": [],
                "historical_first_print": False})
            names = entry.setdefault("published_names", [])
            if row["company_name"] not in names:
                names.append(row["company_name"])
            for field, value in (("evidence_urls", row["evidence_url"]),
                                 ("release_urls", row.get("release_url", row["evidence_url"]))):
                if value not in entry[field]:
                    entry[field].append(value)
    assert set(catalog) == set(merged)
    consumer.write_json(root / "acsi-catalog.json", catalog)
    groups = defaultdict(set)
    for sid, entry in catalog.items():
        groups[entry["industry_id"]].add(sid)
    benchmark_counts = Counter(row["industry_id"] for row in rows)
    six_year = sum({point["date"][:4] for point in points} == set(map(str, range(2021, 2027))) for points in merged.values())
    kinds = dict(sorted(Counter(entry["entity_kind"] for entry in catalog.values()).items()))
    report = ["# ACSI Collection Coverage", "", "Status: pending review; historical first prints remain unverified.", "",
        f"- Archived {len(releases['documents'])} official release pages and {len(benchmarks['documents'])} benchmark pages.",
        f"- Selected national score tables from {len(PROFILES)} explicitly mapped benchmark pages.",
        f"- Verified {len(rows)} dated benchmark cells; retained {len(pending)} undated cells separately.",
        f"- Merged archive: {len(merged)} instruments and {sum(map(len, merged.values()))} observations.",
        f"- Instrument categories: {json.dumps(kinds, sort_keys=True)}.", "",
        "Instrument counts include brands, industries, segments, and smaller-company aggregates. They are not counts of unique companies.", "",
        "The date in the scalar records is the associated official study release date. Benchmark values come from the captured current page, including prior-year columns. This does not prove that those values were available on the associated historical release date.", "",
        "## Coverage by Industry", "", "| Industry | Instruments | Benchmark cells | Merged observations | First release | Last release |",
        "| --- | ---: | ---: | ---: | --- | --- |"]
    for industry, instruments in sorted(groups.items()):
        points = [point for sid in instruments for point in merged[sid]]
        dates = [point["date"] for point in points]
        report.append(f"| {industry} | {len(instruments)} | {benchmark_counts[industry]} | {len(points)} | {min(dates)} | {max(dates)} |")
    report += ["", "## Remaining Evidence", "",
        f"- Most instruments have only one or two benchmark years; {six_year} currently span every year from 2021 through 2026.",
        "- Historical release text is preserved but is not yet exhaustively normalized into company histories.",
        "- The pending cell file contains current values without a unique dated public study release. No date is guessed.",
        "- Regional, fuel-type, device-type, individual experience-benchmark, and stale retail-segment tables are excluded from the selected national company histories.",
        "- Health-care industry tables are archived but excluded from the company-study importer pending a corresponding study-release mapping.",
        "- The old search-engines URL now hosts online sports betting and iGaming data. Its population is identified from the actual table header.",
        "- Calendar, original first-publication availability, rights, and formal target activation remain separate review decisions.", ""]
    notes = []
    for selection in sorted(root.glob("acsi-*-release-selection.json")):
        for note in json.loads(selection.read_text()).get("identity_review", []):
            notes.append(f"- {note} Source selection: `{selection.name}`.")
    if notes:
        report += ["## Identity and Population Review", "", *notes, ""]
    (root / "ACSI_REPORT.md").write_text("\n".join(report))
    print(f"Imported {len(rows)} benchmark cells; {len(pending)} cells lack a unique dated release")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("collect", "collect-releases", "build"))
    parser.add_argument("--archive", type=Path, default=consumer.DEFAULT_ARCHIVE)
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()
    if args.command == "collect":
        collect(args.archive, args.timeout)
    elif args.command == "collect-releases":
        collect_releases(args.archive, args.timeout)
    else:
        build(args.archive)


if __name__ == "__main__":
    main()
