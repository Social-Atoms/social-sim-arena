"""Collect, validate, replay, and append consumer data for local review only.

  python tools/consumer_data.py collect
  python tools/consumer_data.py build --base site/data.json
  python tools/consumer_data.py verify
  python tools/consumer_data.py import netflix /path/all-weeks-countries.tsv --url URL

No refresh, paid provider call, publication, or git command is run here.
"""
import argparse
import csv
import getpass
import io
import json
import os
import sys
import re
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import jsonschema
import requests

from ssa import consumer
from ssa.adapters.consumer import PARSERS, entity_slug

URLS = {
    "michigan_finals": "https://www.sca.isr.umich.edu/files/tbmpx1px5.csv",
    "michigan_current": "https://www.sca.isr.umich.edu/files/tbcpx1px5.csv",
    "netflix": "https://www.netflix.com/tudum/top10/data/all-weeks-countries.tsv",
    "steam": "https://store.steampowered.com/charts/topsellers/US",
    "mint": "https://www.usmint.gov/about/production-sales-figures/cumulative-sales",
    "acsi": "https://theacsi.com/news-and-resources/press-releases/",
    "nyt": "https://api.nytimes.com/svc/books/v3/lists/current/hardcover-fiction.json",
    "sce": "https://www.newyorkfed.org/medialibrary/interactives/sce/sce/downloads/data/frbny-sce-data.xlsx",
}
AUTOMATED = {"michigan_finals", "michigan_current", "netflix", "nyt", "sce", "steam"}
PERMISSION_REQUIRED = {"netflix", "steam", "nyt", "acsi"}


def nyt_key(root):
    key = os.environ.get("NYT_API_KEY", "").strip()
    path = Path(root) / "credentials/nyt-api-key"
    return key or (path.read_text().strip() if path.exists() else "")


def configure_nyt(root):
    if not sys.stdin.isatty():
        raise ValueError("Run configure-nyt in an interactive terminal; the key input must be hidden")
    key = getpass.getpass("NYT Books API key (input hidden): ").strip()
    if not key or any(char.isspace() for char in key):
        raise ValueError("Enter one nonempty API key, not an account password or a URL")
    directory = Path(root) / "credentials"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".nyt-")
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(key + "\n")
        os.replace(temporary, directory / "nyt-api-key")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"NYT API key saved privately in {directory / 'nyt-api-key'}")
    print("The collector can read it automatically. No key value was displayed.")


def collect_source(source, root, timeout, fetch_sce=False, url=None):
    url = url or URLS[source]
    consumer.official_url(source, url)
    if source == "sce" and not fetch_sce:
        files = sorted((ROOT / "sources/sce").glob("????-??-??.xlsx"))
        if not files:
            consumer.record_attempt(root, source, "missing_archive", "No existing SCE workbook")
            return
        path = files[-1]
        consumer.ingest(source, path.read_bytes(), url, root)
        consumer.record_attempt(root, source, "collected", "Reused committed official workbook; capture day only",
                                repository_file=str(path.relative_to(ROOT)), repository_capture_day=path.stem)
        return
    params = None
    if source == "nyt":
        key = nyt_key(root)
        if not key:
            consumer.record_attempt(root, source, "credential_required", "No NYT key configured; run configure-nyt locally or set NYT_API_KEY", url=url)
            return
        params = {"api-key": key}
    try:
        response = requests.get(url, params=params, timeout=(min(timeout, 10), timeout),
                                headers={"User-Agent": "SSA-Consumer-Review/1.0"},
                                allow_redirects=source != "nyt")
    except requests.RequestException as error:
        # requests exception text can contain a credential-bearing URL.
        consumer.record_attempt(root, source, "transport_failure", type(error).__name__, url=url)
        return
    evidence = {"url": url, "http_status": response.status_code,
                "response_bytes": len(response.content), "response_sha256": consumer.sha256(response.content)}
    if response.status_code != 200:
        consumer.record_attempt(root, source, "http_failure", "Publisher request failed; no retry or bypass attempted", **evidence)
        return
    if source not in AUTOMATED:
        body = response.content
        discovery = f"discovery/{source}/{consumer.sha256(body)}.html"
        consumer.immutable(Path(root) / discovery, body)
        evidence["discovery_file"] = discovery
        challenge = b"Just a moment" in body or b"cf-chl" in body
        consumer.record_attempt(root, source, "access_challenge" if challenge else "manual_import_required",
                                "Publisher verification page" if challenge else "Official page needs an audited extraction or manual transcription", **evidence)
        return
    try:
        if source == "nyt":
            dated_list = re.fullmatch(r"https://api\.nytimes\.com/svc/books/v3/lists/(\d{4}-\d{2}-\d{2})/([a-z0-9-]+)\.json", url)
            if dated_list:
                parsed = PARSERS[source](response.content)
                requested_day, requested_list = dated_list.groups()
                expected = "nyt_" + requested_list.replace("-", "_")
                if (set(parsed["lists"]) != {expected}
                        or parsed["metadata"]["publication_date"] != requested_day):
                    raise ValueError("Historical NYT response returned a different publication date or list")
        if source == "steam" and re.search(r"/\d{4}-\d{2}-\d{2}$", url):
            parsed = PARSERS[source](response.content)
            requested = date.fromisoformat(url.rsplit("/", 1)[-1])
            returned = parsed["lists"]["steam_us_top_sellers"][0]["week_end"]
            if date.fromisoformat(returned) != requested + timedelta(days=6):
                raise ValueError("Historical Steam page returned a different measurement week")
        consumer.ingest(source, response.content, url, root)
    except (ValueError, KeyError, RuntimeError) as error:
        consumer.record_attempt(root, source, "parse_failure", str(error)[:300], **evidence)


