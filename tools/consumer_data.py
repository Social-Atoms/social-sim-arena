"""Collect, validate, replay, and append consumer data for local review only.

  python tools/consumer_data.py collect
  python tools/consumer_data.py build --base site/data.json
  python tools/consumer_data.py verify
  python tools/consumer_data.py import mint /path/report.csv --url URL --evidence-file /path/report.csv

Sources: michigan_finals, michigan_current, sce, mint. Collectors for NYT,
Netflix, Steam and ACSI were removed while their terms are unresolved.
No refresh, paid provider call, publication, or git command is run here.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import jsonschema
import requests

from ssa import consumer
from ssa.adapters.consumer import PARSERS

URLS = {
    "michigan_finals": "https://www.sca.isr.umich.edu/files/tbmpx1px5.csv",
    "michigan_current": "https://www.sca.isr.umich.edu/files/tbcpx1px5.csv",
    "mint": "https://www.usmint.gov/about/production-sales-figures/cumulative-sales",
    "sce": "https://www.newyorkfed.org/medialibrary/interactives/sce/sce/downloads/data/frbny-sce-data.xlsx",
}
# Mint answers automated requests with 403, so it is imported by hand.
AUTOMATED = {"michigan_finals", "michigan_current", "sce"}


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
    try:
        response = requests.get(url, timeout=(min(timeout, 10), timeout),
                                headers={"User-Agent": "SSA-Consumer-Review/1.0"})
    except requests.RequestException as error:
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
        consumer.ingest(source, response.content, url, root)
    except (ValueError, KeyError, RuntimeError) as error:
        consumer.record_attempt(root, source, "parse_failure", str(error)[:300], **evidence)


def validate(bundle):
    schema = json.loads((ROOT / "schema/consumer-review.schema.json").read_text())
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(bundle)


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
    fetch = subs.add_parser("collect")
    fetch.add_argument("--sources", nargs="+", choices=sorted(PARSERS), default=sorted(URLS))
    fetch.add_argument("--timeout", type=int, default=20)
    fetch.add_argument("--fetch-sce", action="store_true")
    one = subs.add_parser("fetch")
    one.add_argument("source", choices=sorted(PARSERS))
    one.add_argument("url")
    one.add_argument("--timeout", type=int, default=20)
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
    if args.command == "collect":
        for source in args.sources:
            print(f"Collecting {source}", flush=True)
            collect_source(source, args.archive, args.timeout, args.fetch_sce)
    elif args.command == "fetch":
        collect_source(args.source, args.archive, args.timeout, url=args.url)
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
