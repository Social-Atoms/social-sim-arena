# Consumer Data Review Snapshot

This directory contains actual data for all seven consumer/market sources:
NYT, Michigan, U.S. Mint, Steam, Netflix, New York Fed SCE and ACSI.
Source admission remains pending.
It complements the seven-source evaluation in [PR #227](https://github.com/Social-Atoms/social-sim-arena/pull/227).

- `normalized.json`: 2,099 scalar histories with 6,804 `{date, value}` observations,
  plus four ranking histories with 658 `{week_end, items}` lists.
- `schema.json`: the JSON schema for the normalized review bundle.
- `provenance.json`: original publisher URLs, capture times, hashes and raw-file mappings.
- `raw/`: original CSV/XLSX/JSON/TSV/HTML artifacts. New publisher artifacts use
  gzip compression; their hashes refer to the original decompressed bytes.
- `evidence/` and `corpus-index.json`: ACSI original evidence and publisher-page mappings.
- `manifest.json`: coverage totals and the user's public-redistribution confirmation.

Michigan has two inflation horizons through October 2026 preliminary. Finals
end in September, and sparse long-horizon months are not filled with zeros.
SCE has nine monthly indicators, 160 values each, June 2013-September 2026.
Mint has 698 matched SKUs at 2026-10-11 and 2026-10-18: 1,396 cumulative values
and 698 weekly changes, including 12 negative and 555 zero changes. Cumulative
Mint totals are diagnostics; each SKU currently has only one weekly change.

NYT contains 52 Hardcover Fiction top-ten lists, publication labels 2025-11-02
through 2026-10-25. Netflix contains 277 US film and 277 US TV lists, ending
2026-10-18. Steam contains 52 US weekly revenue lists, with week-end labels
2025-10-27 through 2026-10-19. ACSI contains 692 company/brand/aggregate instruments
and 2,203 score observations; instruments are not counts of distinct companies.
The ACSI publisher corpus and original selection evidence are included as gzip files.

Sources: [Michigan](https://www.sca.isr.umich.edu/),
[Federal Reserve Bank of New York](https://www.newyorkfed.org/microeconomics/sce),
[United States Mint](https://www.usmint.gov/about/production-sales-figures/cumulative-sales),
[NYT Books](https://developer.nytimes.com/),
[Steam](https://store.steampowered.com/charts/topsellers/US),
[Netflix](https://www.netflix.com/tudum/top10/data/all-weeks-countries.tsv),
and [ACSI](https://theacsi.com/news-and-resources/press-releases/).
Mint permits republication with its attribution; SCE's original workbook contains
its data license. Existing repository approval covers Michigan's national tables.
The SCE workbook includes original publisher/license information and is not
relicensed by this snapshot.

The requesting user confirmed public redistribution permission for NYT, Netflix,
Steam and ACSI on 2026-10-22. This confirmation is recorded in the manifest;
formal source admission remains a separate organizer decision. These captures are current
vintages, not proof of historical first prints or a leakage-free backtest.
No active task, resolution, leaderboard or production site-data file is changed.

Provenance IDs in the bundle resolve through the keys in `provenance.json`;
the corresponding raw-file paths are relative to this directory. All values are
numeric observations or ordered publisher item identifiers; descriptions remain metadata.
For compressed files, decompress before checking the original SHA256. Original
ACSI corpus manifests retain their archive-relative paths; `corpus-index.json`
maps those paths to the included compressed publisher pages.