def validate(bundle):
    schema = json.loads((ROOT / "schema/consumer-review.schema.json").read_text())
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(bundle)


def transcribe_acsi(selection, root):
    selected = json.loads(selection.read_text())
    corpus_path = Path(root) / "corpus/acsi.json"
    corpus = json.loads(corpus_path.read_text())
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=["industry_id", "company_id", "company_name", "study_year", "published_date", "score", "evidence_url", "evidence_quote"])
    writer.writeheader()
    count = 0

    def write(year, company, score, quote, url, company_id, industry_id=None):
        nonlocal count
        published = re.search(r"/press-releases/(\d{4}/\d{2}/\d{2})/", url).group(1).replace("/", "-")
        writer.writerow({"industry_id": industry_id or selected["industry_id"], "company_id": company_id,
                         "company_name": company, "study_year": year, "published_date": published,
                         "score": score, "evidence_url": url, "evidence_quote": quote})
        count += 1

    if selected.get("format") == "ssa.consumer.acsi.release-selection.v1":
        for study in selected["studies"]:
            url = consumer.official_url("acsi", study["release_url"])
            for entry in study["selections"]:
                industry = entry.get("industry_id", study.get("industry_id", selected.get("industry_id")))
                for company, score in entry["scores"].items():
                    aliases = selected.get("aliases", {})
                    company_id = aliases.get(f"{industry}:{company}", aliases.get(company, entity_slug(company)))
                    write(study["study_year"], company, score, entry["quote"], url, company_id, industry)
    else:
        for year, company, score, quote in selected["records"]:
            urls = [url for url in corpus["documents"] if f"/press-releases/{year}/" in url and "automobile" in url]
            if len(urls) != 1:
                raise ValueError(f"Expected one official automobile release for {year}")
            write(year, company, score, quote, urls[0], company.lower())
    consumer.ingest("acsi", buffer.getvalue().encode(), URLS["acsi"], root,
                    evidence_body=corpus_path.read_bytes(), evidence_suffix=".json")
    consumer.record_attempt(root, "acsi", "corpus_only", f"Historical release corpus plus {count} verified company-score selections; coverage is not exhaustive",
                            corpus_file="corpus/acsi.json", selection_file=str(selection))


