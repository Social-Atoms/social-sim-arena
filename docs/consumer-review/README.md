# Consumer Source Review

This is a documentation-only proposal to `dev` for two market-track organizers.
All collection/parser/test details below describe a separate local prototype;
that implementation and its private artifacts are not included in this PR.
Reviewer feedback, source admission and formal task activation remain pending.
See the [evidence summary](EVIDENCE.md) for coverage, observed checks and limits.
The collection commands are prototype reproduction notes, not commands available
on this documentation-only branch. Follow-up implementation will be split by source.

Prepared on 2026-10-21 and extended on 2026-10-22 for local review. This implements the evidence-collection
stage of the 2026-10-17 handoff. Facts, proposals, and open decisions are separated
below. No organizer feedback or source approval is implied.

## Verified Collection

| Source | Acquired data | Remaining collection work |
| --- | --- | --- |
| [NYT](nyt.md) | 52 consecutive Hardcover Fiction top-ten lists, publication labels 2025-11-02 through 2026-10-25 | Historical first-print evidence, edition policy, rights review, and other list selection |
| [Michigan inflation](michigan-inflation.md) | Official final CSV and current preliminary CSV; two horizons through October 2026 preliminary | Historical preliminary vintages and release-calendar evidence |
| [US Mint](us-mint.md) | Two user-downloaded official CSVs, 2026-10-11 and 2026-10-18; 698 matched SKUs and weekly net changes | Longer weekly history, eligible-product selection, limits, and buyer metadata |
| [Steam](steam.md) | 52 consecutive US weekly top-10 revenue lists, starting 2025-10-21 | Retrieval/redistribution review and exact boundary confirmation |
| [Netflix](netflix.md) | 277 US film lists and 277 US TV lists, ending 2026-10-18 | Rights, entity/candidate policy, and original historical vintages |
| [SCE](sce.md) | Nine extra monthly series, June 2013 through September 2026 | Review which indicators have adequate signal and task value |
| [ACSI](acsi.md) | 106 official release pages; 47 benchmark pages; 1,084 verified benchmark cells plus release-prose histories | Remaining historical company extraction, 82 undated benchmark cells, and historical first-print evidence |

The original research priority increases down this table. Engineering readiness
does not change that priority. All seven remain proposed targets for review.

The current totals and per-industry coverage are generated in the local reports
below. Appending the candidate histories preserves every existing site-data
record. ACSI instruments include industry and smaller-company aggregates as well
as company/brand scores; instrument counts are not counts of distinct companies.
Many histories remain sparse, and retrospective first-print status is unverified.

## Local Artifacts

- `.local/consumer/index.json`: latest source states and immutable vintage pointers.
- `.local/consumer/raw/`, `vintages/`, `parsed/`, `attempts/`: raw data, hashes, parser replay, and attempt evidence.
- `.local/consumer/corpus/acsi.json`: historical publisher-page inventory.
- `.local/consumer/corpus/acsi-benchmarks.json`: captured public benchmark tables.
- `.local/consumer/ACSI_REPORT.md`, `acsi-catalog.json`, and `acsi-benchmark-pending.json`: industry coverage, instrument identities, and cells without a unique release date.
- `.local/consumer/normalized.json`: complete acquired candidate histories in the arena's scalar/ranking shapes.
- `.local/consumer/review.json`: a copy of `site/data.json` with candidate histories appended.
- `.local/consumer/measurements.json` and `REPORT.md`: measured coverage, movement, and baseline diagnostics.
- `.local/consumer/NYT_REPORT.md`: NYT date semantics, acquired weekly coverage, and the latest top ten.
- `.local/consumer/MINT_REPORT.md`: Mint paired reports, SKU matching, signed weekly changes, and raw evidence pointers.

These files are deliberately ignored by Git and excluded from Vercel deployment.
The separate local prototype contains parsers and reproduction commands. This
PR contains review documentation, not raw archives or credentials. No paid
model provider was called during collection.

## Prototype Reproduction Notes

Use a virtual environment with the repository's `requests` and `jsonschema`
dependencies. No additional runtime package is required.

```bash
.local/venv/bin/python tools/consumer_data.py verify
.local/venv/bin/python tools/consumer_data.py build --output .local/consumer/normalized.json
.local/venv/bin/python tools/consumer_data.py build --base site/data.json
.local/venv/bin/python tests/test_consumer_data.py
```

Acquisition is explicit and separate from replay:

```bash
.local/venv/bin/python tools/consumer_data.py collect
.local/venv/bin/python tools/consumer_data.py history steam --weeks 52
.local/venv/bin/python tools/consumer_data.py history nyt --weeks 52
.local/venv/bin/python tools/consumer_data.py history acsi
.local/venv/bin/python tools/consumer_data.py transcribe-acsi .local/consumer/acsi-selection.json
.local/venv/bin/python tools/consumer_data.py import mint /path/to/official-report.csv --url https://www.usmint.gov/about/production-sales-figures/cumulative-sales --evidence-file /path/to/official-report.csv
.local/venv/bin/python tools/acsi_benchmarks.py collect-releases
.local/venv/bin/python tools/acsi_benchmarks.py collect
.local/venv/bin/python tools/acsi_benchmarks.py build
```

Additional manually checked release selections use the same transcription command,
for example `transcribe-acsi .local/consumer/acsi-automobile-release-selection.json`.
Selections retain exact publisher quotations, explicit entity aliases, and review
notes. No value is reconstructed from a percentage change or a missing year.

On this Mac, Python's bundled certificate store failed for several publishers;
the system CA bundle worked with normal HTTPS verification. For those commands,
set `REQUESTS_CA_BUNDLE=/etc/ssl/cert.pem`. TLS verification was never disabled.
The ACSI selection file is a local, manually checked sample, not a complete
source dataset or a synthetic fixture.

## Review Records

| Source | Organizer 1 | Organizer 2 |
| --- | --- | --- |
| NYT | Pending | Pending |
| Michigan inflation | Pending | Pending |
| US Mint | Pending | Pending |
| Steam | Pending | Pending |
| Netflix | Pending | Pending |
| SCE extensions | Pending | Pending |
| ACSI | Pending | Pending |

Reviewers should independently record proceed / return for evidence / defer,
with reasons. See [decisions](DECISIONS.md), [operations](OPERATIONS.md), and
[document impact](IMPACT.md). Source-level approval precedes live adapters,
sandbox round designs, and a separate formal activation change.