def collect_history(source, root, timeout, weeks=52):
    if source == "nyt":
        entry = consumer.index(root)["sources"].get("nyt", {})
        if "latest_valid" not in entry:
            collect_source("nyt", root, timeout)
            entry = consumer.index(root)["sources"]["nyt"]
        have = set()
        for vintage in entry.get("vintages", [entry["latest_valid"]] if "latest_valid" in entry else []):
            parsed, _ = consumer.read_vintage(root, vintage)
            have.update(point["week_end"] for point in parsed["lists"].get("nyt_hardcover_fiction", []))
        if not have:
            raise ValueError("Cannot derive historical weeks without a validated NYT hardcover-fiction list")
        latest = date.fromisoformat(max(have))
        for offset in range(1, weeks):
            day = (latest - timedelta(weeks=offset)).isoformat()
            if day in have:
                continue
            # Keep requests below five per minute, including when resuming a run.
            time.sleep(13)
            print(f"NYT hardcover fiction {offset + 1}/{weeks}: {day}", flush=True)
            url = f"https://api.nytimes.com/svc/books/v3/lists/{day}/hardcover-fiction.json"
            collect_source("nyt", root, timeout, url=url)
            state = consumer.index(root)["sources"]["nyt"]["last_attempt"]
            if state["status"] != "collected":
                print(f"NYT history stopped: {state['status']}; no retry was attempted", flush=True)
                return
            have.add(day)
        return
    if source == "steam":
        doc = consumer.index(root)
        entry = doc["sources"].get("steam", {})
        if "latest_valid" not in entry:
            collect_source("steam", root, timeout)
            entry = consumer.index(root)["sources"]["steam"]
        if "latest_valid" not in entry:
            raise ValueError("Cannot derive historical weeks without a validated current Steam chart")
        current = consumer.build_all(root)
        have = {point["week_end"] for point in current["lists"]["steam_us_top_sellers"]}
        latest_end = max(have)
        start = date.fromisoformat(latest_end) - timedelta(days=6)
        for offset in range(1, weeks):
            day = start - timedelta(weeks=offset)
            if (day + timedelta(days=6)).isoformat() in have:
                continue
            url = URLS["steam"] + "/" + day.isoformat()
            print(f"Steam history {offset + 1}/{weeks}: {day}", flush=True)
            collect_source("steam", root, timeout, url=url)
        return
    entry = consumer.index(root)["sources"].get("acsi", {})
    discovery = entry.get("last_attempt", {}).get("discovery_file")
    if discovery is None:
        collect_source("acsi", root, timeout)
        discovery = consumer.index(root)["sources"]["acsi"]["last_attempt"].get("discovery_file")
    if discovery is None:
        raise ValueError("No accessible ACSI press-release index")
    from ssa.adapters.consumer import publisher_html
    page = publisher_html(consumer.archive_path(root, discovery).read_bytes())
    urls = sorted({url for url in page.links if re.fullmatch(
        r"https://theacsi.com/news-and-resources/press-releases/202[1-6]/\d{2}/\d{2}/[^/?#]+/", url)})
    if not urls:
        raise ValueError("No 2021-2026 press releases in the official index")
    corpus_path = Path(root) / "corpus/acsi.json"
    corpus = json.loads(corpus_path.read_text()) if corpus_path.exists() else {"format": "ssa.consumer.corpus.v1", "documents": {}}
    for i, url in enumerate(urls, 1):
        if corpus["documents"].get(url, {}).get("status") == "captured":
            continue
        print(f"ACSI release {i}/{len(urls)}: {url.rsplit('/', 2)[-2]}", flush=True)
        try:
            response = requests.get(url, timeout=(min(timeout, 10), timeout), headers={"User-Agent": "SSA-Consumer-Review/1.0"})
            text = publisher_html(response.content)
            valid = (response.status_code == 200 and "No advertising" in " ".join(text.text)
                     and "Just a moment" not in " ".join(text.text))
            artifact = f"corpus/acsi/{consumer.sha256(response.content)}.html"
            consumer.immutable(Path(root) / artifact, response.content)
            corpus["documents"][url] = {"status": "captured" if valid else "invalid_response",
                                        "file": artifact, "sha256": consumer.sha256(response.content),
                                        "http_status": response.status_code, "captured_at": consumer.now(),
                                        "historical_first_print": False}
        except requests.RequestException as error:
            corpus["documents"][url] = {"status": "transport_failure", "error": type(error).__name__, "attempted_at": consumer.now()}
        consumer.write_json(corpus_path, corpus)
    successes = sum(row["status"] == "captured" for row in corpus["documents"].values())
    consumer.record_attempt(root, "acsi", "corpus_only", f"Captured {successes}/{len(urls)} official historical press releases; company score normalization remains partial",
                            corpus_file="corpus/acsi.json", discovery_file=discovery)


def report(bundle, diagnostics):
    lines = ["# Consumer Data Collection Report", "", "Status: pending user and organizer review.", "",
             "Raw bodies and normalized histories are private under `.local/consumer/`.",
             "No official rounds, rights decisions, model calls, or upstream branches were changed.", "",
             "## Source Attempts", "", "| Source | State | Evidence |", "| --- | --- | --- |"]
    for source, state in bundle["sources"].items():
        detail = state["detail"].replace("|", "\\|")
        status = f"HTTP {state['http_status']}; " if "http_status" in state else ""
        lines.append(f"| {source} | {state['status']} | {status}{detail} |")
    lines += ["", "## Scalar Coverage", "", "| Series | Observations | First | Last | Latest value | Signal | Noise |", "| --- | ---: | --- | --- | ---: | ---: | ---: |"]
    for sid, entry in diagnostics["scalar"].items():
        signal = f"{entry['real_movement']:.4f}" if "real_movement" in entry else "insufficient history"
        noise = f"{entry['noise']:.4f}" if "noise" in entry else "insufficient history"
        lines.append(f"| {sid} | {entry['count']} | {entry['first']} | {entry['last']} | {entry['latest_value']:.4f} | {signal} | {noise} |")
    lines += ["", "## Ranking Coverage", ""]
    if not diagnostics["ranking"]:
        lines.append("No validated official ranking history has been acquired yet.")
    for sid, entry in diagnostics["ranking"].items():
        lines.append(f"- {sid}: {entry['count']} lists, {entry['first']} through {entry['last']}.")
    lines += ["", "## Interpretation", "",
              "- Movement uses the last 60 observations; mean absolute change uses at most 36 changes.",
              "- Historical calculations use the captured current vintage, not historical first prints.",
              "- Persistence CRPS is an offline diagnostic using the existing default sd=1.5, not a calibrated comparison between sources.",
              "- These diagnostics do not approve a target or change the Pairwise leaderboard.",
              "- Missing observations and missing sources are not synthesized or treated as zero.", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=consumer.DEFAULT_ARCHIVE)
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("configure-nyt")
    fetch = subs.add_parser("collect")
    fetch.add_argument("--sources", nargs="+", choices=sorted(PARSERS),
                       default=sorted(set(URLS) - PERMISSION_REQUIRED))
    fetch.add_argument("--timeout", type=int, default=20)
    fetch.add_argument("--fetch-sce", action="store_true")
    one = subs.add_parser("fetch")
    one.add_argument("source", choices=sorted(PARSERS))
    one.add_argument("url")
    one.add_argument("--timeout", type=int, default=20)
    history = subs.add_parser("history")
    history.add_argument("source", choices=("steam", "acsi", "nyt"))
    history.add_argument("--weeks", type=int, choices=range(1, 105), default=52)
    history.add_argument("--timeout", type=int, default=20)
    transcribe = subs.add_parser("transcribe-acsi")
    transcribe.add_argument("selection", type=Path)
    imp = subs.add_parser("import")
    imp.add_argument("source", choices=sorted(PARSERS))
    imp.add_argument("file", type=Path)
    imp.add_argument("--url", required=True)
    imp.add_argument("--evidence-file", type=Path)
    imp.add_argument("--published-at")
    build = subs.add_parser("build")
    build.add_argument("--base", type=Path)
    build.add_argument("--output", type=Path)
    subs.add_parser("verify")
    args = parser.parse_args(argv)
    if args.command == "configure-nyt":
        configure_nyt(args.archive)
        return
    requested = (args.sources if args.command == "collect" else
                 [args.source] if args.command in {"fetch", "history"} else [])
    blocked = sorted(set(requested) & PERMISSION_REQUIRED)
    if blocked:
        raise ValueError("Network collection is disabled pending source terms: "
                         + ", ".join(blocked) + "; use authorized manual import")
    if args.command == "collect":
        for source in args.sources:
            print(f"Collecting {source}", flush=True)
            collect_source(source, args.archive, args.timeout, args.fetch_sce)
    elif args.command == "fetch":
        collect_source(args.source, args.archive, args.timeout, url=args.url)
    elif args.command == "history":
        collect_history(args.source, args.archive, args.timeout, args.weeks)
    elif args.command == "transcribe-acsi":
        transcribe_acsi(args.selection, args.archive)
    elif args.command == "import":
        evidence = args.evidence_file
        consumer.ingest(args.source, args.file.read_bytes(), args.url, args.archive,
                        evidence_body=evidence.read_bytes() if evidence else None,
                        evidence_suffix=evidence.suffix if evidence else ".html",
                        published_at=args.published_at)
    bundle = consumer.build_all(args.archive)
    validate(bundle)
    if args.command == "build":
        output = args.output or args.archive / "review.json"
        # Review exports cannot overwrite tracked/public files, even by accident.
        if not output.resolve().is_relative_to((ROOT / ".local").resolve()):
            raise ValueError("Review output must stay inside the repository's ignored .local directory")
        base = json.loads(args.base.read_text()) if args.base else None
        consumer.write_json(output, consumer.append_review(base, bundle) if base is not None else bundle)
        diagnostics = consumer.measurements(bundle)
        consumer.write_json(args.archive / "measurements.json", diagnostics)
        (args.archive / "REPORT.md").write_text(report(bundle, diagnostics))
        print(f"Review export: {output}")
    print(f"Validated {len(bundle['series'])} scalar histories and {len(bundle['lists'])} ranking histories")
    for source, state in bundle["sources"].items():
        print(f"{source}: {state['status']}")


if __name__ == "__main__":
    main()
